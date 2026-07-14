"""
TTS/BGM/Video/Subtitle/Composite 专用工具
精简实用，每个agent一个核心工具
"""
import logging
from tools.base import AgentTool, ToolResult

logger = logging.getLogger("tools.media")


# ======== TTS ========
class VoiceEmotionGuide(AgentTool):
    name = "voice_emotion_guide"
    description = "根据台词情感推荐TTS音色和语速参数"
    category = "tts"

    async def execute(self, dialogue: str = "", emotion: str = "", character: str = "") -> ToolResult:
        if not dialogue:
            return self._fail("缺少台词")

        emotion_map = {
            "愤怒": {"voice": "longwan", "speed": 1.3, "pitch": "+10%", "note": "语速加快，音调升高"},
            "悲伤": {"voice": "longwan", "speed": 0.8, "pitch": "-5%", "note": "语速放缓，压低声调"},
            "欢乐": {"voice": "longwan", "speed": 1.15, "pitch": "+5%", "note": "轻松明快"},
            "紧张": {"voice": "longwan", "speed": 1.2, "pitch": "0", "note": "急促但不失清晰"},
            "平静": {"voice": "longwan", "speed": 1.0, "pitch": "0", "note": "自然沉稳"},
            "浪漫": {"voice": "longwan", "speed": 0.9, "pitch": "-3%", "note": "温柔缓慢"},
        }
        params = emotion_map.get(emotion, {"voice": "longwan", "speed": 1.0, "pitch": "0", "note": "自然语气"})
        params["character"] = character
        params["dialogue_preview"] = dialogue[:50]
        return self._ok(params, 80)


# ======== BGM ========
class BGMMoodMatcher(AgentTool):
    name = "bgm_mood_matcher"
    description = "根据场景情感匹配BGM风格和节奏"
    category = "bgm"

    async def execute(self, scene_mood: str = "", scene_type: str = "", intensity: str = "中等") -> ToolResult:
        mood_bgm = {
            "悲伤": {"style": "钢琴独奏/弦乐", "tempo": "慢板 60-80 BPM", "key": "小调", "instruments": "钢琴、大提琴"},
            "欢乐": {"style": "轻快流行/民谣", "tempo": "中快板 100-130 BPM", "key": "大调", "instruments": "吉他、钢琴"},
            "紧张": {"style": "电子/打击乐", "tempo": "快板 120-150 BPM", "key": "小调/无调性", "instruments": "合成器、鼓组"},
            "浪漫": {"style": "爵士/管弦", "tempo": "中板 70-90 BPM", "key": "大调", "instruments": "萨克斯、钢琴"},
            "动作": {"style": "摇滚/电子", "tempo": "快板 140-180 BPM", "key": "小调", "instruments": "电吉他、鼓"},
            "悬疑": {"style": "环境音/极简", "tempo": "自由节拍", "key": "无调性", "instruments": "合成器、弦乐泛音"},
        }
        result = mood_bgm.get(scene_mood, {"style": "环境音", "tempo": "中板", "key": "自然", "instruments": "钢琴"})
        result["scene_type"] = scene_type
        result["intensity"] = intensity
        return self._ok(result, 80)


# ======== Video ========
class VideoPromptOptimizer(AgentTool):
    name = "video_prompt_optimizer"
    description = "优化视频生成prompt，添加上下文和动作描述"
    category = "video"

    async def execute(self, scene_desc: str = "", action: str = "", emotion: str = "", characters: str = "") -> ToolResult:
        if not scene_desc:
            return self._fail("缺少场景描述")

        issues = []
        tips = []

        motion_map = {
            "对话": "subtle natural movements, slight gestures, talking",
            "行走": "smooth walking motion, steady camera follow",
            "奔跑": "dynamic running, motion blur, energetic chase",
            "打斗": "intense combat, fast motion, dramatic action",
            "哭泣": "slow emotional scene, gentle camera movement",
        }
        motion = motion_map.get(action, "natural movement, cinematic pacing")
        
        # 质量诊断
        if not action or len(action) < 2:
            issues.append("缺少动作描述，画面会静止")
            tips.append("[动作缺失] 添加人物动作：行走/对话/回头/抬手等微动作")
        if not characters or len(characters) < 1:
            tips.append("[角色不清] 补角色名让视频模型知道谁在表演")
        if not emotion:
            tips.append("[情感缺失] 添加情感基调→影响运镜速度和光线氛围")
        if len(scene_desc) < 20:
            issues.append("场景描述过短")
            tips.append("[描述不足] 补环境/光线/道具细节→视频更真实")

        prompt = (
            f"Cinematic video scene: {scene_desc}. "
            f"Action: {motion}. "
            f"Mood: {emotion}. "
            f"Characters: {characters}. "
            f"4K, 24fps, professional cinematography, smooth camera work, "
            f"realistic lighting, film grain, natural color grading."
        )
        score = 85 - len(issues) * 15
        return self._ok({"prompt": prompt, "scene": scene_desc, "motion": motion, "issues": issues}, score, tips)


