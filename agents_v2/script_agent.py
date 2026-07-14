# -*- coding: utf-8 -*-
"""
Hermes ScriptAgent — 总导演：长剧本 + 多季规划
- 第一轮：输出季规划（角色谱系、集纲、悬念/伏笔）
- 第二轮：将第一集扩展为完整分场JSON（兼容下游 agents）
"""
import json, logging, re
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel
from core.safety_filter import clean_text

logger = logging.getLogger(__name__)

GENRE_TEMPLATES = {
    "古装": "古风古韵，画面讲究构图，文戏对白精致，武戏写意不写实",
    "现代都市": "都市情感，写实生活化，对白口语化，节奏明快",
    "仙侠": "玄幻飘逸，大开大合，意境描写为主",
    "悬疑": "气氛悬疑，进度紧张，反转多",
    "喜剧": "轻松搞笑，梗点多，节奏快",
    "科幻": "未来世界观，科技概念自洽，视觉想象力突出",
    "民国": "年代质感，旗袍/长衫/老上海，家国情怀或谍战",
    "甜宠": "男女主互动自然，糖点密集，情感升温有层次",
    "逆袭": "底层→翻身，爽点密集，每集一个小高潮一个大钩子",
}

SEASON_SYSTEM_PROMPT = """你是一位顶级短剧总编剧。你的任务是为一整套季播短剧做顶层设计。

核心要求：
1. 剧本不是单集，而是一整季（8-24集），每集3-5分钟
2. 必须有明显的"下一季空间"——未解决的悬念、待展开的人物线、更大的世界观
3. 角色要立体——每个主要角色有独立的成长弧线
4. 节奏感——每集要有钩子开头+反转中段+悬念结尾
5. 世界观要有足够深度，能支撑多季展开

你的输出必须遵循严格的JSON结构（见格式化要求）。
不要输出任何非JSON内容，只输出JSON。"""

SEASON_USER_PROMPT_TEMPLATE = """题材: {genre}
风格: {style_hint}
标题: {title}
概要: {synopsis}

请按以下JSON结构输出一季完整的剧本顶层设计：

```json
{{
  "format": "season",
  "season": 1,
  "total_episodes": 数字,
  "season_title": "本季副标题",
  "season_subtitle": "一句话Slogan",
  "season_arc": "本季核心冲突，200-300字",
  "world_building": "世界观设定，包括时代/地点/特有规则等，150-200字",
  "theme": "核心主题",
  "tone": "整体情绪基调",

  "characters": [
    {{
      "name": "角色名",
      "role": "主角/配角/反派",
      "gender": "男/女",
      "age": "年龄",
      "appearance": "外貌特征，30-50字",
      "personality": "性格描述，50-80字",
      "background": "背景故事，50-80字",
      "growth_arc": "本季成长线",
      "voice_style": "音色风格",
      "season2_hint": "下一季该角色的可能发展线"
    }}
  ],

  "episode_outlines": [
    {{
      "ep": 1,
      "title": "第一集标题",
      "duration_seconds": 180-300,
      "summary": "本集概要，80-120字",
      "hook": "开场3-15秒钩子",
      "climax": "本集最高潮",
      "cliffhanger": "结尾悬念引向下一集",
      "key_character_moments": "关键角色瞬间"
    }}
  ],

  "season_structure": {{
    "act_1": "第一幕建立世界引入冲突",
    "act_2": "第二幕冲突升级角色成长",
    "act_3": "第三幕推向高潮为下一季埋伏笔"
  }},

  "character_relationships": [
    {{
      "pair": "A×B",
      "relationship": "关系描述",
      "arc": "关系变化线"
    }}
  ],

  "next_season_hooks": [
    "伏笔1：必须具体，说明第二季如何展开",
    "伏笔2",
    "伏笔3"
  ],

  "next_season_setup": "第二季大体走向，100-150字",

  "narrative_threads": {{
    "main_plot": "主线剧情概要",
    "subplots": ["副线1", "副线2"]
  }}
}}
```

【安全红线】
- 不允许血腥暴力画面描写
- 不允许色情/软色情
- 死亡场景用"落幕""远行""沉睡"代替
- 敏感题材用架空处理

请直接输出完整JSON，不要其他文字。"""


EXPAND_SYSTEM_PROMPT = """你是一位专业的短剧编剧，将顶层设计扩展为第一集的可执行剧本。

输出要求：
- 输出严格的JSON格式，包含角色和本集所有场景的分镜头
- 每个场景包含多个镜头（shots），每个镜头有镜头类型、画面描述、台词、时长
- 总时长控制在3-5分钟（180-300秒）
- 保持原设计的所有角色特征、对白风格不变
- 场景描述体现视觉可执行性，让AI视频模型能理解
- 每个镜头时长必须是整数秒钟

只输出JSON，不输出任何其他内容。"""

