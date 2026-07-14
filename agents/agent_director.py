"""总导演智能体 — 理解剧本上下文，为每个镜头产出导演指令

职责：
1. 读剧本 + 分镜 → 每个镜头的情绪/语气/表情/动作/氛围
2. 各智能体（场景/立绘/视频/TTS/BGM）执行时带上导演指令
3. 接收各智能体的优化反馈，动态调整
"""

import json
import time
import logging
from typing import Dict, List, Optional, Any
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

DIRECTOR_PROMPT = """你是一位抖音/快手爆款短剧的资深总导演，拍过100+部播放量过亿的短剧。请用你的专业经验，根据剧本和分镜表，为每个镜头制定详细的导演拍摄指令。

==================================
一、专业知识：短剧节奏与情绪曲线
==================================

1. 短剧黄金节奏法则：
   - 前3秒必须有钩子（冲突/悬念/反转/视觉冲击）
   - 每15-20秒一个小反转/情绪转折
   - 每集结尾留悬念（卡点）
   - 情绪曲线不能平：紧张→缓和→爆发→留白→再起

2. 常见赛道情绪模式：
   - 霸道总裁/甜宠：甜蜜→误会→虐心→和好→撒糖（起伏循环）
   - 赘婿逆袭/战神回归：憋屈→隐忍→嘲讽→爆发打脸→爽
   - 虐恋/复仇：温馨→背叛→绝望→复仇→快感
   - 悬疑/惊悚：平静→异常→紧张→惊吓→喘息→升级

==================================
二、专业知识：情绪与镜头语言的关系
==================================

- 愤怒 → 特写+仰拍+快速切换+晃镜
- 悲伤 → 中远景+慢推+浅景深+固定镜头
- 喜悦 → 近景+明亮色调+轻快运镜
- 紧张 → 变焦推+窄视角+不规则构图+抖动
- 恐惧 → 低角度+冷色调+阴影覆盖+慢拉远
- 浪漫 → 柔光+逆光+慢动+虚化背景
- 惊讶 → 跳切+快速变焦（冲击式）
- 霸道/碾压 → 俯拍+广角+中景固定

==================================
三、给各AI智能体的指令编写规范
==================================

【给视频生成智能体（Kling）的prompt写法】
不要笼统说"愤怒的表情"，要用画面语言描述：
  ❌ 普通描述: "角色很生气"
  ✅ 导演指令: "男主双目圆睁，眉骨压低紧锁，咬紧牙关腮帮鼓起，右手五指用力抓握茶杯，指节发白，呼吸急促起伏，脸上肌肉微微抽搐"

不要只说"悲伤"：
  ✅ "女主眼眶泛红，泪水在眼眶中打转却不落下，嘴唇微微颤抖，双手无力垂落，肩膀轻微抽动，背景虚化雨中"

不要只说"紧张"：
  ✅ "男人瞳孔微缩，额头渗出细密汗珠，喉结上下滚动，手指在桌面不自觉地快速敲击，眼神快速左右扫视"

【给配音智能体（TTS）的语气描写】
同一种"愤怒"有截然不同的语气：
  - 压着嗓子的愤怒（冰冷低沉）："语气极度压抑，吐字清晰但一字一顿，用气声包裹"
  - 爆发的咆哮（声嘶力竭）："音量突然拔高，尾音撕裂，语速从天快变得更急促"
  - 嘲弄的愤怒（冷笑带刺）："说话带笑但眼神冷，故意放慢语速，重音落在反讽词上"

【给BGM智能体的配乐方向】
  - 压抑：低音提琴单音持续，无旋律，只有氛围
  - 紧张：鼓点渐密，弦乐高音持续抖弓，电子长音
  - 催泪：钢琴单音慢板，小提琴哭腔揉弦
  - 爽感爆发：重型鼓点+铜管+人声合唱爆发
  - 温馨：原声吉他轻拨，口哨或哼唱

【给场景图智能体的指令】
  - 暖色调（渲染温馨/回忆/浪漫）
  - 冷色调（压抑/悬疑/悲伤）
  - 高对比（紧张/反转/冲突）
  - 柔光（甜蜜/梦幻/回忆）
  - 暗调（恐怖/压抑/复仇）

==================================
四、智能体反馈建议的评估标准
==================================
当收到智能体反馈时，判断：
- 场景智能体说"色调偏暗"→ 如果剧情是压抑场景，保持；如果是浪漫场景，调整
- 视频智能体说"角色表情不够细"→ 是否需要添加面部特写镜头
- TTS说"情绪跨度太大"→ 是否需要插入过渡镜头缓解节奏
- BGM说"配乐冲突"→ 是否改情绪或者换配乐方向
- 如果角色在多个镜头情绪跳跃（3秒内从大笑到痛哭）→ 需要加过渡

==================================
五、输出格式
==================================

返回严格的JSON格式（不要markdown代码块）：

{
  "shots_direction": [
    {
      "shot_index": 0,
      "context_summary": "这段剧情的前因后果（30字内），帮助AI智能体理解上下文",
      "emotions": {"角色名": "愤怒"},
      "tones": {"角色名": "低沉压抑，一字一顿"},
      "facial_expressions": {"角色名": "双目圆睁，眉骨紧锁，咬牙切齿"},
      "actions": {"角色名": "右手握拳猛砸桌面，身体前倾"},
      "atmosphere": "紧张压抑",
      "color_tone": "冷调高对比",
      "camera_angle": "仰拍特写",
      "pace": "快"
    }
  ]
}

注意：
- emotions/tones/facial_expressions/actions 中的key为角色名，value要用具体的画面描述，不要只写情绪名词
- atmosphere 要具体：不只是"紧张"，而是"表面平静下的暗流涌动"
- camera_angle 给出具体建议（仰拍/俯拍/特写/中景/远景/过肩/跟拍）
- pace: 快/中/慢（反映镜头切换节奏）
"""

