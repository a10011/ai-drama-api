"""智能体：服化道指导 — 逐镜头服装/道具/化妆方案 + 连续性追踪"""
import json
import time
import logging
from typing import Dict, List, Optional
from .agent_base_legacy import BaseAgent, AgentResult
from services.film_refs import get_genre_film_ref

logger = logging.getLogger(__name__)

WARDROBE_PROMPT = """你是一位影视服化道指导（Wardrobe & Props Supervisor）。你的任务：为短剧每个镜头决定服装、道具、化妆方案，并确保镜头间的连续性。

# 服装规则

## 基本原则
1. 同一场景内角色服装不变（除非剧情有换装）
2. 跨越时间（第二天/数年后）→ 服装更换
3. 场景类型决定服装类型：居家便装 / 外出正装 / 战斗戎装 / 宴会华服

## 特殊场景处理
- **沐浴/洗澡**：角色脱去外衣，仅着内衬/浴衣/单薄衣袍。绝对不能全裸，始终保留内层衣物
- **睡觉**：换寝衣/睡衣
- **受伤**：衣服可能有破损、血迹，后续镜头保持破损直到换装
- **落水**：衣服湿透贴身的视觉效果
- **劳作**：挽袖、简装、耐脏颜色

# 道具规则

## 道具与动作匹配
- 打斗：武器（剑/刀/鞭/暗器）按角色设定
- 吃饭：餐具、食物
- 写信：笔墨纸砚（古代）/ 笔纸（现代）
- 施法：法器、符纸、法杖
- 行走：包袱、行囊、马/车

## 道具连续性
- 角色获得的道具在后面镜头中继续持有，直到剧情中丢弃/消耗
- 武器在非战斗镜头中可佩在腰间/背上

# 化妆规则

## 年龄变迁
- 剧本跨度数年 → 角色逐渐变老：青年(无妆) → 中年(细纹+灰发) → 老年(深纹+白发)
- 年龄变化是渐进的，不是跳变
- 回忆/闪回中的角色可能更年轻

## 易容/伪装
- 角色刻意改变容貌以隐藏身份
- 贴假胡子、改变发型、改变肤色、面部伪装
- 易容前后镜头必须标注

## 受伤
- 受伤后伤妆必须持续：擦伤→结痂→疤痕（逐步演化）
- 打斗后：淤青、血痕、衣服破损
- 重伤：绷带、血迹、行动姿态改变
- 伤妆在后续镜头中保持，直到剧情显示治疗/愈合

## 其他化妆
- 病容：面色苍白/蜡黄、唇色淡
- 醉酒：面色潮红、眼神迷离
- 哭泣：泪痕、眼红

# 输出格式

对每个镜头，输出该镜头中每个角色的：
- outfit: 穿什么（描述服装，不是角色名）
- props: 拿什么（描述道具，不是角色名）
- makeup: 化妆需求（年龄/伤妆/易容/状态）
- char_age: 角色在这个镜头中的年龄段（幼年/少年/青年/中年/老年）

返回纯JSON（无markdown代码块）：
{
  "characters": {
    "角色名1": {
      "base_age": "青年",
      "wardrobe_options": ["居家便装", "外出正装", "战斗戎装"],
      "weapon": "佩剑"
    }
  },
  "shots": [
    {
      "shot_num": 1,
      "outfit": {
        "角色名1": "青衫长袍，束发簪冠，腰间玉佩",
        "角色名2": "白衣素裙，盘发银钗，轻纱披肩"
      },
      "props": {
        "角色名1": "手持折扇",
        "角色名2": "端茶盏"
      },
      "makeup": {
        "角色名1": "青年常态妆",
        "角色名2": "淡妆，唇红齿白"
      },
      "char_ages": {
        "角色名1": "青年",
        "角色名2": "少女"
      },
      "notes": "室内场景，宅院日常装束"
    }
  ],
  "continuity_notes": "服装变化时间线、道具去向、伤妆演变、年龄推进说明",
  "special_scenes": ["shot 5: 沐浴→脱外衣仅内衬", "shot 8: 受伤→右臂刀伤+血迹"]
}
"""