EXPAND_USER_PROMPT_TEMPLATE = """请将以下季设计中的第一集扩展为完整剧本JSON。

【季设计JSON】
{season_json}

【第一集大纲】
集号: {ep_number}
标题: {ep_title}
摘要: {ep_summary}
开场钩子: {ep_hook}
高潮: {ep_climax}
结尾悬念: {ep_cliffhanger}

【风格参考】
{style_hint}

【知识参考】
{knowledge}

请输出以下JSON结构，直接输出，不要markdown包裹：

```json
{{
  "format": "episode",
  "season": 1,
  "episode": 1,
  "episode_title": "...",
  "duration_seconds": 总时长秒数,
  "genre": "...",

  "characters": [
    {{
      "name": "角色名",
      "role": "主角/配角",
      "gender": "男/女",
      "age": "年龄",
      "appearance": "外貌特征",
      "personality": "性格",
      "background": "背景",
      "voice_style": "音色"
    }}
  ],

  "scenes": [
    {{
      "scene_id": 1,
      "location": "场景地点",
      "time": "白天/黑夜/黄昏/黎明",
      "atmosphere": "场景氛围",
      "shots": [
        {{
          "shot_id": 1,
          "type": "远景/全景/中景/近景/特写",
          "content": "画面细节描述，30-60字，为AI视频模型提供视觉指导",
          "dialogue": "台词（如有）",
          "subtitle": "字幕文本",
          "duration_seconds": 镜头时长秒数,
          "emotion": "情绪基调",
          "camera_movement": "镜头运动方式"
        }}
      ]
    }}
  ],

  "title": "完整剧名",
  "summary": "本集概要"
}}
```

关键要求：
1. 场景至少3-5个
2. 每个场景至少2-5个镜头
3. 总时长180-300秒
4. 每个镜头content必须详细——环境、角色动作、表情、道具等
5. 对白符合角色性格
6. 安全红线：不允许血腥、色情、敏感政治内容"""


