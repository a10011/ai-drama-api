"""智能体6：情绪配音智能体 — 多音色区分、情绪变速

支持导演指令的情绪/语气/表情映射
"""
import json
import time
import logging
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult
from .route_manager import run_with_fallback
from services.ai_providers import CosyVoiceV2Provider, EdgeTTSProvider

logger = logging.getLogger(__name__)

# 情绪 → 语速/音色映射
# 中文情绪(脚本输出) → CosyVoice情感标签 + v2推荐音色
EMOTION_VOICE_MAP = {
    "愤怒": {"emotion": "angry", "voice": "longyan", "speed": 1.0, "note": "沉稳播音腔带愤怒"},
    "激动": {"emotion": "angry", "voice": "longyan", "speed": 1.0, "note": "沉稳播音腔带激动"},
    "兴奋": {"emotion": "happy", "voice": "longze", "speed": 1.0, "note": "温暖男声带兴奋"},
    "喜悦": {"emotion": "happy", "voice": "longhua_v2", "speed": 1.0, "note": "元气女声带喜悦"},
    "高兴": {"emotion": "happy", "voice": "longhua_v2", "speed": 1.0, "note": "元气女声带高兴"},
    "开心": {"emotion": "happy", "voice": "longhua_v2", "speed": 1.0, "note": "元气女声带开心"},
    "悲伤": {"emotion": "sad", "voice": "longxiaoxia_v2", "speed": 1.0, "note": "沉稳女声带悲伤"},
    "难过": {"emotion": "sad", "voice": "longxiaoxia_v2", "speed": 1.0, "note": "沉稳女声带难过"},
    "低落": {"emotion": "sad", "voice": "longxiaoxia_v2", "speed": 1.0, "note": "沉稳女声带低落"},
    "恐惧": {"emotion": "fearful", "voice": "longxiaoxia_v2", "speed": 1.0, "note": "沉稳女声带恐惧"},
    "紧张": {"emotion": "neutral", "voice": "longxing_v2", "speed": 1.0, "note": "邻家女声紧张"},
    "焦虑": {"emotion": "fearful", "voice": "longxing_v2", "speed": 1.0, "note": "邻家女声焦虑"},
    "温柔": {"emotion": "neutral", "voice": "longanrou", "speed": 1.0, "note": "温柔闺蜜女"},
    "委屈": {"emotion": "sad", "voice": "longhua_v2", "speed": 1.0, "note": "元气女声委屈"},
    "爱慕": {"emotion": "neutral", "voice": "longanrou", "speed": 1.0, "note": "温柔闺蜜女"},
    "平静": {"emotion": "neutral", "voice": "longyan", "speed": 1.0, "note": "沉稳播音腔"},
    "放松": {"emotion": "neutral", "voice": "longanlang", "speed": 1.0, "note": "清爽利落男"},
    "惊讶": {"emotion": "surprised", "voice": "longxing_v2", "speed": 1.0, "note": "邻家女声惊讶"},
    "惊喜": {"emotion": "surprised", "voice": "longhua_v2", "speed": 1.0, "note": "元气女声惊喜"},
    "嘲讽": {"emotion": "neutral", "voice": "longanlang", "speed": 1.0, "note": "清爽男声嘲讽"},
    "轻蔑": {"emotion": "disgusted", "voice": "longanlang", "speed": 1.0, "note": "清爽男声轻蔑"},
    "嫉妒": {"emotion": "angry", "voice": "longyan", "speed": 1.0, "note": "沉稳男声嫉妒"},
    "得意": {"emotion": "happy", "voice": "longze", "speed": 1.0, "note": "温暖男声得意"},
    "失望": {"emotion": "sad", "voice": "longanwen", "speed": 1.0, "note": "优雅女声失望"},
    "冷漠": {"emotion": "neutral", "voice": "longyingjing", "speed": 1.0, "note": "低调冷静女"},
    "无奈": {"emotion": "neutral", "voice": "longanwen", "speed": 1.0, "note": "优雅女声无奈"},
    "默认": {"emotion": "neutral", "voice": "longyan", "speed": 1.0, "note": "沉稳播音腔"},
}

