"""
特效师工具集 (P1)
3个工具: sfx_matcher, transition_designer, color_grader
"""
import json
from tools.base import AgentTool, ToolResult


class SFXMatcher(AgentTool):
    """根据场景内容匹配特效"""
    name = "sfx_matcher"
    description = "根据场景内容和类型，匹配合适的动作/氛围特效"
    category = "sfx"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("scene_desc"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "scene_desc": {"type": "string", "description": "场景描述"},
                "genre": {"type": "string", "description": "题材类型"}
            }
        }

    async def execute(self, scene_desc: str, genre: str = "", **kwargs) -> ToolResult:
        action_fx = {
            "剑": ["刀光剑影", "剑气弧光"], "刀": ["刀光弧线", "金属撞击火花"],
            "拳": ["速度线", "冲击波"], "掌": ["掌风气浪", "内力波动"],
            "枪": ["枪口火焰", "弹道轨迹"], "箭": ["箭矢破空", "箭羽残影"],
        }
        magic_fx = {
            "法": ["灵气环绕", "符文化形", "元素光效"],
            "仙": ["仙气飘渺", "光柱冲天", "花瓣飘落"],
            "魔": ["黑气蔓延", "血色光晕", "暗影波动"],
            "妖": ["妖气缭绕", "异色瞳孔", "变形残影"],
        }
        atmo_fx = {
            "雨": ["雨滴粒子", "地面水花"], "雪": ["雪花飘落", "积雪雾化"],
            "风": ["衣袂飘飞", "沙尘粒子"], "夜": ["月光辉光", "灯笼光晕"],
            "雾": ["薄雾层", "体积光"], "火": ["火星粒子", "热浪扭曲"],
        }

        result = {"action_effects": [], "atmosphere_effects": [], "intensity": 3, "reason": ""}

        for k, v in action_fx.items():
            if k in scene_desc:
                result["action_effects"].extend(v)
                result["intensity"] = max(result["intensity"], 6)
        for k, v in magic_fx.items():
            if k in scene_desc:
                result["action_effects"].extend(v)
                result["intensity"] = max(result["intensity"], 7)
        for k, v in atmo_fx.items():
            if k in scene_desc:
                result["atmosphere_effects"].extend(v)
                result["intensity"] = max(result["intensity"], 4)

        if result["action_effects"]:
            result["reason"] = f"检测到动作关键词，匹配特效: {', '.join(result['action_effects'][:3])}"
        elif result["atmosphere_effects"]:
            result["reason"] = f"检测到氛围关键词，匹配特效: {', '.join(result['atmosphere_effects'][:3])}"
        else:
            result["reason"] = "纯叙事镜头，无需特效"

        return self._ok(result, 85 if result["action_effects"] else 70)


class TransitionDesigner(AgentTool):
    """根据镜头关系设计转场"""
    name = "transition_designer"
    description = "根据前后镜头关系和情绪，设计最佳转场方式"
    category = "sfx"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("current_emotion"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "current_emotion": {"type": "string", "description": "当前镜头情绪"},
                "next_emotion": {"type": "string", "description": "下一镜头情绪"},
                "time_jump": {"type": "string", "description": "是否有时间跳跃"}
            }
        }

    async def execute(self, current_emotion: str = "", next_emotion: str = "",
                      time_jump: str = "", **kwargs) -> ToolResult:
        if time_jump and time_jump not in ("无", "没有", ""):
            transition = "叠化" if "长" not in time_jump else "淡出淡入"
            reason = f"时间跳跃{time_jump}，用{transition}过渡"
        elif current_emotion == next_emotion:
            transition = "切入"
            reason = "情绪连续，直接切入"
        elif "爆发" in current_emotion or "高潮" in current_emotion:
            transition = "闪白"
            reason = "情绪爆发后用闪白过渡"
        elif "悲伤" in current_emotion or "结束" in current_emotion:
            transition = "淡出"
            reason = "悲伤/结束场景淡出"
        elif "回忆" in next_emotion:
            transition = "叠化"
            reason = "进入回忆用叠化过渡"
        else:
            transition = "切入"
            reason = "标准转场"

        return self._ok({"transition": transition, "reason": reason}, 80)


class ColorGrader(AgentTool):
    """根据场景推荐调色方案"""
    name = "color_grader"
    description = "根据场景氛围和题材推荐调色方案"
    category = "sfx"

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "scene_desc": {"type": "string", "description": "场景描述"},
                "emotion": {"type": "string", "description": "情绪"},
                "genre": {"type": "string", "description": "题材"}
            }
        }

    async def execute(self, scene_desc: str = "", emotion: str = "", genre: str = "", **kwargs) -> ToolResult:
        genre_grades = {
            "古风": "暖金、低饱和古韵", "仙侠": "高饱和青蓝、金色光晕",
            "武侠": "低饱和黄沙、暗绿", "宫廷": "红金浓墨、高对比",
            "现代": "青橙(Teal & Orange)", "悬疑": "冷蓝暗调、高反差",
            "都市": "中性色、冷蓝白", "玄幻": "高饱和奇幻色、紫蓝渐变",
        }
        emotion_grades = {
            "温馨": "+暖黄", "悲伤": "+淡蓝灰", "紧张": "+暗调高对比",
            "浪漫": "+柔粉", "恐惧": "+深蓝黑", "回忆": "+褪色暖黄",
        }

        grade = genre_grades.get(genre, "自然色")
        for k, v in emotion_grades.items():
            if k in emotion or k in scene_desc:
                grade += v
                break

        return self._ok({"color_grade": grade, "base": genre_grades.get(genre, "自然色")}, 80)
