"""
场景生成专用工具 — 确保场景与剧本上下文一致
"""
import logging
from tools.base import AgentTool, ToolResult

logger = logging.getLogger("tools.scene")


class SceneAtmospherePrompt(AgentTool):
    name = "scene_atmosphere_prompt"
    description = "根据场景描述和情感基调生成最优化的生图prompt"
    category = "scene"

    async def execute(self, scene_description: str = "", mood: str = "", time_of_day: str = "", location_type: str = "") -> ToolResult:
        if not scene_description:
            return self._fail("缺少场景描述")

        mood_colors = {
            "悲伤": "cool tones, blue-gray palette, soft dim light",
            "欢乐": "warm golden light, vibrant colors, sunny atmosphere",
            "紧张": "high contrast, harsh shadows, cool blue neon",
            "浪漫": "soft warm light, pink-amber palette, bokeh effect",
            "平静": "natural soft light, muted earth tones, serene atmosphere",
        }
        color = mood_colors.get(mood, "natural balanced lighting")

        time_map = {
            "白天": "bright daylight, sun rays",
            "夜晚": "moody night scene, warm artificial light",
            "黄昏": "golden hour, dramatic sunset backlighting",
            "黎明": "soft dawn light, misty morning atmosphere",
        }
        time_desc = time_map.get(time_of_day, "natural lighting")

        comp_map = {
            "悲伤": "孤独感构图，留白空间", "欢乐": "饱满构图，人物互动",
            "紧张": "倾斜构图，前景遮挡", "浪漫": "对称构图，双人特写",
        }
        composition = comp_map.get(mood, "balanced composition")

        prompt = (
            f"Cinematic wide shot of {scene_description}. "
            f"Time: {time_desc}. Mood: {color}. "
            f"Composition: {composition}. "
            f"Photorealistic, 8K, professional cinematography. "
            f"NOT cartoon, NOT anime, NOT 3D rendered."
        )
        return self._ok({"prompt": prompt, "mood": mood, "composition": composition}, 85)


class SceneConsistencyCheck(AgentTool):
    name = "scene_consistency_check"
    description = "检查场景与剧本的情感/逻辑一致性"
    category = "scene"

    async def execute(self, scene_description: str = "", script_context: str = "", expected_mood: str = "") -> ToolResult:
        if not scene_description or not script_context:
            return self._fail("缺少数据")

        issues = []
        entities = ["门", "窗", "桌", "椅", "灯", "路", "车", "树", "山", "水", "楼",
                     "房间", "床", "手机", "电脑", "沙发"]
        script_entities = [e for e in entities if e in script_context]
        missing = [e for e in script_entities if e not in scene_description]
        if len(missing) > 2:
            issues.append(f"场景缺少剧本关键元素: {missing[:3]}")

        mood_kw = {
            "悲伤": ["哭", "泪", "暗", "灰", "冷", "孤单"],
            "欢乐": ["笑", "亮", "暖", "艳", "聚会", "拥抱"],
            "紧张": ["窄", "暗", "乱", "急促", "追逐"],
        }
        expected_kw = mood_kw.get(expected_mood, [])
        matched = sum(1 for kw in expected_kw if kw in scene_description)
        if expected_kw and matched == 0:
            issues.append(f"场景情感基调与预期({expected_mood})不符")

        score = max(10 - len(issues) * 2.5, 0)
        suggestions = []
        for issue in issues:
            if "关键元素" in issue:
                suggestions.append(f"[元素缺失] {issue} — 将这些元素加入场景描述或道具清单")
            elif "情感基调" in issue:
                suggestions.append(f"[情感不符] {issue} — 调整场景的光线、色调、布局以匹配{expected_mood}氛围")
        return self._ok({"issues": issues, "consistent": len(issues) == 0}, score * 10, suggestions)