VOICE_CASTING_PROMPT = """你是一位配音导演。根据角色人设和剧本，为每个角色分配配音方案。

【专业知识·配音/配乐设计】
▎角色配音原则：
- 音色要符合角色设定的年龄、性格和外貌
- 语速反映角色性格（急性子快/沉稳慢）
- 语气和情绪变化要适配剧情节奏

▎常见角色音色匹配：
- 霸道总裁：男中低音、沉稳有力、含控制感
- 阳光少年：男高音、清亮向上、活力
- 甜美女主：女高音、柔和、可爱
- 知性御姐：女中音、有力、稳重
- 反派：声音带笑意、尾音上挑或拖长
- 老人：颤抖、沙哑、语速慢
- 小孩：清脆、明亮、略带稚气

▎情绪配音技巧：
- 愤怒：力度加大、尾音重、语速先慢后快
- 悲伤：气息不稳、停顿多、音调下沉
- 紧张：语速快、吞音多、音量小
- 惊喜：音高突升、尾音上扬
- 嘲讽：语速慢、重音偏移、带笑
- 低声（密谋/威胁）：压嗓、发音清晰、语速慢

返回JSON格式（不要markdown代码块）：
{
  "casting": [
    {
      "character": "角色名",
      "gender": "男/女",
      "voice_type": "清亮/低沉/甜美/沙哑/柔和/磁性/中性/稚嫩",
      "age_range": "20-30",
      "speed": "slow/normal/fast",
      "pitch": "low/medium/high",
      "emotion_range": ["喜悦", "悲伤", "愤怒", "惊恐", "平静"],
      "tts_voice_id": "推荐的TTS音色ID",
      "notes": "配音指导备注"
    }
  ],
  "style_guide": "整体配音风格指导"
}"""

DIALOGUE_SPEECH_PROMPT = """你是一位配音演员。根据台词内容和情绪标注，输出带情绪的配音标注文本。

返回JSON格式（不要markdown代码块）：
{
  "annotated_lines": [
    {
      "line_num": 1,
      "character": "角色名",
      "original_text": "原始台词",
      "emotion": "喜悦/悲伤/愤怒/惊恐/平静/嘲讽/温柔/激动",
      "speed_mark": "slow/normal/fast",
      "pause_before_ms": 300,
      "pause_after_ms": 500,
      "emphasis_words": ["需要重读的词1", "词2"],
      "annotated_text": "带情绪标记的文本（用[emotion]标记）"
    }
  ]
}"""


