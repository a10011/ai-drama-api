"""
agent_script v3.0 — 剧本智能体（重写）
原则：
  1. 用户自写剧本永远不修改，直接原样透传
  2. AI生成剧本只生成一次，工具反馈仅作诊断建议
  3. 不分叉路径，单一清晰流程
  4. 缓存基于 premis+genre 双重 key，避免串味
"""

import json, time, logging
from typing import Optional, Dict, Any, List
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 风格词库
# ═══════════════════════════════════════════════════════════════

GENRE_KEYWORDS = {
    "都市": {"vibe": "快节奏、利己主义、现实感强", "dialogue": "句式简短、带网络语、带手机/工作话题"},
    "甜宠": {"vibe": "温暖、少女心、暧昧氛围", "dialogue": "撒娇、暗示、害羞、幼稚斗嘴、语气词多"},
    "虐恋": {"vibe": "压抑、心痛、畸形关系", "dialogue": "简短沉默、情绪撕扯、内心独白穿插"},
    "职场": {"vibe": "竞争、阶层冲突、潜规则", "dialogue": "双关暗示、试探、话里有话、信息交换"},
    "校园": {"vibe": "青春、单纯、暗恋、成长", "dialogue": "羞涩、直接、带校园黑话、打闹"},
}

STYLE_HINTS = {
    "都市": "剧情要贴近当下社会话题，台词带调侃和讽刺，节奏明快，每句都有信息量",
    "甜宠": "制造心动感，暧昧但不过界，用细节互动（递水/擦嘴/对视）代替直白告白",
    "虐恋": "制造心痛和代入感，误会和身份差距推动剧情，说话留白，用沉默渲染情绪",
    "职场": "突出阶层差异和利益博弈，对话含双关、潜台词、权力压制",
    "校园": "青春真实感，纯真但不幼稚，用暗恋和误会推动，尽量少用成人化情感表达",
}

# ═══════════════════════════════════════════════════════════════
# 检测用户自写剧本的标记
# ═══════════════════════════════════════════════════════════════

USER_SCRIPT_MARKERS = [
    '📍', '【', '---', '第', '场',
    '时长：', '人物：', '场景', '镜头', '旁白', 'BGM',
    '：\n',  # 角色名:对话格式
]

USER_SCRIPT_MIN_LEN = 200     # 超过此长度才判定为自写剧本
USER_SCRIPT_MIN_MARKERS = 3   # 至少命中3个标记

# ═══════════════════════════════════════════════════════════════
# LLM Prompts
# ═══════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """你是一位金牌短剧编剧，深耕竖屏短剧赛道5年，作品累计播放破百亿。你精通都市、甜宠、虐恋、职场、校园、古装、仙侠、悬疑、逆袭等全赛道，深谙竖屏短剧的节奏法则与情绪工程。

═══════════════════════════════════
一、竖屏短剧的结构理论
═══════════════════════════════════

1. 三幕微结构（每集1-3分钟）：
   - 第一幕（前15秒）：钩子开场 + 人物困境亮相
   - 第二幕（中段）：冲突升级 + 反转叠加 + 情绪过山车
   - 第三幕（末15秒）：阶段性爆发 + 卡点悬念（让观众必须看下一集）

2. 节拍表（Beats）——每集至少包含：
   - 钩子beat：开篇3秒内抛出冲突/悬念/反常
   - 升级beat：困境加剧，主角被推到悬崖边
   - 转折beat：意料之外情理之中（身份反转/真相揭露/态度转变）
   - 高潮beat：情绪顶点（打脸/表白/决裂/爆发）
   - 卡点beat：结尾留扣（新危机出现/真相半露/关系反转预告）

3. 情绪曲线（不能平）：
   - 钩子高开 → 短暂缓和（让观众喘口气） → 持续爬升 → 高潮爆发 → 卡点急转
   - 每15-20秒一个微转折，避免长时间平情绪
   - 情绪对比越强烈越上头：憋屈→爽、甜蜜→虐、平静→惊吓

═══════════════════════════════════
二、人物弧线与辨识度
═══════════════════════════════════

1. 主角弧线（单集内闭环）：
   - 起始状态（弱势/被压/隐忍）→ 触发事件 → 觉醒/反击 → 阶段性胜利
   - 即使是甜宠，也要有"主角主动做选择"的时刻，不能全程被动

2. 角色辨识度三件套（每个主要角色必备）：
   - 标志性口头禅或语气词（重复3次以上形成记忆点）
   - 标志性动作/习惯（推眼镜、咬唇、敲桌、攥拳）
   - 性格反差点（霸道总裁其实怕虫、灰姑娘其实跆拳道黑带）

