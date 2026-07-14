"""
服化道工具集 (P1)
3个工具: outfit_matcher, props_assigner, continuity_checker
"""
import json
from tools.base import AgentTool, ToolResult


class OutfitMatcher(AgentTool):
    """根据场景类型为角色匹配服装"""
    name = "outfit_matcher"
    description = "根据场景类型和角色身份，匹配最合适的服装方案"
    category = "wardrobe"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("scene_type"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "scene_type": {"type": "string", "description": "场景类型"},
                "char_gender": {"type": "string", "description": "角色性别"},
                "char_role": {"type": "string", "description": "角色身份"},
                "is_special": {"type": "string", "description": "特殊场景标签"}
            }
        }

    async def execute(self, scene_type: str = "", char_gender: str = "",
                      char_role: str = "", is_special: str = "", **kwargs) -> ToolResult:
        # 特殊场景优先
        special_outfits = {
            "沐浴": "脱去外衣，仅着素白内衬/浴衣，发髻微散",
            "洗澡": "脱去外衣，仅着素白内衬/浴衣，发髻微散",
            "睡觉": "素色寝衣/睡衣，散发",
            "受伤": "衣服保留破损和血迹","落水": "衣服湿透贴身",
            "劳作": "简装挽袖，深色耐脏",
        }
        if is_special in special_outfits:
            return self._ok({"outfit": special_outfits[is_special], "type": is_special}, 90)

        # 身份→服装
        role_outfits = {
            "侠客": {"日常": "青衫劲装，束袖绑腿", "战斗": "深色戎装，护甲", "宴会": "锦袍正装"},
            "公主": {"日常": "华服宫装，金冠珠钗", "战斗": "轻甲劲装（特殊）", "宴会": "凤冠霞帔"},
            "书生": {"日常": "素色长衫，方巾", "外出": "青布直裰，头巾", "宴会": "锦缎长袍"},
            "将军": {"日常": "便装武服", "战斗": "明光铠，披风", "宴会": "官服朝服"},
            "道士": {"日常": "道袍拂尘", "战斗": "法衣宝剑", "做法": "鹤氅法冠"},
            "商人": {"日常": "绸缎长衫", "外出": "布衣低调", "宴会": "华丽锦袍"},
        }

        # 场景类型
        scene_outfits = {
            "日常": "日常便装", "居家": "居家常服", "外出": "外出正装",
            "战斗": "戎装战甲", "打斗": "劲装轻甲", "宴会": "华服盛装",
            "宫廷": "官服朝服", "劳作": "简装便服",
        }

        outfit = "日常便装"
        for rk, rv in role_outfits.items():
            if rk in char_role or rk in scene_type:
                for sk, sv in rv.items():
                    if sk in scene_type or sk == "日常":
                        outfit = sv
                        break
                break

        if outfit == "日常便装":
            for sk, sv in scene_outfits.items():
                if sk in scene_type:
                    outfit = sv
                    break

        return self._ok({"outfit": outfit, "type": scene_type}, 80)


class PropsAssigner(AgentTool):
    """根据动作和场景匹配道具"""
    name = "props_assigner"
    description = "根据角色动作和场景，匹配合适的道具"
    category = "wardrobe"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("scene_desc"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "scene_desc": {"type": "string", "description": "场景描述"},
                "char_role": {"type": "string", "description": "角色身份"}
            }
        }

    async def execute(self, scene_desc: str = "", char_role: str = "", **kwargs) -> ToolResult:
        action_props = {
            "打": "兵器", "战": "兵器", "杀": "兵器",
            "吃": "餐具", "喝": "茶具", "饮": "酒具",
            "写": "笔墨纸砚", "读": "书卷",
            "施法": "法器", "做法": "符纸法器",
            "走": "行囊", "骑": "马/缰绳",
            "弹": "乐器", "奏": "乐器",
            "舞": "剑/扇/绸带",
        }

        weapons = {
            "侠客": "佩剑", "将军": "长刀", "道士": "拂尘/法剑",
            "刺客": "匕首/暗器", "弓手": "弓箭", "法师": "法杖",
        }

        props = []
        for k, v in action_props.items():
            if k in scene_desc:
                props.append(v)

        if "兵器" in str(props):
            for rk, rv in weapons.items():
                if rk in char_role:
                    props = [rv if p == "兵器" else p for p in props]
                    break

        if not props:
            props = ["无特殊道具"]

        return self._ok({"props": list(set(props))}, 80)


class ContinuityChecker(AgentTool):
    """检查服化道连续性"""
    name = "continuity_checker"
    description = "检查镜头间的服装、道具、化妆连续性，标注变化点"
    category = "wardrobe"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("shots"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "shots": {"type": "string", "description": "分镜数据(JSON)"}
            }
        }

    async def execute(self, shots: str = "", **kwargs) -> ToolResult:
        try:
            if isinstance(shots, str):
                data = json.loads(shots)
            else:
                data = shots
            shot_list = data if isinstance(data, list) else data.get("shots", [])

            changes = []
            prev_outfits = {}
            prev_makeup = {}

            for i, s in enumerate(shot_list):
                outfit = s.get("outfit", {})
                makeup = s.get("makeup", {})
                desc = s.get("description", s.get("desc", ""))

                for char, o in outfit.items():
                    if char in prev_outfits and prev_outfits[char] != o:
                        changes.append(f"Shot {i+1}: {char}换装 {prev_outfits[char]}→{o}")
                    prev_outfits[char] = o

                for char, m in makeup.items():
                    if char in prev_makeup and prev_makeup[char] != m:
                        changes.append(f"Shot {i+1}: {char}妆容变化 {prev_makeup[char]}→{m}")
                    prev_makeup[char] = m

                special_words = ["沐浴","洗澡","受伤","落水","易容","伪装"]
                for w in special_words:
                    if w in desc:
                        changes.append(f"Shot {i+1}: ⚠️ 特殊场景-{w}，需特殊妆造处理")

            return self._ok({
                "continuity_changes": changes,
                "change_count": len(changes),
                "ok": len(changes) <= len(shot_list) * 2  # 合理范围
            }, 85)

        except Exception as e:
            return self._fail(str(e))