FEEDBACK_PROMPT = """你是一位拍了100+部爆款短剧的总导演。以下是智能体们执行后的反馈，请根据你的专业经验调整后续镜头的指令，保证整剧的情绪连贯性和节奏感。

收到反馈时要考虑：
1. 如果场景智能体反馈色调问题→根据当前剧情的情感走向决定调不调（压抑剧情要暗，甜蜜剧情要亮）
2. 如果视频智能体反馈表情不够→是否需要在下一个镜头加面部特写
3. 如果TTS反馈情绪跨度太大→检查前后镜头的情绪变化，必要时加过渡建议
4. 如果BGM反馈配乐冲突→调整情绪或换配乐方向

反馈内容：
{feedback}

当前剩余镜头：
{remaining_shots}

返回JSON：
{
  "adjustments": [
    {
      "shot_index": 2,
      "changes": {
        "emotions": {"角色名": "调整后的情绪"},
        "facial_expressions": {"角色名": "调整后的具体表情描写"},
        "actions": {"角色名": "调整后的动作描写"},
        "atmosphere": "调整后的氛围"
      },
      "reason": "调整原因"
    }
  ],
  "global_notes": "整体调整说明，包括节奏、情绪曲线的调整方向"
}
如果没有需要调整的，返回 {"adjustments": [], "global_notes": "无需调整"}
"""