class WardrobeAgent(BaseAgent):
    """服化道指导：逐镜头服装/道具/化妆方案 + 连续性追踪"""

    name = "服化道指导"
    description = "设计逐镜头的服装、道具、化妆方案，处理易容/年龄/受伤/特殊场景，追踪镜头间连续性"
    version = "1.0.0"

    def design(self, shots: List[dict] = None, characters: List[dict] = None,
               script: str = "", genre: str = "", **kwargs) -> AgentResult:
        shots = shots or []
        characters = characters or []
        """为分镜设计服化道方案"""
        start = time.time()
        try:
            if not shots:
                return AgentResult(success=False, error="无分镜数据", duration_ms=0)
            if not characters:
                return AgentResult(success=False, error="无角色数据", duration_ms=0)

            # 角色信息
            chars_info = {}
            for c in characters:
                name = c.get("name", c.get("char_name", "")) if isinstance(c, dict) else str(c)
                desc = c.get("description", c.get("appearance", "")) if isinstance(c, dict) else ""
                gender = c.get("gender", c.get("sex", "")) if isinstance(c, dict) else ""
                age = c.get("age", "") if isinstance(c, dict) else ""
                chars_info[name] = {
                    "gender": gender,
                    "age": age,
                    "description": desc[:150] if desc else ""
                }

            chars_json = json.dumps(chars_info, ensure_ascii=False, indent=2)

            # 场景检测
            has_injury = False
            has_bath = False
            has_disguise = False
            has_aging = False
            has_fight = False
            for s in shots:
                desc = s.get("description", s.get("desc", ""))
                if any(w in desc for w in ["受伤", "流血", "打伤", "中箭", "中刀", "砍"]):
                    has_injury = True
                if any(w in desc for w in ["洗澡", "沐浴", "洗浴", "入浴", "浴"]):
                    has_bath = True
                if any(w in desc for w in ["易容", "伪装", "乔装", "假扮", "化装"]):
                    has_disguise = True
                if any(w in desc for w in ["年", "岁", "老", "少年", "鬓白"]):
                    has_aging = True
                if any(w in desc for w in ["打", "战", "攻", "杀", "击", "拳", "剑", "刀"]):
                    has_fight = True

            # 构建镜头数据
            shots_info = []
            for s in shots:
                info = {
                    "shot_num": s.get("shot_num", s.get("id", "")),
                    "description": s.get("description", s.get("desc", "")),
                    "dialogue": s.get("dialogue", ""),
                    "emotion": s.get("emotion", ""),
                    "importance": s.get("importance", "medium"),
                }
                shots_info.append(info)

            shots_json = json.dumps(shots_info, ensure_ascii=False, indent=2)

            flags = []
            if has_injury: flags.append("⚠️ 检测到受伤场景 → 伤妆需持续演变")
            if has_bath: flags.append("⚠️ 检测到沐浴场景 → 脱外衣仅内衬，绝不裸体")
            if has_disguise: flags.append("⚠️ 检测到易容场景 → 面容改变需标注")
            if has_aging: flags.append("⚠️ 检测到年龄跨度 → 年龄渐进变化")
            if has_fight: flags.append("⚠️ 检测到打斗场景 → 武器+盔甲+受伤可能")

            flags_str = "\n".join(flags) if flags else "无特殊场景"

            genre_str = f"题材：{genre}" if genre else ""

            script_text = kwargs.get("script_text", "") or script or ""
            script_ctx = "\n\n【剧情上下文】（服化道贴合角色身份处境：落难朴素/战场战损/显贵华服）：\n" + script_text[:600] if script_text else ""
            film_ref = get_genre_film_ref(genre, aspect="wardrobe")
            user_prompt = f"""{film_ref}{genre_str}
{flags_str}{script_ctx}

角色信息：
{chars_json}

分镜数据：
{shots_json}

请逐镜头为每个角色配置服装、道具、化妆。特别注意：
1. 沐浴场景：脱外衣仅着内衬/浴衣，严禁全裸
2. 受伤场景：伤妆必须标明，后续镜头保持
3. 易容场景：明确标注面容变化
4. 年龄跨度：年龄渐进标注
5. 道具跟随动作，获得后继续持有"""

            result = self._call_llm_json(WARDROBE_PROMPT, user_prompt, retries=1)

            if isinstance(result, dict):
                wd_shots = result.get("shots", [])
                continuity = result.get("continuity_notes", "")
                special_scenes = result.get("special_scenes", [])

                # 合并回原始 shots
                merged_shots = []
                for i, s in enumerate(shots):
                    s_copy = dict(s)
                    if i < len(wd_shots):
                        wd = wd_shots[i]
                        s_copy["outfit"] = wd.get("outfit", s_copy.get("outfit", {}))
                        s_copy["props"] = wd.get("props", s_copy.get("props", {}))
                        s_copy["makeup"] = wd.get("makeup", s_copy.get("makeup", {}))
                        s_copy["char_ages"] = wd.get("char_ages", s_copy.get("char_ages", {}))
                        s_copy["wardrobe_notes"] = wd.get("notes", "")
                    merged_shots.append(s_copy)

                elapsed = int((time.time() - start) * 1000)
                logger.info(f"[Wardrobe] Designed for {len(merged_shots)} shots in {elapsed}ms, "
                           f"injury={has_injury} bath={has_bath} disguise={has_disguise} aging={has_aging}")
                return AgentResult(
                    success=True,
                    data={
                        "shots": merged_shots,
                        "continuity_notes": continuity,
                        "special_scenes": special_scenes,
                        "flags": {
                            "has_injury": has_injury,
                            "has_bath": has_bath,
                            "has_disguise": has_disguise,
                            "has_aging": has_aging,
                            "has_fight": has_fight
                        }
                    },
                    duration_ms=elapsed
                )

            return AgentResult(success=False, error="服化道方案生成失败", duration_ms=0)

        except Exception as e:
            logger.error(f"[Wardrobe] failed: {e}")
            return AgentResult(success=False, error=str(e),
                               duration_ms=int((time.time() - start) * 1000))

    def run(self, action: str = "design", **kwargs) -> AgentResult:
        return self.execute(action, **kwargs)

    def execute(self, action: str = "design", **kwargs) -> AgentResult:
        """统一入口"""
        action_map = {
            "design": self.design,
        }
        if action in action_map:
            return action_map[action](**kwargs)
        if "shots" in kwargs:
            return self.design(**kwargs)
        return AgentResult(success=False, error=f"未知 action: {action}")
