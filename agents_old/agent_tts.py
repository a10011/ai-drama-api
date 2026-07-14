"""智能体6：情绪配音智能体 — 多音色区分、情绪变速

支持导演指令的情绪/语气/表情映射
"""
import json
import time
import logging
import os
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult
from .route_manager import run_with_fallback
from services.ai_providers import CosyVoiceV2Provider, EdgeTTSProvider

logger = logging.getLogger(__name__)

# CosyVoice 音色 → Edge-TTS 音色映射（edge-tts 用微软音色名，cosyvoice 用 longxxx）
# 当 cosyvoice 不可用时，edge-tts 用此映射选择对应微软音色
EDGE_VOICE_MAP = {
    "longyan": "zh-CN-YunxiNeural",        # 沉稳男声
    "longxiaochun": "zh-CN-XiaoxiaoNeural", # 女声
    "longshu": "zh-CN-YunyangNeural",       # 书生男声
    "longcheng": "zh-CN-YunxiaNeural",      # 成熟男声
    "longhua": "zh-CN-XiaoyiNeural",        # 花旦女声
    "longshuo": "zh-CN-YunfengNeural",      # 男声
    "longjing": "zh-CN-XiaohanNeural",      # 御姐女声
    "longmiao": "zh-CN-XiaomengNeural",     # 女声
    "longfei": "zh-CN-YunhaoNeural",        # 男声
    "longfei": "zh-CN-YunzeNeural",        # 男声
}
EDGE_DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

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