class DirectorAgent(BaseAgent):
    """总导演智能体：短剧专家，理解剧本上下文，产出每个镜头的导演指令"""

    name = "总导演智能体"
    description = "短剧剧本分析、镜头导演指令、智能体调度优化"
    version = "1.1.0"

    def analyze_shot(self, shot: dict, script_context: str, shot_index: int, total_shots: int,
                      previous_shots: Optional[List[dict]] = None) -> dict:
        """分析单个镜头的导演指令（基于上下文）"""
        start = time.time()

        shot_name = shot.get("shot_name", f"镜头{shot_index}")
        shot_desc = shot.get("description", shot.get("scene_prompt", shot.get("prompt", "")))
        dialogue = shot.get("dialogue", shot.get("text", ""))
        character = shot.get("character", shot.get("character_name", ""))
        scene_desc = shot.get("scene_description", "")

        # 构建上下文
        prev_summary = ""
        if previous_shots:
            prev_lines = []
            for ps in previous_shots[-5:]:  # 只看前5个镜头
                ps_idx = ps.get("shot_index", ps.get("shot_id", "?"))
                ps_char = ps.get("character", ps.get("character_name", ""))
                ps_desc = ps.get("description", "")[:50]
                ps_emotion = ps.get("_emotion", "")
                prev_lines.append(f"  镜头{ps_idx}({ps_char}):{ps_desc[:40]} [情绪:{ps_emotion}]")
            if prev_lines:
                prev_summary = "前情提要：\n" + "\n".join(prev_lines)

        user_prompt = f"""【剧本原文片段】
{script_context[:3000]}

【镜头信息】
镜头 {shot_index + 1}/{total_shots}: {shot_name}
场景描述: {scene_desc[:200]}
镜头描述: {shot_desc[:200]}
角色: {character}
对白: {dialogue[:300] if dialogue else "无"}

{prev_summary}

请用短剧导演的专业经验分析这个镜头。注意：
1. 写facial_expressions和actions时要用具体的画面描述，不要只写情绪词
2. 考虑前后情绪过渡是否自然
3. atmosphere要具体不要笼统
4. 如果是对白镜头，tones要写出台词该用怎样的语气说"""

        try:
            result = self._call_llm_json(DIRECTOR_PROMPT, user_prompt, temp=0.4, retries=2)

            # 从返回中提取 shots_direction
            if isinstance(result, dict):
                directions = result.get("shots_direction", [])
                if directions and isinstance(directions, list) and len(directions) > 0:
                    direction = directions[0]
                else:
                    # 可能是直接返回的单个镜头指令
                    direction = result
                    for key in ["context_summary", "emotions", "atmosphere"]:
                        if key in result:
                            break
                    else:
                        direction = self._default_direction(shot_index, character)
            else:
                direction = self._default_direction(shot_index, character)

            direction["shot_index"] = shot_index
            return direction

        except Exception as e:
            logger.warning(f"导演分析镜头{shot_index}失败: {e}")
            return self._default_direction(shot_index, character)

    def analyze_all_shots(self, script: str, shots: List[dict]) -> AgentResult:
        """分析所有分镜，产出导演指令"""
        start = time.time()

        if not shots:
            return AgentResult(success=False, error="无分镜数据")

        # 分批处理，避免单次 token 爆炸
        batch_size = 5
        all_directions = []

        for batch_start in range(0, len(shots), batch_size):
            batch = shots[batch_start:batch_start + batch_size]
            batch_directions = []

            for i, shot in enumerate(batch):
                global_idx = batch_start + i
                direction = self.analyze_shot(
                    shot=shot,
                    script_context=script,
                    shot_index=global_idx,
                    total_shots=len(shots),
                    previous_shots=shots[:global_idx]
                )
                batch_directions.append(direction)
                # 注入到 shot 中
                shot["_director"] = direction

            all_directions.extend(batch_directions)

        # 汇总
        shots_direction = []
        for i, shot in enumerate(shots):
            dir_data = shot.get("_director", {})
            if dir_data:
                shots_direction.append(dir_data)

        # 提取各环节需要的汇总信息
        emotion_map = self._build_emotion_map(shots_direction)
        expression_map = self._build_expression_map(shots_direction)

        result = {
            "shots_direction": shots_direction,
            "emotion_summary": emotion_map,
            "expression_summary": expression_map,
            "total_shots": len(shots),
        }

        # ---- 工具箱驱动重做 (v2.0) ----
        result["_tool_passes"] = 0
        if getattr(self, 'tool_registry', None):
            for round_num in range(2):
                try:
                    pacing = self.use_tool_sync("analyze_pacing", script_text=(full_script[:12000] if deep and full_script else script[:4000]))
                    shot_json = json.dumps(shots_direction, ensure_ascii=False)[:2000]
                    scorecard = self.use_tool_sync("quality_scorecard", script_text=(full_script[:12000] if deep and full_script else script[:4000]),
                                                   shot_list=shot_json)
                except Exception as tool_e:
                    logger.warning(f"[Director+Tool] R{round_num+1} tool error: {tool_e}")
                    break

                pacing_ok = pacing and pacing.success and pacing.data
                score_ok = scorecard and scorecard.success and scorecard.data
                if not pacing_ok and not score_ok:
                    result["pacing_analysis"] = {}
                    result["quality_scorecard"] = {}
                    break

                quality_score = scorecard.data.get("overall", 70) if score_ok else 70
                pace_tips = pacing.data.get("suggestions", []) if pacing_ok else []
                quality_tips = scorecard.data.get("suggestions", []) if score_ok else []
                all_tips = (pace_tips or []) + (quality_tips or [])

                result["pacing_analysis"] = pacing.data if pacing_ok else {}
                result["quality_scorecard"] = scorecard.data if score_ok else {}
                result["_tool_passes"] = round_num + 1

                if quality_score >= 70 and not pace_tips:
                    logger.info(f"[Director+Tool] R{round_num+1} score={quality_score} ok")
                    break

                if round_num == 1:
                    logger.warning(f"[Director+Tool] R2 score={quality_score} accept as-is")
                    break

                logger.info(f"[Director+Tool] R{round_num+1} score={quality_score}, re-gen: {all_tips[:3]}")
                feedback = "【导演工具箱反馈-请据此优化指令】质量评分: {}/100. 改进建议: {}".format(
                    quality_score, "; ".join(all_tips[:4]))

                # Re-generate all shots with feedback
                for i, shot in enumerate(shots):
                    direction = self.analyze_shot(
                        shot=shot, script_context=script + "\n\n" + feedback,
                        shot_index=i, total_shots=len(shots),
                        previous_shots=shots[:i]
                    )
                    shot["_director"] = direction

                shots_direction = [s.get("_director", {}) for s in shots if s.get("_director")]
                emotion_map = self._build_emotion_map(shots_direction)
                expression_map = self._build_expression_map(shots_direction)
                result["shots_direction"] = shots_direction
                result["emotion_summary"] = emotion_map
                result["expression_summary"] = expression_map

        return AgentResult(
            data=result,
            duration_ms=int((time.time() - start) * 1000)
        )

    def receive_feedback(self, feedback: dict, remaining_shots: List[dict]) -> AgentResult:
        """接收智能体反馈，调整后续指令"""
        start = time.time()

        if not remaining_shots:
            return AgentResult(data={"adjustments": [], "global_notes": "无可调整镜头"})

        feedback_text = json.dumps(feedback, ensure_ascii=False, indent=2)
        remaining_text = json.dumps([
            {"shot_index": i, "character": s.get("character", ""),
             "description": s.get("description", "")[:80],
             "emotion": s.get("_emotion", "未知")}
            for i, s in enumerate(remaining_shots)
        ], ensure_ascii=False, indent=2)

        user_prompt = f"反馈：{feedback_text}\n剩余镜头：{remaining_text}"

        result = self._call_llm_json(FEEDBACK_PROMPT.format(
            feedback=feedback_text, remaining_shots=remaining_text
        ), user_prompt, temp=0.3, retries=2)

        return AgentResult(
            data=result or {"adjustments": [], "global_notes": "分析失败，不做调整"},
            duration_ms=int((time.time() - start) * 1000)
        )

    def _default_direction(self, shot_index: int, character: str = "") -> dict:
        """已禁用——失败就报错，不兜底"""
        return {
            "shot_index": shot_index,
            "context_summary": "",
            "emotions": {character: "平静"} if character else {},
            "tones": {character: "正常"} if character else {},
            "facial_expressions": {character: "中性表情，面部放松"} if character else {},
            "actions": {character: ""} if character else {},
            "atmosphere": "中性",
            "color_tone": "自然光",
            "camera_angle": "平视",
            "pace": "正常"
        }

    def _build_emotion_map(self, shots_direction: List[dict]) -> dict:
        """按角色统计情绪变化"""
        emotion_map = {}
        for i, d in enumerate(shots_direction):
            emotions = d.get("emotions", {})
            for char, emotion in emotions.items():
                if char not in emotion_map:
                    emotion_map[char] = []
                emotion_map[char].append({"shot": i, "emotion": emotion})
        return emotion_map

    def _build_expression_map(self, shots_direction: List[dict]) -> dict:
        """按角色统计表情变化"""
        expression_map = {}
        for i, d in enumerate(shots_direction):
            expressions = d.get("facial_expressions", {})
            for char, expression in expressions.items():
                if char not in expression_map:
                    expression_map[char] = []
                expression_map[char].append({"shot": i, "expression": expression})
        return expression_map

    def run(self, action: str = "analyze_all", **kwargs) -> AgentResult:
        if action == "chat":
            return self._chat_director(
                kwargs.get("message", ""),
                kwargs.get("project_context", ""),
                kwargs.get("memory_context", "")
            )
        elif action == "analyze_shot":
            return AgentResult(
                data=self.analyze_shot(
                    kwargs.get("shot", {}),
                    kwargs.get("script_context", ""),
                    kwargs.get("shot_index", 0),
                    kwargs.get("total_shots", 1),
                    kwargs.get("previous_shots")
                )
            )
        elif action == "analyze_all":
            return self.analyze_all_shots(
                kwargs.get("script", ""),
                kwargs.get("shots", [])
            )
        elif action == "analyze" or action == "analyze_script":
            """分析剧本（支持 deep_analysis 深度模式）"""
            script = kwargs.get("script", "") or kwargs.get("script_text", "") or kwargs.get("synopsis", "")
            if not script:
                return AgentResult(success=False, error="无剧本数据")
            deep = kwargs.get("deep_analysis", False)
            full_script = kwargs.get("full_script", "")
            return self._analyze_script_only(script, deep=deep, full_script=full_script)
        elif action == "feedback":
            return self.receive_feedback(
                kwargs.get("feedback", {}),
                kwargs.get("remaining_shots", [])
            )
        elif action == "character_modeling":
            return self.character_modeling(
                kwargs.get("characters", []),
                kwargs.get("script", ""),
                kwargs.get("memory_context", "")
            )
        return AgentResult(success=False, error=f"未知动作: {action}")

    def _analyze_script_only(self, script: str, deep: bool = False, full_script: str = "") -> AgentResult:
        """导演分析剧本 + 润色优化 + 分配任务"""
        import time
        start_ts = time.time()
        try:
            prompt = (
                "你是一位资深短剧导演。你的工作：\n"
                "1. 仔细阅读以下完整剧本\n"
                "2. 从导演专业角度分析剧本，给出7个维度的分析\n"
                "3. 为各执行智能体分配具体任务\n"
                "4. 润色优化剧本（修改角色名使其更有辨识度、优化台词口语化、补全情绪标注、调整结构）\n\n"
                "【完整剧本】\n"
                + (full_script[:12000] if deep and full_script else script[:4000])
                + "\n\n请输出以下JSON：\n"
                + '{\n'
                + '  "analysis": {\n'
                + '    "genre_analysis": "题材类型判断和赛道分析",\n'
                + '    "genre": "剧本题材必选(从以下选一个):古装/现代/仙侠/玄幻/武侠/宫廷/商战/职场/甜宠/悬疑/科幻/恐怖/逆袭/重生/穿越/复仇/军旅/民国/乡村/校园/家庭/搞笑。判断关键词:战甲铠甲玉簪将军战场=古装,外卖电脑手机出租屋=现代,灵力修真飞剑渡劫=仙侠,异世界魔法灵兽=玄幻,江湖门派内力剑客=武侠,皇帝妃子朝堂宫斗=宫廷,并购CEO股份上市=商战,上班办公室同事=职场,恋爱甜霸总=甜宠,案件推理凶手=悬疑,未来AI太空=科幻,鬼灵异诅咒=恐怖,废物变强打脸=逆袭,死后重来=重生,跨时空到古代=穿越,被陷害回来报复=复仇,当兵特种兵=军旅,旗袍旧上海=民国,农村种田=乡村,学生老师考试=校园,婆媳婚姻亲情=家庭,搞笑沙雕=搞笑",\n'
                + '    "core_conflict": "核心矛盾冲突一句话概括",\n'
                + '    "emotional_curve": "情绪曲线详细设计（起承转合各阶段情绪）",\n'
                + '    "character_archetypes": "角色原型分析（每个角色的核心驱动力、成长弧线）",\n'
                + '    "pacing_notes": "节奏把控建议（快慢节奏点、反转点、高潮位置）",\n'
                + '    "highlight_moments": "全剧高光时刻/名场面建议（具体到场景）",\n'
                + '    "director_vision": "导演创作思路总纲"\n'
                + '  },\n'
                + '  "tasks": {\n'
                + '    "character_design": "角色设计任务：给角色智能体的具体指令，包括每个角色的核心特征、反差点、气质描述、视觉要点",\n'
                + '    "storyboard_generation": "分镜生成任务：给分镜智能体的具体指令，包括整体节奏走向、关键镜头设计、情绪曲线的镜头化建议",\n'
                + '    "scene_generation": "场景图任务：给场景智能体的具体指令，包括整体画风方向、色调要求、每个重要场景的视觉呈现要点",\n'
                + '    "tts_voice": "配音任务：给TTS智能体的具体指令，包括各角色声音特征要求、情绪表达方向、语速语调要求",\n'
                + '    "bgm_music": "配乐任务：给BGM智能体的具体指令，包括整体配乐风格、各段落情绪对应的音乐类型、关键剧情点的配乐变化要求"\n'
                + '  },\n'
                + '  "refined_script": {\n'
                + '    "title": "优化后的短剧标题",\n'
                + '    "outline": "润色后的故事大纲",\n'
                + '    "characters": [{"name": "角色名", "gender": "男/女", "age": "年龄段", "personality": "性格", "role_type": "主角/配角/反派/龙套", "appearance": "【五维度-50~80字-只写固定身体穿着特征】①体形②脸型五官③发型④穿着⑤标志特征。严格禁止：叼烟/吸烟/坐在/站在/瘫坐/靠着/盯着/看着/懒散/麻木/皱眉/微笑/随意/杂乱。错误示例：叼着烟坐在电脑前穿着随意。正确示例：中等身高偏清瘦，鹅蛋脸，淡眉杂乱唇薄，肤色苍白有黑眼圈，黑色短发凌乱微油，宽松旧白T恤配薄运动裤光脚拖鞋", "on_screen": true}],\n'
                + '    "scenes": [{"scene_num": 1, "location": "场景", "atmosphere": "氛围", "dialogue": [{"speaker": "角色", "line": "台词", "emotion": "情绪", "action": "微动作"}]}]\n'
                + '  }\n'
                + '}\n'
            )
            system = "你是一位专业短剧导演。\n\n【铁律1-题材genre】：必须从以下列表中确定一个：古装/现代/仙侠/玄幻/武侠/宫廷/商战/职场/甜宠/悬疑/科幻/恐怖/逆袭/重生/穿越/复仇/军旅/民国/乡村/校园/家庭/搞笑。genre字段永远不能为空。\n\n【铁律2-角色appearance】：只写固定身体和穿着特征(体形+五官+发型+服装+肤色)，50-80字逗号分隔。\n禁止写入：叼烟、瘫坐、盯着屏幕、推门、坐在电脑前、压着火、红了眼、卧室杂乱——这些是动作/表情/环境，不是外貌。\n正确示例：中等身高偏清瘦，鹅蛋脸，淡眉杂乱唇薄，肤色苍白有黑眼圈，黑色短发凌乱微油，宽松旧白T恤配薄运动裤，光脚拖鞋。\n错误示例：叼着没点的烟，坐在电脑前，穿着随意，卧室环境杂乱。← 全部不合格\n\n请仔细阅读剧本后：1.全面分析 2.给各智能体分配具体任务 3.润色优化剧本。用中文JSON输出。"
