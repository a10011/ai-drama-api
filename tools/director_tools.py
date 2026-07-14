"""
导演智能体工具集 (P0)
4个工具: analyze_pacing, suggest_camera, shotlist_from_script, quality_scorecard
"""
import json
from tools.base import AgentTool, ToolResult

# emotion → shot映射
EMOTION_SHOT_MAP = {
    "紧张": {"shot": "手持晃动", "angle": "近景/特写", "movement": "快速跟拍", "focal": "35mm", "notes": "浅景深，呼吸感"},
    "恐惧": {"shot": "低角度", "angle": "仰拍", "movement": "缓慢推进", "focal": "24mm", "notes": "压迫感，暗调"},
    "悲伤": {"shot": "慢推近景", "angle": "平视", "movement": "缓慢拉近", "focal": "50mm", "notes": "柔光，留白"},
    "愤怒": {"shot": "快速切", "angle": "低角度仰拍", "movement": "剧烈晃动", "focal": "28mm", "notes": "冷调，高对比"},
    "浪漫": {"shot": "滑轨平移", "angle": "平视", "movement": "平滑跟随", "focal": "85mm", "notes": "暖光，柔焦"},
    "悬疑": {"shot": "缓慢推拉", "angle": "荷兰角", "movement": "窥视感移动", "focal": "35mm", "notes": "阴影，蓝绿色调"},
    "喜悦": {"shot": "广角平摇", "angle": "平视/略仰", "movement": "流畅跟拍", "focal": "24mm", "notes": "明亮，暖色调"},
    "压抑": {"shot": "固定长镜", "angle": "俯拍", "movement": "极慢/静止", "focal": "50mm", "notes": "灰调，窄画幅"},
    "震撼": {"shot": "大远景→急推", "angle": "鸟瞰→平视", "movement": "急速推进", "focal": "16mm→50mm", "notes": "史诗感"},
    "温情": {"shot": "中景缓移", "angle": "平视", "movement": "轻柔滑移", "focal": "50mm", "notes": "暖黄，柔焦"},
}


class AnalyzePacing(AgentTool):
    name = "analyze_pacing"
    description = "分析剧本节奏曲线，标出高潮/低谷/转折点，量化到秒级"
    category = "analysis"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("script_text"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "script_text": {"type": "string", "description": "剧本全文"},
                "total_duration_sec": {"type": "integer", "description": "预计总时长(秒)", "default": 180}
            }
        }

    async def execute(self, script_text: str, total_duration_sec: int = 180, **kwargs) -> ToolResult:
        try:
            prompt = f"""分析以下短剧剧本的节奏。总时长约{total_duration_sec}秒。
剧本：{script_text[:4000]}
逐段评分(1-10)，标出：最高潮点、最低谷点、建议加快的拖沓段、建议放慢的关键段。
返回JSON: {{"curve":[{{"scene":"...","time_sec":0,"score":5,"label":"..."}}],"climax_at_sec":0,"slowest_at_sec":0,"suggestions":["..."]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            data = json.loads(raw.strip().split("```json")[-1].split("```")[0].strip().strip("`"))
            curve = data.get("curve", [])
            score = 70 + (len(curve) * 2) if curve else 50
            tips = data.get("suggestions", [])
            return self._ok(data, score, tips)
        except Exception as e:
            return self._fail(str(e))


class SuggestCamera(AgentTool):
    name = "suggest_camera"
    description = "根据情绪和场景推荐运镜方案"
    category = "creative"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("emotion"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "emotion": {"type": "string", "description": "主要情绪"},
                "scene_desc": {"type": "string", "description": "场景描述"},
                "character_count": {"type": "integer", "description": "出场人数"}
            }
        }

    async def execute(self, emotion: str, scene_desc: str = "", character_count: int = 2, **kwargs) -> ToolResult:
        # 先查映射表
        mapped = {"shot": "中景", "angle": "平视", "movement": "固定", "focal": "50mm", "notes": "标准镜头"}
        for key, val in EMOTION_SHOT_MAP.items():
            if key in emotion:
                mapped = val
                break

        # LLM微调
        try:
            prompt = f"""情绪：{emotion}，场景：{scene_desc}，人数：{character_count}
基础方案：{json.dumps(mapped, ensure_ascii=False)}
请微调运镜方案，考虑场景和人数。返回JSON: {{"shot_type":"...","angle":"...","movement":"...","focal_length":"...","notes":"..."}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            refined = json.loads(raw.strip().split("```json")[-1].split("```")[0].strip().strip("`"))
            return self._ok(refined, 85)
        except Exception:
            return self._ok(mapped, 70)


class ShotlistFromScript(AgentTool):
    name = "shotlist_from_script"
    description = "从剧本自动生成完整分镜表"
    category = "creative"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("script_text"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "script_text": {"type": "string", "description": "剧本全文"},
                "style": {"type": "string", "description": "风格偏好"}
            }
        }

    async def execute(self, script_text: str, style: str = "", **kwargs) -> ToolResult:
        try:
            prompt = f"""将以下短剧剧本转为完整分镜表。
剧本：{script_text[:4000]}
风格：{style or '短剧快节奏'}
每个镜头包含：shot_id, scene, camera(类型/角度/运动), dialogue(前10字), emotion, duration_sec, transition
返回JSON: {{"shots":[{{"shot_id":1,"scene":"...","camera":"...","dialogue":"...","emotion":"...","duration_sec":3,"transition":"cut"}}]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            data = json.loads(raw.strip().split("```json")[-1].split("```")[0].strip().strip("`"))
            shots = data.get("shots", [])
            score = min(100, len(shots) * 3 + 50)
            return self._ok({"shots": shots, "total_shots": len(shots)}, score)
        except Exception as e:
            return self._fail(str(e))


class QualityScorecard(AgentTool):
    name = "quality_scorecard"
    description = "多维度评估成片质量（节奏/视觉/音频/故事）"
    category = "analysis"

    def validate(self, **kwargs) -> bool:
        return bool(kwargs.get("script_text"))

    def explain(self) -> dict:
        return {
            "name": self.name, "description": self.description,
            "parameters": {
                "script_text": {"type": "string", "description": "剧本全文"},
                "shot_list": {"type": "string", "description": "分镜表(JSON)"},
                "video_url": {"type": "string", "description": "视频链接(可选)"}
            }
        }

    async def execute(self, script_text: str, shot_list: str = "", video_url: str = "", **kwargs) -> ToolResult:
        try:
            prompt = f"""作为专业影评人，多维度评估这部短剧。
剧本：{script_text[:2000]}
分镜：{shot_list[:1000] if shot_list else '未提供'}
视频：{'已提供' if video_url else '未生成'}

评分维度（各0-25分）：
1. 节奏控制 — 是否张弛有度
2. 视觉呈现 — 运镜/色调/构图
3. 音频配合 — 配音/BGM适配度
4. 故事完成度 — 情节逻辑/反转质量

返回JSON: {{"overall":80,"dimensions":{{"pacing":20,"visual":20,"audio":20,"story":20}},"suggestions":["..."]}}
只返回JSON。"""
            raw = self._call_llm(prompt)
            data = json.loads(raw.strip().split("```json")[-1].split("```")[0].strip().strip("`"))
            dims = data.get("dimensions", {})
            overall = data.get("overall", sum(dims.values()))
            tips = data.get("suggestions", [])
            return self._ok({"overall": overall, "dimensions": dims, "suggestions": tips}, overall, tips)
        except Exception as e:
            return self._fail(str(e))