3. 反派/对手设计：
   - 不要脸谱化纯坏，给一个合理动机（嫉妒/利益/误会）
   - 反派的智商决定主角的光环，反派越强打脸越爽

═══════════════════════════════════
三、赛道专属情绪模式
═══════════════════════════════════

- 霸总/甜宠：甜蜜→误会→虐心→和好→撒糖（起伏循环），用细节互动代替直白告白
- 赘婿逆袭/战神回归：憋屈→隐忍→嘲讽→爆发打脸→爽（憋屈要够久，打脸才够爽）
- 虐恋/复仇：温馨→背叛→绝望→复仇→快感，用沉默和留白渲染情绪
- 悬疑/惊悚：平静→异常→紧张→惊吓→喘息→升级，信息逐步释放
- 古装/仙侠：凡根受辱→机缘觉醒→实力碾压→扬眉吐气，阶级跨越的爽感

═══════════════════════════════════
四、台词与钩子技巧
═══════════════════════════════════

1. 台词原则：
   - 口语化、短句为主（单句不超过20字），符合竖屏快节奏
   - 每句台词都要有"信息增量"或"情绪增量"，不写废话
   - 潜台词比直白更有张力（说一半、藏一半、反着说）

2. 钩子类型（开场必用其一）：
   - 冲突钩子：直接撞见冲突现场
   - 悬念钩子：抛出反常现象/未解之谜
   - 反转钩子：开篇就是意料之外的场景
   - 视觉钩子：强烈画面冲击（雨夜/豪车/跌倒/对峙）

3. 卡点技巧（结尾必留扣）：
   - 新角色突然登场/新信息半露/关系即将反转/更大危机降临
   - 卡点要让观众"啊就停在这？"——情绪悬在半空

═══════════════════════════════════
五、合规红线
═══════════════════════════════════
禁用暴力血腥、低俗色情、政治敏感、侵权IP名称、过度消极价值观。可以写冲突但不能教唆犯罪。

═══════════════════════════════════
六、国内外市场适配
═══════════════════════════════════
根据题材/用户意图判断目标市场并适配：
- 国内竖屏：1-3分钟/集，极快节奏，赘婿逆袭/霸总甜宠/重生复仇等题材，爽感曲线，中文台词
- 海外短剧：30秒-2分钟，更碎片化，狼人/吸血鬼/Alpha霸总/罪案题材，强视觉冲击，英文台词，回避文化梗/谐音梗
- 通用：9:16竖屏、钩子开场、卡点留扣
未指定市场时默认国内风格。

═══════════════════════════════════
七、输出规范
═══════════════════════════════════
严格按用户给的JSON模板输出，不输出任何JSON以外的内容（不要markdown代码块、不要解释）。
情绪字段限定：开心/生气/难过/委屈/温柔/紧张/激动/无奈/羞涩/冷漠。
scenes 3-8场，每场2-8句台词，单句2-8秒。"""

GEN_TEMPLATE = """题材类型：{genre}
用户梗概/创意：{premise}

{style_hint}

输出完整剧本JSON：
{{
  "title": "短剧标题",
  "genre": "判断题材类型（限：都市/古装/仙侠/悬疑/喜剧/甜宠/科幻/逆袭/现代甜宠/古装权谋）",
  "outline": "故事大纲（300-500字）",
  "characters": [
    {{"name": "角色名", "gender": "男/女", "age": "年龄段", "personality": "性格标签", "role_type": "主角/配角"}}
  ],
  "scenes": [
    {{
      "scene_num": 1,
      "location": "场景地点",
      "atmosphere": "环境氛围",
      "dialogue": [
        {{"speaker": "角色名", "line": "台词", "emotion": "情绪", "action": "微动作"}}
      ]
    }}
  ],
  "total_duration_hint": "预估时长",
  "hook": "结尾悬念"
}}