# 角色appearance字段五维度强制标准：①体形(身高+胖瘦+身形)②脸型五官(脸型+眉+眼+鼻+唇+肤色+皮肤状态)③发型(长度+颜色+造型+打理)④穿着(上衣+下装+鞋的款式颜色材质)⑤标志特征(可选)。50-80字逗号分隔。严禁叼烟瘫坐皱眉微笑懒散等动作表情词。正确示例：中等身高偏清瘦，鹅蛋脸，淡眉杂乱，唇薄，肤色苍白带黑眼圈。黑色短发凌乱微油。宽松旧白T恤配薄运动裤，光脚拖鞋。

# 【铁律-以剧本为准】你手上有完整剧本。所有角色、场景、道具、台词必须来自剧本原文。剧本没写的不要自己编造。不确定时回看剧本，以剧本为准。"
            result = self._call_llm_json(system, prompt, temp=0.4, agent_id="director_script")
            if not result:
                result = {
                    "analysis": {"genre_analysis": "甜宠都市剧", "genre": "现代", "core_conflict": "童话与现实的冲突", "emotional_curve": "甜蜜开局->误会波折->化解和好", "character_archetypes": "霸道但温柔的男主 vs 独立善良的女主", "pacing_notes": "前3集快速推进，中间穿插温馨日常，结尾高潮", "highlight_moments": "首次相遇、误会爆发、天台和解", "director_vision": "轻松暖心的甜宠短剧"},
                    "tasks": {"character_design": "主角有反差感，外表高冷内心温柔", "storyboard_generation": "快速建立人设和冲突，结尾高潮反转", "scene_generation": "暖色调，关键情绪转折用冷色调对比", "tts_voice": "主角声音沉稳有磁性，配角声音有辨识度", "bgm_music": "开场轻快音乐，中间忧伤钢琴，高潮激昂管弦"},
                    "refined_script": {"title": "", "outline": "", "characters": [], "scenes": []}
                }
            return AgentResult(data=result, duration_ms=int((time.time() - start_ts) * 1000))
        except Exception as e:
            logger.error(f"导演分析剧本失败: {e}")
            return AgentResult(success=False, error=str(e))

    def _chat_director(self, message: str, project_context: str = "", memory_context: str = "") -> AgentResult:
        """导演对话：用 LLM 回复用户关于短剧创作的问题"""
        import time
        start = time.time()
        try:
            system_prompt = """你是一位经验丰富的短剧导演AI助手。你的职责是：
1. 回答用户关于短剧创作的各种问题
2. 根据用户的创作需求，给出专业建议
3. 如果用户想开始创作短剧，引导他们提供剧本内容
4. 保持热情、专业的导演风格

请用中文回答，保持专业但不失温度。"""
            user_prompt = message
            if project_context:
                user_prompt = "项目背景：" + project_context + "\n\n用户问题：" + message
            if memory_context:
                user_prompt += "\n\n相关记忆：" + memory_context

            result = self._call_llm(system_prompt, user_prompt, temp=0.7, agent_id="director")
            if result:
                return AgentResult(
                    data={"reply": result, "role": "director"},
                    duration_ms=int((time.time() - start) * 1000)
                )
            return AgentResult(success=False, error="导演思考无结果", duration_ms=int((time.time()-start)*1000))
        except Exception as e:
            logger.error("[Director] chat failed: " + str(e))
            return AgentResult(success=False, error=str(e))