class TTSAgent(BaseAgent):
    """情绪配音智能体：多音色区分、情绪变速"""

    name = "情绪配音智能体"
    description = "多音色区分、情绪变速、支持导演指令"
    version = "2.0.0"

    @staticmethod
    def emotion_to_params(emotion: str) -> dict:
        """根据情绪返回推荐的TTS参数"""
        return EMOTION_VOICE_MAP.get(emotion, EMOTION_VOICE_MAP["默认"])

    def voice_casting(self, characters: List[Dict]) -> AgentResult:
        """角色配音选角"""
        start = time.time()
        try:
            char_info = json.dumps([
                {"name": c.get("name", ""), "gender": c.get("gender", ""),
                 "personality": c.get("personality", {}).get("traits", [])[:3]
                 if isinstance(c.get("personality"), dict) else []}
                for c in characters
            ], ensure_ascii=False, indent=2)
            user_prompt = f"""角色列表：
{char_info}

请为每个角色匹配最合适的配音方案。"""
            result = self._call_llm_json(VOICE_CASTING_PROMPT, user_prompt, retries=2)
            if isinstance(result, dict):
                if 'dub_config' not in result:
                    casting = result.get('casting', [])
                    result['dub_config'] = []
                    for c in casting:
                        result['dub_config'].append({
                            'character': c.get('character', '旁白'),
                            'voice_type': c.get('voice_type', '标准'),
                            'emotion': c.get('emotion_range', ['平静'])[0] if isinstance(c.get('emotion_range'), list) else '平静',
                            'speed': 1.0 if c.get('speed') == 'normal' else 0.8 if c.get('speed') == 'slow' else 1.2,
                            'text': '',
                            'status': 'pending'
                        })
            return AgentResult(
                data=result,
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"配音选角失败: {e}")
            return AgentResult(success=False, error=str(e))

    def annotate_dialogue(self, script: str, character_voices: Dict[str, Dict]) -> AgentResult:
        """标注台词的语速情绪"""
        start = time.time()
        try:
            voice_info = json.dumps(character_voices, ensure_ascii=False, indent=2)
            user_prompt = f"""剧本片段（带角色标记）：
{script[:4000]}

配音方案：
{voice_info}

请为每句台词标注情绪和语速。"""
            result = self._call_llm_json(DIALOGUE_SPEECH_PROMPT, user_prompt, retries=2)
            return AgentResult(
                data=result,
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"台词标注失败: {e}")
            return AgentResult(success=False, error=str(e))

    def generate_speech(self, text: str, voice: str = "longyan", speed: float = 1.0,
                        emotion: str = "平静", tone: str = "正常") -> AgentResult:
        """生成配音音频 — 带情绪参数"""
        start = time.time()
        from .result_cache import get as cache_get, set as cache_set

        # ── 缓存检查 ──
        cache_key = f"tts_{text}_{voice}_{speed}_{emotion}"
        cached = cache_get(cache_key, "agent_tts_v2")
        if cached and cached.get("audio_url"):
            logger.info(f"[Cache] 命中TTS缓存: {text[:30]}...")
            return AgentResult(data=cached, duration_ms=0)

        # 如果传了 emotion，覆盖音色/语速
        if emotion and emotion in EMOTION_VOICE_MAP:
            params = EMOTION_VOICE_MAP[emotion]
            voice = params["voice"]
            speed = params.get("speed", 1.0)

        def _tts_provider_fn(info, timeout=20, **kw):
            name = info["name"]
            if name == "cosyvoice":

                provider = CosyVoiceV2Provider()
                # Use the real CosyVoice emotion tag from the map
                emo_tag = EMOTION_VOICE_MAP.get(emotion, {}).get("emotion", None) if emotion else None
                audio = provider.synthesize(text, voice, speed, emotion=emo_tag)
                if audio and isinstance(audio, bytes) and len(audio) > 100:
                    # 存本地文件（避免存 base64 到 DB 失败）
                    ts = int(1000 * time.time())
                    pipeline_id = kwargs.get("pipeline_id", "")
                    base_dir = f"/www/wwwroot/storage/{pipeline_id}" if pipeline_id else "/www/wwwroot/storage"
                    local_path = f"{base_dir}/audio/{ts}.wav"
                    with open(local_path, "wb") as f:
                        f.write(audio)
                    audio_url = f"https://ai.mzsh.top/storage/{pipeline_id}/audio/{ts}.wav" if pipeline_id else f"https://ai.mzsh.top/storage/audio/{ts}.wav"
                    return {"audio_url": audio_url, "text": text, "voice": voice, "speed": speed, "emotion": emotion, "local_path": local_path}
                return None
            elif name == "edge-tts":
                from services.media_registry import register_audio

                provider = EdgeTTSProvider()
                result = provider.generate_tts(text, voice, speed)
                if result and isinstance(result, dict) and result.get("audio_url") and "base64" in result.get("audio_url", ""):
                    return result
                return None
            elif name == "silent":
                raise Exception(f"TTS({voice})不可用，请稍后重试")
            return None

        result = run_with_fallback("tts", _tts_provider_fn)
        if result["success"]:
            # 带上情绪信息给下游使用
            data = result["data"]
            data["_emotion"] = emotion
            data["_tone"] = tone
            cache_set(cache_key, "agent_tts_v2",
                      data={"audio_url": data.get("audio_url",""), "text": text,
                            "voice": voice, "speed": speed, "_emotion": emotion, "_tone": tone})
            return AgentResult(data=data, duration_ms=int((time.time()-start)*1000))
        return AgentResult(success=False, error=result["error"])

    def synthesize(self, text: str, voice: str = "longyan", speed: float = 1.0,
                   config: dict = None) -> AgentResult:
        """合成语音 - action='synthesize'的入口"""
        emotion = "平静"
        tone = "正常"
        if config:
            emotion = config.get("emotion", config.get("_emotion", emotion))
            tone = config.get("tone", config.get("_tone", tone))
            speed = config.get("speed", speed)
            voice = config.get("voice", voice)
        return self.generate_speech(text=text, voice=voice, speed=speed, emotion=emotion, tone=tone)

    def run(self, action: str = "casting", **kwargs) -> AgentResult:
        if action == "casting" or action == "auto_assign":
            return self.voice_casting(kwargs.get("characters", []))

        if action == "generate":
            # 真实配音：从 shots 提取对话，逐个调用 CosyVoice
            shots = kwargs.get("shots", [])
            audio_files = []
            for i, shot in enumerate(shots):
                dialogue = shot.get("dialogue", shot.get("subtitle", shot.get("text", "")))
                if not dialogue:
                    continue
                # 确定情绪 — 直接使用中文情绪标签
                mood = shot.get("mood", "中性")
                # 确定角色音色（如果是主角用女性，旁白标准）
                char_info = kwargs.get("character_voices", {})
                voice = "longyan"  # 默认
                for name, vinfo in char_info.items():
                    if name == shot.get("speaker", shot.get("character", "")):
                        voice = vinfo.get("voice", "longwan")
                        break
                try:
                    result = self.generate_speech(
                        text=dialogue,
                        voice=voice,
                        speed=1.0,
                        emotion=mood,
                        tone="正常"
                    )
                    if result.success:
                        audio_url = result.data.get("audio_url", "")
                        audio_files.append({
                            "shot_index": i,
                            "dialogue": dialogue,
                            "audio_url": audio_url,
                            "emotion": mood,
                            "duration_sec": len(dialogue) / 3.0  # 粗略估算
                        })
                    else:
                        audio_files.append({
                            "shot_index": i,
                            "dialogue": dialogue,
                            "audio_url": "",
                            "error": result.error
                        })
                except Exception as e:
                    audio_files.append({
                        "shot_index": i,
                        "dialogue": dialogue,
                        "audio_url": "",
                        "error": str(e)
                    })
            return AgentResult(data={
                "audio_files": audio_files,
                "total": len(shots),
                "success_count": sum(1 for a in audio_files if a.get("audio_url"))
            })
        elif action == "annotate":
            return self.annotate_dialogue(
                kwargs.get("script", ""),
                kwargs.get("character_voices", {})
            )
        elif action == "speech":
            return self.generate_speech(
                kwargs.get("text", ""),
                kwargs.get("voice", "zh-CN-standard"),
                kwargs.get("speed", 1.0),
                kwargs.get("emotion", "平静"),
                kwargs.get("tone", "正常")
            )
        elif action == "synthesize":
            return self.synthesize(
                text=kwargs.get("text", ""),
                voice=kwargs.get("voice", "longyan"),
                speed=kwargs.get("speed", 1.0),
                config=kwargs.get("config", None),
            )

        return AgentResult(success=False, data={}, error=f"未知动作: {action}")


    def execute(self, shot: dict, config: dict = None, **kwargs):
        """唯一入口：合成配音"""
        text = shot.get("dialogue", "")
        if not text:
            return AgentResult(success=False, error="无台词文本")
        return self.synthesize(text=text, config=config)