VOICE_CASTING_PROMPT = """你是一位金牌配音导演，深耕影视配音10年，精通声学特征分析、角色-音色心理映射与情绪配音工程。你深谙"声音是角色的第二张脸"——观众闭上眼也能从声音辨认角色。

【专业知识·配音设计】
▎角色配音原则：
- 音色要符合角色设定的年龄、性格、外貌与社会阶层
- 语速反映角色性格（急性子快/沉稳慢/犹豫断续）
- 语气和情绪变化要适配剧情节奏，且有连续性（同一角色不能忽快忽慢）
- 多角色对话音色必须有明显区分度，避免观众混淆

▎声学特征四要素（选角时精确定义）：
- 音高(pitch)：低=沉稳/权威/压抑，中=日常/中性，高=活泼/紧张/年轻
- 音色(timbre)：磁性/清亮/沙哑/柔和/尖锐/浑厚——决定角色"听感年龄与气质"
- 语速(speed)：快=急躁/紧张/激动，慢=沉稳/悲伤/威胁，断续=犹豫/恐惧
- 共振(resonance)：胸腔共鸣=成熟/权威，头腔共鸣=年轻/明亮，鼻腔=柔弱/病态

▎角色-音色心理映射（音色暗示角色本质）：
- 霸道总裁：男中低音、胸腔共鸣、沉稳有力、含控制感、语速偏慢（不急不躁的压迫感）
- 阳光少年：男高音、头腔共鸣、清亮向上、活力、语速快
- 甜美女主：女高音、柔和、可爱、气息感（不是尖锐而是甜润）
- 知性御姐：女中音、有力、稳重、尾音下沉（气场）
- 反派：声音带笑意、尾音上挑或拖长、低沉有穿透力（笑里藏刀）
- 隐忍角色：压低嗓音、字斟句酌、偶尔爆发形成反差
- 老人：颤抖、沙哑、语速慢、气息短
- 小孩：清脆、明亮、略带稚气、语速不稳定

▎情绪配音进阶技巧（同一情绪有多种表达，要看剧情语境）：
- 愤怒三态：压抑的怒（冰冷低沉一字一顿）/爆发的怒（声嘶力竭尾音撕裂）/嘲弄的怒（带笑冷语重音反讽）
- 悲伤三态：隐忍的悲（气息不稳强忍）/崩溃的悲（抽泣断续）/释怀的悲（平静低沉带笑）
- 紧张：语速快、吞音多、音量小、气息浅
- 惊喜：音高突升、尾音上扬、语速加快
- 嘲讽：语速慢、重音偏移、带笑、尾音上挑
- 威胁/密谋：压嗓、发音清晰、语速慢、低频共振
- 心动/暧昧：气声、语速放慢、尾音轻颤、音量减小

▎配音连续性：
- 同一角色贯穿全剧音色一致（不能换声）
- 情绪变化要过渡自然，不能突变（除非剧情是爆发点）
- 多人对话时音量平衡，主角略突出

返回JSON格式（不要markdown代码块）：
{
  "casting": [
    {
      "character": "角色名",
      "gender": "男/女",
      "voice_type": "清亮/低沉/甜美/沙哑/柔和/磁性/中性/稚嫩",
      "age_range": "20-30",
      "acoustic": {"pitch": "low/medium/high", "resonance": "chest/head/nasal", "timbre_detail": "音色细节描述"},
      "speed": "slow/normal/fast",
      "emotion_range": ["喜悦", "悲伤", "愤怒", "惊恐", "平静"],
      "tts_voice_id": "推荐的TTS音色ID（如 longyan/zh-CN-XiaoxiaoNeural）",
      "character_voice_signal": "音色向观众传递的角色信息",
      "notes": "配音指导备注（含情绪转换要点）"
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

    # ── CosyVoice V2 instruction 生成 ──────────────────────────
    @staticmethod
    def _build_instruction(name: str = "", gender: str = "男",
                           scene: str = "", emotion_hint: str = "",
                           is_battle: bool = False,
                           is_farewell: bool = False) -> str:
        """按角色性别+场景+情绪生成 CosyVoice V2 instruction 控制指令"""
        name_lower = name.lower()

        # ── 性别/身份判定 ──
        is_female = gender in ("女", "female")

        # ── 判断角色所属剧集题材 ──
        _ancient_hints = ["古","将军","军","将","侠","武林","江湖","宫","帝王","殿","边关","沙场",
                         "将士","帅","君主","王府","阁","剑","镖","寨"]
        _modern_hints = ["总裁","霸总","经理","总监","办公","校园","公司","霸","职业","经理"]
        is_ancient = any(h in (scene + name + emotion_hint) for h in _ancient_hints)
        is_modern = any(h in (scene + name + emotion_hint) for h in _modern_hints)

        # ── 身份类型细分 ──
        _general_hints = ["将军","将领","帅","军","将士","边关","戍边","沙场","主帅"]
        _young_hero_hints = ["少侠","侠客","少年","青年"]
        _elder_hints = ["老者","谋士","丞相","老","谋","师"]
        _emperor_hints = ["帝王","帝","皇","王","天子","君主"]
        _female_gentle_hints = ["温婉","白衣","女子","姑娘","小姐","侍女"]
        _female_empress_hints = ["女帝","皇后","太后","贵妃"]
        _female_sweet_hints = ["甜","少女","小妹","丫鬟","宫女"]
        _modern_boss_hints = ["总裁","霸总","经理","总监","董事长"]
        _modern_office_hints = ["助理","员工","打工人","职员","员工"]
        _modern_teen_hints = ["高中生","学生"]
        _xianyi_hints = ["悬疑","刑侦","刑警","警","侦探","队长","警员"]
        _villain_hints = ["反派","敌","冷沉","内敛","阴"]

        # ── 情感基调 ──
        mood_map = {
            "悲": "隐忍怅然，语气低沉内敛，有淡淡不舍",
            "离": "离别牵挂，语速放缓，情绪内敛克制",
            "战": "战意坚定，语气庄重，气息饱满",
            "怒": "语气克制不狂躁，压抑中的冷凝",
            "喜": "轻快舒缓，语气微微上扬，温和愉快",
            "哀": "语气克制平淡，气息收束，尾音浅淡",
            "惊": "语气轻微起伏，略带讶异但不过度",
            "冷": "语气清冷理智，平稳淡然，无多余情绪",
            "温": "语气温和舒缓，气息柔和，放松自然",
        }
        mood_phrase = mood_map.get(emotion_hint[:1], "情绪自然平稳，贴合对话情境")

        # ── 语速控制 ──
        if is_farewell:
            speed_desc = "语速缓慢厚重，句间轻微停顿，尾音轻轻放缓"
        elif is_battle:
            speed_desc = "语速铿锵利落，气息饱满，短句干脆有力"
        else:
            speed_desc = "语速适中平缓，气息自然，说话松弛"

        # ── 组合成完整 instruction ──
        if is_ancient:
            if is_female:
                if any(h in name for h in _female_empress_hints):
                    return (f"成熟中年女性中音，冷冽沉稳有气场；杜绝甜美软糯、尖细少女音；"
                            f"一字一顿语速偏慢，语调淡漠威严，克制疏离，自带上位者稳重感。{mood_phrase}。")
                elif any(h in name for h in _female_sweet_hints):
                    return (f"清甜柔和少女细嗓，通透干净；禁止低沉粗哑厚重音；"
                            f"语速轻快舒缓，语调轻微上扬，情绪恬淡羞涩。{mood_phrase}。")
                else:  # 温婉女子
                    return (f"青年温婉女子，轻柔干净中嗓，音色温润干净；禁止粗哑厚重壮汉音、大嗓门；"
                            f"语速轻柔偏慢，气息舒缓内敛，{mood_phrase}。{speed_desc}。")
            else:  # 古风男性
                if any(h in name for h in _general_hints):
                    return (f"中年沙场将领，浑厚胸腔低音，嗓音带风沙干涩气声；"
                            f"禁止尖细、奶油少年音、娘娘腔、轻浮细嗓；"
                            f"{speed_desc}。{mood_phrase}。与对话对象互动，平稳无哭腔。")
                elif any(h in name for h in _young_hero_hints):
                    return (f"青年男生清亮干净中音，通透爽朗；禁止低沉粗哑、阴柔细嗓；"
                            f"语速轻快利落，语气坦荡舒展，少年意气温和明朗。{mood_phrase}。")
                elif any(h in name for h in _elder_hints):
                    return (f"老年男性温润沙哑中音，语速从容缓慢；禁止洪亮嘶吼、尖锐嗓；"
                            f"娓娓道来，思虑沉稳平和，谈吐儒雅通透。{mood_phrase}。")
                elif any(h in name for h in _villain_hints):
                    return (f"中年男性偏低冷嗓，声线紧绷压抑；杜绝阳光清亮奶油音；"
                            f"语速缓慢压低，语气冷淡疏离，暗藏算计，全程克制不狂吼。{mood_phrase}。")
                else:  # 江湖/日常古风男
                    return (f"青壮年男性中音，扎实平实自然；无尖锐轻浮音色；"
                            f"语速规整平稳，语气自然。{mood_phrase}。")

        elif is_modern:
            if is_female:
                if any(h in name for h in _female_gentle_hints):
                    return (f"青年成熟女声通透利落中音，干净冷感；禁止软糯甜腻、尖细少女音；"
                            f"语速均匀平稳，理智淡然，无多余情绪起伏。{mood_phrase}。")
                elif any(h in name for h in _female_sweet_hints) or "甜" in name:
                    return (f"清甜柔和少女软嗓，通透轻盈；杜绝低沉粗哑厚重音；"
                            f"语速轻快柔和，气息轻盈，心情恬淡愉悦。{mood_phrase}。")
                else:
                    return (f"青年温润干净中音，音色柔和松弛；杜绝粗哑浑厚男声；"
                            f"语速平缓轻柔，气息柔和，{mood_phrase}。")
            else:  # 现代男性
                if any(h in name for h in _modern_boss_hints):
                    return (f"青年成熟男性低磁胸腔冷嗓，醇厚清冷；禁止奶油少年细嗓、娘娘腔；"
                            f"语速偏慢字句清晰，情绪克制平淡，交谈冷静理性，轻微加重关键台词。{mood_phrase}。")
                elif any(h in name for h in _modern_office_hints):
                    return (f"青年中性平实生活化中音，音色自然无极端高低；"
                            f"语速适中，语气恭敬平实，日常低声沟通。{mood_phrase}。")
                elif any(h in name for h in _modern_teen_hints):
                    return (f"少年清亮通透中音，干净爽朗；杜绝低沉粗哑、阴柔细嗓；"
                            f"语速轻快自然，心态轻松温和。{mood_phrase}。")
                else:
                    return (f"青年温润干净中音，气息柔和松弛；杜绝冷硬低沉粗嗓；"
                            f"语速平缓轻柔，语调轻微上扬，包容温和。{mood_phrase}。")

        elif any(h in (scene + emotion_hint) for h in _xianyi_hints):
            return (f"中年偏低粗砺中音，踏实稳重；杜绝轻柔细嗓、阴柔音色；"
                    f"语速平缓严谨，逻辑清晰，情绪冷静理性。{mood_phrase}。")

        # ── 通用旁白 / 兜底 ──
        return (f"中年中性标准播音腔，音色平稳厚重；无刺耳电子音；"
                f"全程匀速舒缓，停顿规整，中立叙事，客观讲述画面剧情。{mood_phrase}。")


    def generate_speech(self, text: str, voice: str = "longyan", speed: float = 1.0,
                        emotion: str = "平静", tone: str = "正常",
                        instruction: str = None) -> AgentResult:
        """生成配音音频 — 带情绪参数"""
        start = time.time()
        from .result_cache import get as cache_get, set as cache_set

        # ── 缓存检查 ──
        # [cosyvoice-v2] instruction 控制影响音质，加入缓存键区分
        inst_suffix = f"_{hash(instruction) % 100000}" if instruction else ""
        cache_key = f"tts_{text}_{voice}_{speed}_{emotion}{inst_suffix}"
        cached = cache_get(cache_key, "agent_tts_v2")
        if cached and cached.get("audio_url"):
            logger.info(f"[Cache] 命中TTS缓存: {text[:30]}...")
            return AgentResult(data=cached, duration_ms=0)

        # 如果传了 emotion，且 voice/speed 未被调用方显式指定（仍是默认值），才用情绪映射
        # [bugfix] 调用方（如 generate 方法）已按角色+场景计算好音色/语速，不会被 emotion 覆盖
        if emotion and emotion in EMOTION_VOICE_MAP:
            params = EMOTION_VOICE_MAP[emotion]
            if voice == "longyan":
                voice = params["voice"]
            if speed == 1.0:
                speed = params.get("speed", 1.0)

        def _tts_provider_fn(info, timeout=20, **kw):
            name = info["name"]
            if name == "cosyvoice":

                provider = CosyVoiceV2Provider()
                # [bugfix] CosyVoiceV2Provider.synthesize 已返回 dict {audio_file, audio_data}
                # emotion 不再透传给 dashscope SDK（不支持），由 voice 切换音色
                # [cosyvoice-v2] 传 instruction 实现人设+情绪+语速控制
                audio = provider.synthesize(text=text, voice=voice, speed=speed,
                                            instruction=instruction)
                if audio and isinstance(audio, dict) and audio.get("audio_file"):
                    # synthesize 已落盘到 /www/wwwroot/storage/audio/，转成可公开访问 URL
                    local_path = audio["audio_file"]
                    # 若指定了 pipeline_id，移动到对应子目录便于隔离
                    pipeline_id = kw.get("pipeline_id", "") if kw else ""
                    if pipeline_id:
                        base_dir = f"/www/wwwroot/storage/{pipeline_id}/audio"
                        os.makedirs(base_dir, exist_ok=True)
                        import shutil as _sh
                        new_path = f"{base_dir}/{os.path.basename(local_path)}"
                        try:
                            _sh.move(local_path, new_path)
                            local_path = new_path
                        except Exception:
                            pass
                        audio_url = f"https://ai.mzsh.top/storage/{pipeline_id}/audio/{os.path.basename(local_path)}"
                    else:
                        audio_url = f"https://ai.mzsh.top/storage/audio/{os.path.basename(local_path)}"
                    return {"audio_url": audio_url, "text": text, "voice": voice, "speed": speed, "emotion": emotion, "instruction": instruction, "local_path": local_path}
                return None
            elif name == "edge-tts":
                from services.media_registry import register_audio

                provider = EdgeTTSProvider()
                # [bugfix] voice 可能是 cosyvoice 音色名(如 longyan)，edge-tts 不认识
                # 映射到微软音色名，映射不到则用默认女声
                edge_voice = EDGE_VOICE_MAP.get(voice, EDGE_DEFAULT_VOICE)
                result = provider.generate_tts(text, edge_voice, speed)
                if result and isinstance(result, dict) and result.get("audio_url") and "base64" in result.get("audio_url", ""):
                    # 补充 local_path 供下游视频合成用音频文件路径
                    if result.get("audio_file"):
                        result["local_path"] = result["audio_file"]
                    return result
                return None
            elif name == "silent":
                raise Exception(f"TTS({voice})不可用，请稍后重试")
            return None

        # [bugfix] 不走 route_manager 的 fail_flag 缓存(对本地 edge-tts 有害：
        # 一次失败会冷却5分钟，导致后续全跳过)。改为直接顺序调 provider。
        result = None
        # 顺序：edge-tts(本地免费，主力) → cosyvoice(需服务开通)
        for info in ({"name": "edge-tts"}, {"name": "cosyvoice"}):
            try:
                r = _tts_provider_fn(info, timeout=20)
                if r:
                    result = {"success": True, "data": r, "provider": info["name"],
                              "model": info["name"], "error": ""}
                    break
            except Exception as e:
                logger.warning(f"[tts] {info['name']} 失败: {str(e)[:120]}")
        if not result:
            result = {"success": False, "data": None, "provider": "", "model": "",
                      "error": "edge-tts 与 cosyvoice 均失败", "all_failed": True}
        if result["success"]:
            # 带上情绪信息给下游使用
            data = result["data"]
            data["_emotion"] = emotion
            data["_tone"] = tone
            data["_instruction"] = instruction
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
        # ═══ 接收导演配音指令 ═══
        dt = kwargs.get("director_tasks", kwargs.get("params", {}).get("director_tasks", {}))
        da = kwargs.get("director_analysis", kwargs.get("params", {}).get("director_analysis", {}))
        self._director_tts_hint = ""
        if isinstance(dt, dict) and dt.get("tts_voice"):
            self._director_tts_hint = str(dt["tts_voice"])
        if not self._director_tts_hint and isinstance(da, dict):
            arch = da.get("character_archetypes", "")
            if arch:
                self._director_tts_hint = str(arch)
        if self._director_tts_hint:
            import logging; logging.getLogger(__name__).info(f"[TTS] 接收导演配音指令: {self._director_tts_hint[:80]}...")
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
                # 确定角色音色 — 按角色性别+场景智能匹配
                char_info = kwargs.get("character_voices", {})
                speaker_name = shot.get("speaker", shot.get("character", ""))
                voice = "longyan"
                speed = 1.0

                # 角色配置的音色优先
                _char_voice = ""
                for name, vinfo in char_info.items():
                    if name == speaker_name:
                        _char_voice = vinfo.get("voice", "")
                        break

                if not _char_voice:
                    # 没有配置 → 按角色名+性别+场景智能匹配
                    _chars = kwargs.get("characters", [])
                    _gender = ""
                    for _ch in _chars:
                        if _ch.get("name", "") == speaker_name:
                            _gender = _ch.get("gender", "")
                            break
                    if not _gender:
                        _female_hints = ["玉","漱","娘","姐","妹","女","妃","姬","婉","柔"]
                        _gender = "女" if any(h in speaker_name for h in _female_hints) else "男"

                    scene = shot.get("scene", "")
                    emotion_hint = shot.get("emotion", "")
                    _is_battle = any(kw in (scene + emotion_hint) for kw in ["战场","冲锋","号令","激昂","愤怒","军阵"])
                    _is_farewell = any(kw in (scene + emotion_hint) for kw in ["离别","诀别","断崖","不舍","悲伤","苍凉","凄"])

                    if _gender in ("女", "female"):
                        # 女声：温婉女子用柔美音色，悲伤离别用沉稳女声
                        if _is_farewell:
                            voice = "longjing"  # 御姐女声 温柔中带坚强
                            speed = 0.85       # 离别放慢
                        else:
                            voice = "longjing"
                            speed = 0.9
                    else:
                        # 男声：将军用低沉浑厚，战场用洪亮，离别用沉稳克制
                        if _is_battle:
                            voice = "longfei"  # 低沉浑厚 战场指挥
                            speed = 1.0
                        elif _is_farewell:
                            voice = "longfei"  # 低沉 离别克制
                            speed = 0.85       # 离别放慢
                        else:
                            voice = "longfei"  # 默认低沉男声
                            speed = 0.95

                    logger.info(f"[TTS] {speaker_name} 性别={_gender} 场景={'战场' if _is_battle else '离别' if _is_farewell else '日常'} → 音色={voice} 语速={speed}")
                else:
                    voice = _char_voice

                # [cosyvoice-v2] 按角色+场景动态生成 instruction
                instruction = self._build_instruction(
                    name=speaker_name,
                    gender=_gender if not _char_voice else "男",
                    scene=shot.get("scene", ""),
                    emotion_hint=shot.get("emotion", ""),
                    is_battle=_is_battle if not _char_voice else False,
                    is_farewell=_is_farewell if not _char_voice else False
                )
                try:
                    result = self.generate_speech(
                        text=dialogue,
                        voice=voice,
                        speed=speed,
                        emotion=mood,
                        tone="正常",
                        instruction=instruction
                    )
                    if result.success:
                        audio_url = result.data.get("audio_url", "")
                        audio_files.append({
                            "shot_index": i,
                            "dialogue": dialogue,
                            "audio_url": audio_url,
                            "voice": voice,
                            "speed": speed,
                            "emotion": mood,
                            "duration_sec": len(dialogue) / 3.0  # 粗略估算
                        })
                    else:
                        audio_files.append({
                            "shot_index": i,
                            "dialogue": dialogue,
                            "audio_url": "",
                            "type": "dialogue",
                            "error": result.error
                        })
                except Exception as e:
                    audio_files.append({
                        "shot_index": i,
                        "dialogue": dialogue,
                        "audio_url": "",
                        "type": "dialogue",
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