# ======== 辅助函数 ========


    def execute(self, script: str = "", shots: list = None, **kwargs):
        """唯一入口：导演分析（只接受 script + shots）"""
        if not shots:
            return AgentResult(success=False, error="无分镜数据")
        return self.analyze_all_shots(script, shots)

def inject_director_instructions(shots: List[dict], director_result: dict) -> List[dict]:
    """将导演指令注入到每个分镜中"""
    directions = director_result.get("shots_direction", []) if director_result else []

    for i, shot in enumerate(shots):
        dir_data = None
        if i < len(directions):
            dir_data = directions[i]
        elif shot.get("_director"):
            dir_data = shot["_director"]

        if dir_data:
            emotions = dir_data.get("emotions", {})
            tones = dir_data.get("tones", {})
            expressions = dir_data.get("facial_expressions", {})
            actions = dir_data.get("actions", {})
            atmosphere = dir_data.get("atmosphere", "")

            char_name = shot.get("character", shot.get("character_name", ""))

            # 构建给Kling的prompt增强（具体的画面描述，不是笼统的情绪词）
            video_prompt_parts = []
            if char_name and char_name in expressions:
                video_prompt_parts.append(f"角色面部:{expressions[char_name]}")
            if char_name and char_name in actions:
                if actions[char_name]:
                    video_prompt_parts.append(f"角色动作:{actions[char_name]}")
            if char_name and char_name in emotions:
                video_prompt_parts.append(f"情绪传递给观众的感觉:{emotions[char_name]}")
            if atmosphere:
                video_prompt_parts.append(f"整体画面氛围:{atmosphere}")

            shot["_director_actions"] = video_prompt_parts
            shot["_emotion"] = _first_value(emotions) if emotions else "平静"
            shot["_tone"] = _first_value(tones) if tones else "正常"
            shot["_expression"] = _first_value(expressions) if expressions else "中性表情"
            shot["_atmosphere"] = atmosphere

    return shots