class VideoInputValidator(AgentTool):
    """检查图生视频输入质量：图片URL、参数完整性"""
    name = "video_input_validator"
    description = "校验图生视频的输入参数是否完整可靠"
    category = "video"

    async def execute(self, image_url: str = "", prompt: str = "", 
                      duration: float = 5, resolution: str = "720P") -> ToolResult:
        issues = []
        tips = []

        if not image_url:
            issues.append("缺少输入图片URL")
            tips.append("[图片缺失] 场景生图必须完成，确认image_url可访问")
        elif not (image_url.startswith("http://") or image_url.startswith("https://")):
            issues.append(f"图片URL无效格式: {image_url[:60]}")
            tips.append("[URL格式] 需完整HTTP地址，检查场景生图结果")

        if not prompt or len(prompt) < 15:
            issues.append("视频prompt过短")
            tips.append("[Prompt不足] 至少描述动作和场景氛围")

        if duration < 2:
            issues.append(f"时长{duration}s过短")
            tips.append("[时长过短] 建议≥2秒")

        if resolution not in ("720P", "1080P", "480P"):
            issues.append(f"分辨率{resolution}无效")
            tips.append("[分辨率] 使用720P或1080P")

        score = max(0, 100 - len(issues) * 20)
        return self._ok({
            "valid": len(issues) == 0,
            "issues": issues,
        }, score, tips)


# ======== Subtitle ========
class SubtitleStyleGuide(AgentTool):
    name = "subtitle_style_guide"
    description = "根据短剧类型推荐字幕样式、字体、位置"
    category = "subtitle"

    async def execute(self, genre: str = "现代", mood: str = "") -> ToolResult:
        styles = {
            "现代": {"font": "微软雅黑", "color": "#FFFFFF", "outline": "#000000", "position": "bottom", "size": "36px"},
            "古装": {"font": "楷体", "color": "#F5E6C8", "outline": "#4A3728", "position": "bottom", "size": "38px"},
            "科幻": {"font": "Consolas", "color": "#00FFAA", "outline": "#001100", "position": "bottom", "size": "34px"},
            "悬疑": {"font": "黑体", "color": "#FFFFFF", "outline": "#333333", "position": "bottom", "size": "36px"},
            "喜剧": {"font": "微软雅黑", "color": "#FFD700", "outline": "#000000", "position": "bottom", "size": "38px"},
        }
        style = styles.get(genre, styles["现代"])
        style["genre"] = genre
        style["mood"] = mood
        return self._ok(style, 80)


# ======== Composite ========
class CompositeQualityCheck(AgentTool):
    name = "composite_quality_check"
    description = "检查视频合成后的质量指标"
    category = "composite"

    async def execute(self, clips_count: int = 0, total_duration: float = 0, expected_duration: float = 0,
                      has_audio: bool = True, has_subtitle: bool = True, output_url: str = "") -> ToolResult:
        issues = []
        score = 80

        if clips_count == 0:
            issues.append("没有合成片段")
            score = 0
        if total_duration > 0 and expected_duration > 0:
            diff = abs(total_duration - expected_duration)
            if diff > expected_duration * 0.1:
                issues.append(f"时长偏差{int(diff)}s")
                score -= 15
        if not has_audio:
            issues.append("缺少音频轨道")
            score -= 20
        if not has_subtitle:
            issues.append("缺少字幕轨道")
            score -= 15

        return self._ok({
            "clips": clips_count, "duration": total_duration,
            "has_audio": has_audio, "has_subtitle": has_subtitle,
            "issues": issues, "score": score
        }, score)


# 注册列表
MEDIA_TOOLS = [
    VoiceEmotionGuide(),
    BGMMoodMatcher(),
    VideoPromptOptimizer(),
    SubtitleStyleGuide(),
    CompositeQualityCheck(),
]
