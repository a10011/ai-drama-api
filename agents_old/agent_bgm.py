"""智能体8：BGM配乐智能体 — 剧情自动匹配BGM、卡点音效"""
import json
import time
import logging
from typing import Optional, Dict, List
from .agent_base_legacy import BaseAgent, AgentResult

from services.balance_manager import record_cost
logger = logging.getLogger(__name__)

BGM_MATCH_PROMPT = """你是一位金牌影视配乐师，深耕短剧配乐10年，精通用音乐操控观众情绪。你深谙配器学、和声进行、节奏卡点与情绪心理学。

【专业规范】
1. 情绪→配器映射：
   - 压抑/绝望：低音提琴单音持续 + 无旋律氛围垫 + 缓慢弦乐揉弓
   - 紧张/悬疑：弦乐高音持续抖弓(tremolo) + 鼓点渐密 + 电子长音不和谐音程
   - 催泪/悲伤：钢琴单音慢板 + 小提琴哭腔揉弦 + 大提琴低吟
   - 爽感爆发：重型鼓点 + 铜管齐鸣 + 人声合唱爆发 + bass sub
   - 温馨/甜蜜：原声吉他轻拨 + 口哨/哼唱 + 钢琴琶音 + 轻打击乐
   - 史诗/燃：管弦乐全奏 + 定音鼓 + 法国号主题 + 渐强crescendo
   - 浪漫/暧昧：竖琴琶音 + 弦乐ppp弱奏 + 钢琴高音区 + 长笛
2. BPM（每分钟拍数）选择：
   - 慢板 60-80：悲伤/压抑/浪漫/回忆
   - 中板 90-110：温馨/日常/叙事推进
   - 快板 120-140：紧张/追逐/悬疑
   - 极快 150+：爆发/高潮/打脸
3. 情绪曲线设计：BGM不能一首到底，要随剧情起伏。
   入场弱(p)→发展渐强(mf)→高潮强(f)→收尾弱(pp)，与台词节奏同步呼吸。
4. 卡点音效（关键！）：在情绪转折/动作发生/反转点加音效。
   - 打脸/反转：重低音"咚"+ 金属脆响
   - 惊讶/真相揭露：短促弦乐上行滑音 + 静音0.3秒（制造真空感）
   - 表白/心动：清脆风铃/钢琴高音单音
   - 危机降临：低频sub bass渐强 + 心跳声
5. 音量混音：BGM音量 0.4-0.6（不压台词），有人声时自动ducking压低，台词停顿处BGM浮起。
6. 全片风格统一：同一部剧的BGM要在配器/调性上保持统一感，不同场景是变奏而非换曲风。

返回JSON格式（不要markdown代码块）：
{
  "bgm_tracks": [
    {
      "scene_index": 1,
      "scene_name": "场景名称",
      "mood": "情绪描述",
      "bgm_genre": "流行/古典/电子/民谣/爵士/摇滚/氛围/影视原声",
      "bgm_style": "温馨/紧张/悲壮/欢快/悬疑/浪漫/史诗/轻松",
      "bgm_tempo": "慢板/中板/快板",
      "bpm": 90,
      "instrumentation": "具体乐器组成（如：钢琴+小提琴+大提琴）",
      "harmony": "和声进行建议（如：小调i-VI-III-VII）",
      "dynamics": "力度变化（如：p→mf→f→pp）",
      "bgm_volume": 0.5,
      "recommended_tracks": [
        {"name": "推荐曲目风格描述", "source": "建议来源", "duration_sec": 30}
      ],
      "sound_effects": [
        {"time_sec": 2.5, "effect": "音效名称", "type": "环境/动作/情绪/转场", "volume": 0.8, "trigger": "触发原因"},
        {"time_sec": 5.0, "effect": "音效名称", "type": "环境/动作/情绪/转场", "volume": 0.6, "trigger": "触发原因"}
      ]
    }
  ],
  "overall_music_style": "全片配乐风格指导",
  "volume_mix_notes": "音量混音建议"
}"""

BGM_GENERATE_PROMPT = """你是一位金牌影视配乐师，精通用音乐操控观众情绪。根据短剧片段信息，生成专业BGM配乐方案。

【专业规范】
1. 配器与情绪匹配：压抑用低音提琴+氛围垫，催泪用钢琴+小提琴，爆发用重型鼓点+铜管，温馨用原声吉他+口哨。
2. BPM：慢板60-80(悲伤/浪漫)，中板90-110(日常)，快板120-140(紧张)，极快150+(爆发)。
3. 力度曲线：p→mf→f→pp，与剧情呼吸同步。
4. 和声：小调显悲伤/压抑/悬疑，大调显明朗/温暖/胜利；不和谐音程制造紧张。
5. 卡点：在反转/打脸/表白/惊吓点设计精准音效（见情绪→音效映射）。
6. 全片风格统一，不同场景是变奏而非换曲风。

返回JSON格式（不要markdown代码块）：
{
  "bgm_style": "古风/流行/电子/古典/民谣/氛围/影视原声",
  "mood": "大气/温馨/紧张/悲壮/欢快/悬疑/浪漫/史诗/轻松/伤感",
  "duration_sec": 120,
  "tempo": "慢板/中板/快板",
  "bpm": 90,
  "instrumentation": "具体乐器组成（如：钢琴+小提琴+大提琴+低音提琴）",
  "harmony": "和声进行建议（如：小调i-VI-III-VII循环）",
  "dynamics": "力度变化曲线（如：p渐强到f再收pp）",
  "intensity": "渐强/平稳/起伏",
  "key_points": [{"time_sec": 5.0, "event": "卡点事件", "sfx": "音效建议"}],
  "recommendation": "建议使用的音乐风格描述",
  "bgm_tracks": [],
  "overall_music_style": "全片配乐风格指导",
  "volume_mix_notes": "音量混音建议"
}"""