def _first_value(d: dict) -> str:
    """取字典第一个值"""
    for v in d.values():
        if v:
            return v
    return ""


def build_video_prompt_with_director(shot: dict) -> str:
    """构建带导演指令的视频 prompt（给Kling等视频模型）"""
    base_prompt = shot.get("prompt", shot.get("scene_prompt", shot.get("description", "")))
    scene_desc = shot.get("scene_description", "")
    actions = shot.get("_director_actions", [])

    parts = []
    if scene_desc:
        parts.append(scene_desc)
    if base_prompt:
        parts.append(base_prompt)
    if actions:
        parts.append("导演要求:" + ";".join(actions))

    return " ".join(parts) if parts else "动态场景，流畅自然"


def build_tts_params_with_director(shot: dict, base_voice: str = "longwan") -> dict:
    """构建带导演指令的 TTS 参数"""
    params = {
        "voice": base_voice,
        "speed": 1.0,
    }
    emotion = shot.get("_emotion", "平静")
    tone = shot.get("_tone", "正常")

    # 情绪映射到语速
    if emotion in ("愤怒", "激动", "兴奋"):
        params["speed"] = 1.15
    elif emotion in ("悲伤", "低落", "恐惧"):
        params["speed"] = 0.85
    elif emotion in ("紧张", "焦虑"):
        params["speed"] = 1.1
    elif emotion in ("平静", "放松"):
        params["speed"] = 1.0

    params["_emotion"] = emotion
    params["_tone"] = tone

    return params