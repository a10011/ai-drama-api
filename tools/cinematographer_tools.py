"""
摄影指导工具集 (P1)
3个工具: shot_camera_guide, lighting_planner, lens_selector
"""
import json
from tools.base import AgentTool, ToolResult


class ShotCameraGuide(AgentTool):
    """根据镜头内容推荐运镜+角度+景别组合"""
    name = "shot_camera_guide"
    description = "分析镜头内容和情绪，推荐最佳运镜/角度/景别组合"
    category = "cinematographer"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("content"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "content": {"type": "string", "description": "镜头描述"},
                "emotion": {"type": "string", "description": "情绪"},
                "importance": {"type": "string", "description": "重要性 high/medium/low"}
            }
        }

    async def execute(self, content: str, emotion: str = "", importance: str = "medium", **kwargs) -> ToolResult:
        # 运镜映射
        movement_map = {
            "对话": "固定", "交流": "固定", "聊天": "固定", "交谈": "固定",
            "爆发": "推", "高潮": "推", "揭示": "推", "真相": "推", "关键": "推",
            "离别": "拉", "远去": "拉", "离开": "拉", "告别": "拉", "消失": "拉",
            "追": "跟", "跑": "跟", "走": "跟", "行": "跟",
            "打斗": "摇", "战斗": "摇", "攻击": "摇", "出手": "摇",
            "开场": "升降", "进入": "升降", "新场景": "升降",
            "对峙": "环绕", "对立": "环绕", "角力": "环绕",
        }
        # 景别映射
        shot_map = {
            "对话": "中景", "交流": "中景",
            "爆发": "近景", "高潮": "特写", "揭示": "近景",
            "离别": "全景", "远去": "远景",
            "打斗": "全景", "追": "中景",
            "开场": "远景", "进入": "全景",
            "对峙": "中景",
        }
        # 角度映射
        angle_map = {
            "悲伤": "俯视", "孤独": "俯视", "渺小": "俯视",
            "威严": "仰视", "巨大": "仰视", "力量": "仰视", "危险": "仰视",
            "混乱": "倾斜", "不安": "倾斜", "失衡": "倾斜",
        }

        mov = "固定"
        for k, v in movement_map.items():
            if k in content:
                mov = v
                break
        if importance == "high" and mov == "固定":
            mov = "推"

        sht = "中景"
        for k, v in shot_map.items():
            if k in content:
                sht = v
                break

        ang = "平视"
        for k, v in angle_map.items():
            if k in emotion:
                ang = v
                break

        # LLM 微调
        try:
            prompt = f"""镜头描述：{content[:300]}
情绪：{emotion}
启发式推荐：运镜={mov} 景别={sht} 角度={ang}

请根据描述微调，返回JSON: {{"camera_movement":"...","shot_type":"...","camera_angle":"...","reason":"..."}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            data = json.loads(raw.strip().split("```json")[-1].split("```")[0].strip().strip("`"))
            return self._ok(data, 85)
        except Exception:
            return self._ok({
                "camera_movement": mov, "shot_type": sht,
                "camera_angle": ang, "reason": "启发式匹配"
            }, 70)


class LightingPlanner(AgentTool):
    """根据场景氛围推荐灯光方案"""
    name = "lighting_planner"
    description = "根据场景氛围和情绪推荐灯光方案"
    category = "cinematographer"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("scene_desc"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "scene_desc": {"type": "string", "description": "场景描述"},
                "emotion": {"type": "string", "description": "情绪"},
                "time_of_day": {"type": "string", "description": "时间（白天/夜晚/黄昏）"}
            }
        }

    async def execute(self, scene_desc: str, emotion: str = "", time_of_day: str = "", **kwargs) -> ToolResult:
        lighting_map = {
            "温馨": "暖光、柔光", "浪漫": "暖光、柔焦、侧逆光",
            "紧张": "硬光、高对比、暗角", "悬疑": "暗调、蓝冷光、阴影",
            "悲伤": "柔光、低照度、灰色调", "孤独": "逆光剪影、低饱和度",
            "愤怒": "冷硬光、高反差", "恐惧": "底光、暗调、摇曳光",
            "神圣": "逆光、光晕、金色", "回忆": "柔光、暖黄、轻微过曝",
        }
        lighting = "自然柔光"
        for k, v in lighting_map.items():
            if k in emotion or k in scene_desc:
                lighting = v
                break

        time_adj = {"夜晚": "+暗调", "黄昏": "+暖橙", "清晨": "+冷蓝", "室内": "+人工光源"}
        for k, v in time_adj.items():
            if k in time_of_day:
                lighting += v

        return self._ok({"lighting": lighting, "light_type": lighting.split("、")[0]}, 80)


class LensSelector(AgentTool):
    """根据需求选择焦段"""
    name = "lens_selector"
    description = "根据镜头需求选择最佳焦段"
    category = "cinematographer"

    async def execute(self, shot_type: str = "中景", focus: str = "人物", **kwargs) -> ToolResult:
        lens_map = {
            "远景": {"lens": "16-24mm", "reason": "广角展示空间"},
            "全景": {"lens": "24-35mm", "reason": "环境+人物关系"},
            "中景": {"lens": "35-50mm", "reason": "自然视角叙事"},
            "近景": {"lens": "50-85mm", "reason": "压缩空间聚焦人物"},
            "特写": {"lens": "85-135mm", "reason": "极致压缩突出细节"},
            "大特写": {"lens": "100-200mm", "reason": "微距级细节"},
        }
        result = lens_map.get(shot_type, {"lens": "50mm", "reason": "标准镜头"})
        return self._ok(result, 80)