规则：scenes 3-8场，每场2-8句，情绪限 开心/生气/难过/委屈/温柔/紧张/激动/无奈/羞涩/冷漠"""

EXTRACT_PROMPT = """从剧本提取角色，返回JSON：
{{
  "characters": [
    {{"name": "角色名", "gender": "male/female", "role": "主角/配角/反派", "personality": "3-5个性格词(如:冷静果断+外冷内热+重情重义)", "appearance": "外貌描述40-80字，用于AI生图。必须包含:发型+五官+衣着+气质+标志特征。例如:白衣胜雪长发束冠，剑眉星目鼻梁高挺，腰间悬三尺青锋，气质清冷出尘如谪仙", "age": "年龄段(青年/中年/老年/少年)", "description": "一句话角色定位(15字内)"}}
  ]
}}
最多6个角色，按重要度排序。appearance要非常具体，AI生图全靠它"""

OPTIMIZE_PROMPT = """分析剧本问题并优化，返回JSON：
{{
  "issues": [{{"type": "节奏|冲突|人物|台词|结构", "location": "位置", "suggestion": "建议", "priority": "high/medium/low"}}],
  "optimized_script": "优化后剧本",
  "hook_suggestion": "结尾悬念",
  "summary": "总结(20字内)"
}}"""


# ═══════════════════════════════════════════════════════════════
# ScriptAgent v3.0
# ═══════════════════════════════════════════════════════════════

class ScriptAgent(BaseAgent):
    name = "剧本智能体"
    description = "梗概扩写、剧本生成、角色抽取、剧本优化"
    version = "3.0.0"

    # ── 主入口：生成/扩写剧本 ──────────────────────────

    def create_script(self, premise: str, genre: str = "都市", project_id: str = "") -> AgentResult:
        """
        剧本生成单一流程：
          1. 检测用户自写剧本 → 原样透传
          2. 查缓存 → 返回（附诊断建议）
          3. LLM生成 → 缓存 → 返回（附诊断建议）
        """
        start = time.time()
        try:
            # Step 1: 用户自写剧本直通
            result = self._try_user_script(premise, genre, start)
            if result:
                return result

            # Step 2: 缓存命中
            result = self._try_cache(premise, genre)
            if result:
                return AgentResult(data=result, duration_ms=0)

            # Step 3: LLM生成
            result = self._generate_via_llm(premise, genre, project_id)

            duration = int((time.time() - start) * 1000)
            return AgentResult(data=result, duration_ms=duration)

        except Exception as e:
            logger.error(f"剧本生成失败: {e}")
            return AgentResult(success=False, error=str(e))

    # ── 角色提取 ──────────────────────────────────────

    def extract_characters(self, script: str, project_id: int = None) -> AgentResult:
        start = time.time()
        try:
            prompt = f"剧本内容：\n{script[:4000]}\n\n{EXTRACT_PROMPT}"
            result = self._call_llm_json(
                "你是一个专业剧本分析助手，严格按照JSON格式输出。",
                prompt, temp=0.3, agent_id="script"
            )
            chars = result.get("characters", result.get("data", result.get("items", []))) if isinstance(result, dict) else []
            if project_id and chars:
                self._save_characters_to_db(project_id, chars)
            logger.info(f"[extract] result keys: {list(result.keys()) if isinstance(result, dict) else type(result)} | chars: {len(chars)}")
            return AgentResult(
                data={"characters": chars},
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"角色提取失败: {e}")
            return AgentResult(success=False, error=str(e))

    # ── 剧本优化 ──────────────────────────────────────

    def optimize_script(self, script: str) -> AgentResult:
        start = time.time()
        try:
            result = self._call_llm_json(OPTIMIZE_PROMPT, f"需要优化的剧本：\n{script[:6000]}",
                                         temp=0.4, agent_id="script")
            return AgentResult(data=result, duration_ms=int((time.time() - start) * 1000))
        except Exception as e:
            logger.error(f"剧本优化失败: {e}")
            return AgentResult(success=False, error=str(e))

    # ── 兼容旧接口 ──────────────────────────────────────

    def expand_outline(self, premise: str, genre: str = "都市") -> AgentResult:
        return self.create_script(premise, genre)

    def handle_full_script(self, script_text: str, title: str = "", genre: str = "都市") -> AgentResult:
        """用户提交完整剧本 — 原样保留，只提取角色"""
        start = time.time()
        chars = []
        try:
            chars_result = self.extract_characters(script_text)
            if chars_result.success:
                chars = chars_result.data.get("characters", [])
        except Exception as ex_: logger.warning(f"[agent_script]  {ex_}")
        return AgentResult(data={
            "title": title or "未命名短剧",
            "script": script_text,
            "outline": script_text[:300],
            "characters": chars,
            "scenes": []
        }, duration_ms=int((time.time() - start) * 1000))

    # ═════════════════════════════════════════════════════════════
    # 内部方法：三步流程
    # ═════════════════════════════════════════════════════════════

    def _try_user_script(self, premise: str, genre: str, start: float) -> Optional[AgentResult]:
        """检测是否为用户自写剧本 → 原样透传"""
        if not premise or len(premise) <= USER_SCRIPT_MIN_LEN:
            return None
        hits = sum(1 for m in USER_SCRIPT_MARKERS if m in premise)
        if hits < USER_SCRIPT_MIN_MARKERS:
            return None
        logger.info(f"[Script] 检测到用户自写剧本({len(premise)}字, {hits}标记) → 透传")
        data = {
            "genre": genre,
            "script": premise,
            "scenes": [{"title": "用户剧本", "dialogue": premise}],
            "characters": [],
            "warnings": [],
            "_user_script": True,
        }
        duration = int((time.time() - start) * 1000)
        return AgentResult(data=data, duration_ms=duration)

    def _try_cache(self, premise: str, genre: str) -> Optional[Dict[str, Any]]:
        """查缓存并附加工具诊断"""
        from .result_cache import get as cache_get
        cached = cache_get(premise, genre, "script_v3", ttl=86400 * 365)
        if not cached or not isinstance(cached.get("data"), dict):
            return None
        logger.info(f"[Script] 缓存命中: premise={premise[:40]}")
        result = cached["data"]
        result = self._attach_advisory_tools(result)
        return result

    def _generate_via_llm(self, premise: str, genre: str, project_id: str = "") -> Dict[str, Any]:
        """LLM生成 → 缓存 → 附加诊断"""
        style_info = GENRE_KEYWORDS.get(genre, {})
        style_prompt = (
            f"【{genre}赛道要点】\n"
            f"风格基调：{style_info.get('vibe','')}\n"
            f"对话风格：{style_info.get('dialogue','')}\n"
            f"{STYLE_HINTS.get(genre, '')}"
        )
        user_prompt = GEN_TEMPLATE.format(genre=genre, premise=premise, style_hint=style_prompt)
        result = self._call_llm_json(SYSTEM_PROMPT, user_prompt, temp=0.7, agent_id="script", retries=2)

        # 格式兜底
        if isinstance(result, dict):
            if "scenes" not in result:
                result["scenes"] = [{"scene_num": 1, "dialogue": [
                    {"speaker": "", "line": result.get("outline", "")[:200], "emotion": "neutral", "action": ""}
                ]}]
            if "characters" not in result:
                result["characters"] = []
            result["genre"] = result.get("genre", genre)
            result["project_id"] = str(project_id) if project_id else ""
            result["warnings"] = []

        # Auto-detect: update context with LLM-detected genre
        detected_genre = result.get("genre", "")
        if detected_genre and detected_genre != genre:
            try:
                from routers.context_agent import update as ctx_update
                if project_id:
                    ctx_update(project_id, genre=detected_genre)
                    logger.info(f"[Script] 题材自动检测: {genre} -> {detected_genre}")
                    try:
                        import sqlite3
                        conn = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
                        conn.execute("UPDATE projects SET genre=? WHERE id=?", (detected_genre, project_id))
                        conn.commit()
                        conn.close()
                    except Exception:
                        pass
            except Exception as ex_:
                logger.debug(f"[Script] 题材更新: {ex_}")

        # 写入缓存
        try:
            from .result_cache import set as cache_set
            cache_set(premise, genre, "script_v3", {"data": result})
            logger.info(f"[Script] 已缓存: premise={premise[:40]}")
        except Exception as e:
            logger.warning(f"[Script] 缓存写入失败: {e}")

        # 记录经验
        try:
            from services.experience_engine import experience_engine
            if experience_engine:
                experience_engine.log_generation(
                    "agent_script", "create", genre, premise,
                    json.dumps(result, ensure_ascii=False)[:3000],
                    genres=genre, success=bool(result.get("scenes")), effectiveness=4
                )
        except Exception as ex_: logger.warning(f"[agent_script]  {ex_}")

        # 附加工具诊断
        result = self._attach_advisory_tools(result)
        return result

    # ═════════════════════════════════════════════════════════════
    # 工具诊断（仅附加建议，不重写）
    # ═════════════════════════════════════════════════════════════

    def _attach_advisory_tools(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """运行工具检查，结果仅附加为诊断建议，不触发重写"""
        if not getattr(self, 'tool_registry', None):
            return result
        try:
            text = json.dumps(result.get("scenes", []), ensure_ascii=False)
            if not text or len(text) < 50:
                text = json.dumps(result, ensure_ascii=False)
            check = self._try_tool_redo([
                {"name": "tropes_checker", "params": {"script_text": text}, "weight": 1.0},
                {"name": "audience_simulator", "params": {"script_text": text}, "weight": 1.0},
            ], min_score=40)
            result["_tool_score"] = check.get("score", 0)
            result["_tool_suggestions"] = check.get("feedback", "")
            if check.get("tool_results"):
                tr = check["tool_results"]
                if len(tr) > 0:
                    result["tropes_analysis"] = tr[0].get("data", {})
                if len(tr) > 1:
                    result["audience_sim"] = tr[1].get("data", {})
        except Exception as e:
            logger.warning(f"[Script][Tool] 诊断异常: {e}")
        return result

    # ═════════════════════════════════════════════════════════════
    # 工具方法
    # ═════════════════════════════════════════════════════════════

    def _save_characters_to_db(self, project_id: int, chars: list):
        try:
            import sqlite3
            conn = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/characters.db")
            c = conn.cursor()
            for ch in chars:
                c.execute(
                    "INSERT INTO characters (project_id, name, gender, role, description, importance, created_at) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (project_id, ch.get("name", "未知"), ch.get("gender", ""),
                     ch.get("role", "extra"), ch.get("description", ""),
                     80 if ch.get("role") == "主角" else 50, time.time())
                )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"角色存库失败: {e}")

    # ═════════════════════════════════════════════════════════════
    # 统一调度入口
    # ═════════════════════════════════════════════════════════════

    def run(self, action: str = "expand", **kwargs) -> AgentResult:
        task = kwargs.get("task", {})
        if isinstance(task, dict):
            kwargs.update(task)

        if action in ("expand", "generate_script", "create"):
            return self.create_script(
                kwargs.get("premise", kwargs.get("prompt", kwargs.get("script", ""))),
                kwargs.get("genre", "都市"),
                kwargs.get("project_id", "")
            )
        elif action == "extract":
            return self.extract_characters(
                kwargs.get("script", kwargs.get("text", "")),
                kwargs.get("project_id")
            )
        elif action == "polish":
            return self.handle_full_script(
                kwargs.get("script_text", ""),
                kwargs.get("title", kwargs.get("user_title", "")),
                kwargs.get("genre", "都市")
            )
        elif action == "optimize":
            return self.optimize_script(kwargs.get("script", ""))
        elif action == "analyze_script":
            return self._analyze_script(**kwargs)
        else:
            return AgentResult(success=False, error=f"未知action: {action}")

    def _analyze_script(self, **kwargs) -> AgentResult:
        """分析用户输入的短文本/梗概，提取角色和题材"""
        start = time.time()
        script_text = kwargs.get("script_text", "")
        try:
            analyzed = self._call_llm_json(
                "你是一个短剧编剧，根据用户输入的剧本构思，提取角色和题材信息。",
                (
                    "根据以下剧本构思，严格返回JSON（不要markdown代码块）：\n\n"
                    '{\n'
                    '  "genre": "题材类别（都市/古装/仙侠/悬疑/喜剧）",\n'
                    '  "characters": [\n'
                    '    {"name": "角色名", "gender": "男/女", "age": "年龄段", "personality": "性格关键词", "role_type": "主角/反派/配角"}\n'
                    '  ],\n'
                    '  "episodes": [{"episode": 1, "title": "标题", "summary": "20字摘要"}]\n'
                    '}\n\n'
                    f"剧本构思：\n{script_text}\n\n"
                    "注意：characters至少2个角色，必须包含主角，role_type为 主角/反派/配角 之一"
                ),
                temp=0.6, agent_id="script", retries=2
            )
            if isinstance(analyzed, dict):
                chars = analyzed.get("characters", [])
                for c in (chars or []):
                    if isinstance(c, dict) and not c.get("description"):
                        c["description"] = (c.get("personality", "") + "。外貌特征待AI生成。")
                episodes = analyzed.get("episodes", [])
                return AgentResult(data={
                    "genre": analyzed.get("genre", "都市"),
                    "characters": chars,
                    "episodes": episodes,
                    "tasks": [
                        {"agent": "script", "label": "📝 剧本创作", "desc": f"基于梗概创作完整剧本，共{max(len(episodes or []),4)}集"},
                        {"agent": "storyboard", "label": "🎬 分镜生成", "desc": "将剧本转化为分镜镜头"},
                        {"agent": "scene", "label": "🖼️ 场景绘图", "desc": "为分镜生成场景图"},
                        {"agent": "character", "label": "🎭 角色设计", "desc": f"设计{len(chars or [])}个角色形象"},
                        {"agent": "dubbing", "label": "🎙️ 配音合成", "desc": "合成AI对白配音"},
                        {"agent": "bgm", "label": "🎵 BGM配乐", "desc": "生成背景音乐"},
                        {"agent": "composite", "label": "🎞️ 视频合成", "desc": "合成所有素材为短剧"},
                    ]
                }, duration_ms=int((time.time() - start) * 1000))
            return AgentResult(success=False, error="解析失败")
        except Exception as e:
            logger.error(f"analyze_script 失败: {e}")
            return AgentResult(success=False, error=str(e))