class BGMAgent(BaseAgent):
    """BGM配乐智能体：剧情自动匹配BGM、卡点音效"""

    name = "BGM配乐智能体"
    description = "剧情自动匹配BGM、卡点音效"
    version = "1.0.0"

    def analyze_and_match(self, scenes: List[Dict], story_mood: str) -> AgentResult:
        """分析剧情情绪并匹配BGM"""
        start = time.time()
        try:
            scenes_info = [
                {
                    "name": s.get("scene_name", f"场景{i+1}"),
                    "description": s.get("description", ""),
                    "mood": (
                        s.get("mood", {}).get("atmosphere", "平静")
                        if isinstance(s.get("mood"), dict)
                        else "平静"
                    ),
                    "duration_sec": s.get("duration_sec", 30),
                }
                for i, s in enumerate(scenes)
            ]
            user_prompt = f"""故事整体情绪：{story_mood}
场景列表：
{json.dumps(scenes_info, ensure_ascii=False, indent=2)}"""
            result = self._call_llm_json(BGM_MATCH_PROMPT, user_prompt, retries=2)
            # 格式对齐前端
            if isinstance(result, dict):
                if "bgm_list" not in result:
                    tracks = result.get("bgm_tracks", [])
                    result["bgm_list"] = []
                    for t in tracks:
                        recs = t.get("recommended_tracks", [])
                        for r in recs:
                            result["bgm_list"].append(
                                {
                                    "name": r.get(
                                        "name", t.get("bgm_genre", "配乐")
                                    ),
                                    "mood": t.get("bgm_style", "中性"),
                                    "scene": t.get("scene_name", ""),
                                    "url": None,
                                }
                            )
                        if not recs:
                            result["bgm_list"].append(
                                {
                                    "name": f"{t.get('bgm_genre','配乐')} - {t.get('bgm_style','')}",
                                    "mood": t.get("mood", "中性"),
                                    "scene": t.get("scene_name", ""),
                                    "url": None,
                                }
                            )
            # P0-2: 不再兜底生成假数据，空结果直接失败
            if not result or not isinstance(result, dict):
                return AgentResult(success=False, error="BGM匹配失败: LLM返回空结果")
            if "bgm_tracks" not in result or not result.get("bgm_tracks"):
                return AgentResult(success=False, error="BGM匹配失败: LLM未生成bgm_tracks")
            # 确保 bgm_list
            if "bgm_list" not in result:
                result["bgm_list"] = [
                    {"name": t.get("bgm_genre", "配乐"), "mood": t.get("bgm_style", "中性"),
                     "scene": t.get("scene_name", ""), "url": None}
                    for t in result.get("bgm_tracks", [])
                ]
            return AgentResult(
                data=result, duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"BGM匹配失败: {e}")
            return AgentResult(success=False, error=str(e)[:200])

    def generate_bgm(self, shots: list, config: dict = None, director_task: str = '', script_text: str = '') -> AgentResult:
        """根据shots信息生成BGM配乐元数据"""
        start = time.time()
        try:
            # 汇总shots信息用于LLM推理
            plot_summary = "; ".join(
                [
                    s.get("description", s.get("dialogue", s.get("content", "")))[:100]
                    for s in shots[:10]
                ]
            )
            mood_hint = config.get("mood", "中性") if config else "中性"
            duration_total = sum(
                s.get("duration", s.get("duration_sec", 5)) for s in shots
            )
            dialogue_hint = "; ".join(
                [
                    s.get("dialogue", s.get("text", ""))[:80]
                    for s in shots[:5]
                    if s.get("dialogue") or s.get("text")
                ]
            )

            script_context = (script_text or "")[:800]
            # 读取导演分镜里的配乐方向（sound_design），让BGM遵循导演意图
            director_music_hints = []
            for s in shots:
                sd = s.get("sound_design", "")
                if sd and ("配乐" in sd or "音乐" in sd or "BGM" in sd.upper() or "氛围" in sd):
                    scene = s.get("scene", "")
                    director_music_hints.append(f"[{scene}] {sd[:80]}")
            director_music = "\n".join(director_music_hints[:8]) if director_music_hints else ""

            user_prompt = f"""短剧片段内容概要：{plot_summary[:500]}
对话片段：{dialogue_hint[:300]}
提示情绪：{mood_hint}
总时长估算：{duration_total}秒
剧本正文（剧情上下文，配乐需贴合其整体氛围与情绪走向）：
{script_context}
"""
            if director_music:
                user_prompt += f"""
导演分镜配乐方向（必须遵循，与画面情绪一致）：
{director_music}
"""
            user_prompt += """
请生成BGM配乐方案。"""
            result = self._call_llm_json(BGM_GENERATE_PROMPT, user_prompt, retries=2)

            # P0-2: LLM失败不兜底，直接返回失败
            if not result or not isinstance(result, dict):
                return AgentResult(success=False, error="BGM生成失败: LLM返回空结果")

            # 兼容前端 bgm_list
            if "bgm_list" not in result:
                result["bgm_list"] = [
                    {
                        "name": result["recommendation"],
                        "mood": result["mood"],
                        "scene": "",
                        "url": None,
                    }
                ]

            # P0-2: 不再用 ffmpeg 合成假 BGM，依赖真实音乐 API
            # [Mureka接入] 用 LLM 元数据生成真实 BGM 音乐文件
            try:
                from services.mureka_provider import mureka
                # 用 LLM 给的风格/情绪/配器拼成 mureka prompt
                style_hint = result.get("bgm_style", "") or result.get("mood", "")
                instr = result.get("instrumentation", "")
                mureka_prompt = f"{style_hint}, {instr}, background music, cinematic" if style_hint else "cinematic background music, soft, ambient"
                gender = "female" if "温馨" in str(style_hint) or "浪漫" in str(style_hint) else ""
                mr = mureka.generate_instrumental(prompt=mureka_prompt, gender=gender, max_wait=240)
                if mr.get("success") and mr.get("audio_url"):
                    bgm_url = mr["audio_url"]
                    # 填入 bgm_list 和顶层 url
                    for item in result.get("bgm_list", []):
                        if not item.get("url"):
                            item["url"] = bgm_url
                    result["bgm_url"] = bgm_url
                    result["audio_url"] = bgm_url
                    logger.info(f"[BGM] ✅ Mureka 生成成功: {bgm_url[:80]}")
                else:
                    logger.warning(f"[BGM] Mureka 生成失败(保留元数据): {mr.get('error','')[:100]}")
            except Exception as me:
                logger.warning(f"[BGM] Mureka 接入异常(保留元数据): {str(me)[:100]}")

            return AgentResult(
                data=result, duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"BGM生成失败: {e}")
            return AgentResult(success=False, error=str(e)[:200])

    # P0-2: _gen_real_bgm 已删除 — 不再用 ffmpeg 正弦波合成假 BGM，依赖真实音乐 API

    def add_sound_effects(self, storyboard_shots: List[Dict]) -> AgentResult:
        """为分镜添加卡点音效"""
        start = time.time()
        try:
            prompt = f"""你是一位拟音师。为以下分镜添加卡点音效。

分镜列表：
{json.dumps(storyboard_shots, ensure_ascii=False, indent=2)}

返回JSON格式：
{{
  "shots_with_sfx": [
    {{
      "shot_num": 1,
      "sound_effects": [
        {{"time_sec": 0.5, "effect": "音效名称", "trigger": "动作/转场/情绪点", "volume": 0.8}}
      ]
    }}
  ]
}}"""
            result = self._call_llm_json(
                "你只返回JSON。",
                prompt,
            )
            return AgentResult(
                data=result, duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"添加音效失败: {e}")
            return AgentResult(success=False, error=str(e))

    def run(
        self, action: str = "match", **kwargs
    ) -> AgentResult:
        # 接收导演配乐指令
        _dt = kwargs.get("director_tasks", kwargs.get("params", {}).get("director_tasks", {}))
        _da = kwargs.get("director_analysis", kwargs.get("params", {}).get("director_analysis", {}))
        _bgm_hint = ""
        if isinstance(_dt, dict) and _dt.get("bgm_music"):
            _bgm_hint = str(_dt["bgm_music"])
        if not _bgm_hint and isinstance(_da, dict) and _da.get("emotional_curve"):
            _bgm_hint = "情绪：" + str(_da["emotional_curve"])
        if _bgm_hint:
            import logging; logging.getLogger(__name__).info(f"[BGM] 导演配乐指令: {_bgm_hint[:80]}...")

        if action in ("match", "generate"):
            return self.analyze_and_match(
                kwargs.get("scenes", []),
                kwargs.get("mood", "中性"),
            )
        elif action == "generate_bgm":
            return self.generate_bgm(
                kwargs.get("shots", []),
                kwargs.get("config"),
                script_text=kwargs.get("script_text", ""),
            )
        elif action == "sfx":
            return self.add_sound_effects(kwargs.get("shots", []))
        return AgentResult(success=False, error=f"未知动作: {action}")

    def execute(self, shots: list, config: dict = None, **kwargs):
        """唯一入口：生成BGM"""
        return self.generate_bgm(shots, config)