class ScriptAgent(AgentV3):
    name = "script"
    max_workers = 16

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        title = data.get("title", "")
        genre = data.get("genre", "")
        synopsis = data.get("synopsis", "")
        full_script = data.get("full_script", data.get("script_text", ""))
        knowledge = task.get("_knowledge", "")
        pipeline_id = task.get("pipeline_id", "")

        style_hint = GENRE_TEMPLATES.get(genre, "")

        # Pass-through: 已有完整剧本直接透传，不用生成
        if full_script and len(full_script.strip()) > 50:
            logger.info("[ScriptAgent] pass-through: full_script=" + str(len(full_script)) + " chars")
            return {"success": True, "data": {"script_text": full_script, "title": title, "genre": genre, "pipeline_id": pipeline_id}}

        # ── 第一步：生成季设计 ──
        season_prompt = SEASON_USER_PROMPT_TEMPLATE.format(
            genre=genre,
            style_hint=style_hint,
            title=title,
            synopsis=synopsis or "根据题材自由创作",
        )

        season_result = self.call_with_safety_retry(
            task.get("model", "agnes-2.0-flash"), 3,
            UnifiedModel.llm,
            prompt=season_prompt,
            system=SEASON_SYSTEM_PROMPT,
            max_tokens=8192,
            timeout=180,
        )
        season_text = season_result.get("text", "")
        season_text = clean_text(season_text)

        # 解析季设计 JSON
        season_plan = self._safe_json_parse(season_text)
        if not season_plan or not season_plan.get("characters") or not season_plan.get("episode_outlines"):
            logger.warning(f"[ScriptAgent] 季设计JSON解析失败，降级为单集模式")
            return self._legacy_fallback(title, genre, synopsis, knowledge, pipeline_id, style_hint)

        # ── 第二步：扩展第一集 ──
        ep1_outline = season_plan["episode_outlines"][0] if season_plan.get("episode_outlines") else {}
        ep1_prompt = EXPAND_USER_PROMPT_TEMPLATE.format(
            season_json=json.dumps(season_plan, ensure_ascii=False, indent=2),
            ep_number=ep1_outline.get("ep", 1),
            ep_title=ep1_outline.get("title", title),
            ep_summary=ep1_outline.get("summary", ""),
            ep_hook=ep1_outline.get("hook", ""),
            ep_climax=ep1_outline.get("climax", ""),
            ep_cliffhanger=ep1_outline.get("cliffhanger", ""),
            style_hint=style_hint,
            knowledge=knowledge[:3000] if knowledge else "无",
        )

        ep1_result = self.call_with_safety_retry(
            task.get("model", "agnes-2.0-flash"), 3,
            UnifiedModel.llm,
            prompt=ep1_prompt,
            system=EXPAND_SYSTEM_PROMPT,
            max_tokens=8192,
            timeout=180,
        )
        ep1_text = ep1_result.get("text", "")
        ep1_text = clean_text(ep1_text)
        ep1_data = self._safe_json_parse(ep1_text)

        # ── 合并输出 ──
        chars = season_plan.get("characters", [])
        scenes = []
        if ep1_data:
            scenes = ep1_data.get("scenes", ep1_data.get("shots", []))
            # 展平场景中的 shots
            flat_shots = []
            for sc in scenes:
                shs = sc.get("shots", [])
                for sh in shs:
                    sh["scene_id"] = sc.get("scene_id", 0)
                    sh["location"] = sc.get("location", "")
                    flat_shots.append(sh)
            if flat_shots:
                scenes = flat_shots

        result = {
            "success": True,
            "pipeline_id": pipeline_id,
            "title": title,
            "genre": genre,

            # 季设计
            "format": "season",
            "season_plan": season_plan,

            # 第一集剧本（兼容下游）
            "script_text": json.dumps(ep1_data if ep1_data else season_plan, ensure_ascii=False),
            "characters": chars,
            "scenes": scenes,
            "shots": scenes,

            # 季相关元数据
            "_episode_expanded": bool(ep1_data),
            "_total_episodes": season_plan.get("total_episodes", 0),
            "_next_season_hooks": season_plan.get("next_season_hooks", []),
        }

        self.log_asset("script", meta={
            "pipeline_id": pipeline_id,
            "title": title,
            "episodes": season_plan.get("total_episodes", 0),
        })
        return result

    def _safe_json_parse(self, text: str) -> dict | None:
        """解析可能带 markdown 包裹的 JSON"""
        if not text or not text.strip():
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # 提取 code block
        m = re.search(r'```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        # 提取第一个大括号
        m = re.search(r'(\{.*\})', text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        return None

    def _legacy_fallback(self, title, genre, synopsis, knowledge, pipeline_id, style_hint):
        """降级：单集短剧模式（当季设计 JSON 解析失败时）"""
        user_prompt = (
            f"题材: {genre}\n"
            f"标题: {title}\n"
            f"风格: {style_hint}\n"
            f"概要: {synopsis}\n\n"
            "请输出一个完整的短剧剧本JSON。包含characters和scenes字段，"
            "每个scene包含多个shots。本集时长约180秒。\n\n"
            "JSON结构：\n"
            "{\n"
            '  "characters": [{"name":,"role":,"gender":,"age":,"appearance":,"personality":,"background":,"voice_style":}],\n'
            '  "scenes": [{"scene_id":,"location":,"time":,"shots":[{"shot_id":,"type":,"content":,"dialogue":,"duration_seconds":}]}],\n'
            '  "title": ""\n'
            "}"
        )
        system_prompt = "你是一位专业的短剧编剧。输出严格的JSON剧本。"
        result = self.call_with_safety_retry(
            task.get("model", "agnes-2.0-flash"), 3,
            UnifiedModel.llm,
            prompt=user_prompt,
            system=system_prompt,
            max_tokens=8192,
            timeout=120,
        )
        script_text = result.get("text", "")
        if not script_text:
            import logging; logging.getLogger(__name__).info("[ScriptAgent] returning data keys: " + str(list(locals().get("result",{}).keys()))); return {"success": False, "error": "剧本生成失败", "pipeline_id": pipeline_id}
        script_text = clean_text(script_text)
        parsed = self._safe_json_parse(script_text) or {}
        chars = parsed.get("characters", [])
        scenes = parsed.get("scenes", [])
        flat_shots = []
        for sc in scenes:
            shs = sc.get("shots", [])
            for sh in shs:
                sh["scene_id"] = sc.get("scene_id", 0)
                sh["location"] = sc.get("location", "")
                flat_shots.append(sh)

        self.log_asset("script", meta={"pipeline_id": pipeline_id, "title": title})
        import logging; logging.getLogger(__name__).info("[ScriptAgent] returning data keys: " + str(list(locals().get("result",{}).keys()))); return {
            "success": True,
            "pipeline_id": pipeline_id,
            "format": "single",
            "script_text": script_text,
            "characters": chars,
            "scenes": flat_shots or scenes,
            "shots": flat_shots or scenes,
            "title": title,
            "genre": genre,
            "season_plan": None,
            "_total_episodes": 1,
            "_next_season_hooks": [],
        }

    # ── 继承的 AgentV3 记忆/进化方法 ──

    def _check_memory(self, task: dict) -> dict | None:
        data = task.get("data", {})
        genre = data.get("genre", "")
        title = data.get("title", "")
        if not genre or not title:
            return None
        return self.memory.lookup("script", genre, title)

    def _find_similar_memory(self, task: dict) -> list:
        genre = task.get("data", {}).get("genre", "")
        if not genre:
            return []
        similars = self.memory.find_similar(genre, limit=3)
        return [s["value"] for s in similars if s.get("value", {}).get("success")]

    def _save_memory(self, task: dict, result: dict):
        if not result.get("success"):
            return
        genre = result.get("genre", "")
        title = result.get("title", "")
        self.memory.save(result, "script", genre, title, tags=genre)

    def _evolution_check(self, task: dict) -> list:
        genre = task.get("data", {}).get("genre", "")
        tips = []
        try:
            similar = self.memory.find_similar(genre, limit=5)
            for s in similar:
                val = s.get("value", {})
                if isinstance(val, dict) and not val.get("success", True):
                    tips.append("上次同类失败: " + str(val.get("error", "?")))
        except Exception:
            pass
        return tips
