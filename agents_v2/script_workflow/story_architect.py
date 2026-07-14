"""
Hermes StoryArchitect — 故事架构师
三幕结构、情节点设计、节奏把控
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位顶级故事架构师（Story Architect），专精于短剧结构设计。

能力：
1. 三幕/五幕结构搭建，确保起承转合完整
2. 每集情节点设计，保证冲突层层递进
3. 节奏标定，确保每集有钩子+反转+悬念
4. 多季规划，预留伏笔和展开空间

输出必须是严格的 JSON，不要输出任何非 JSON 文字。"""

USER_PROMPT_TEMPLATE = """创作任务：
题材: {genre}
标题: {title}
概要: {synopsis}
风格: {style_hint}

{creative_focus}

请输出一季完整的剧本顶层设计，严格按以下 JSON 结构：

```json
{{
  "format": "season",
  "season": 1,
  "total_episodes": 12,
  "season_title": "本季副标题",
  "season_subtitle": "一句话 Slogan",
  "season_arc": "本季核心冲突，200-300字",
  "world_building": "世界观设定，150-200字",
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
      "season2_hint": "下一季可能发展线"
    }}
  ],
  "episode_outlines": [
    {{
      "ep": 1,
      "title": "标题",
      "duration_seconds": 180,
      "summary": "80-120字",
      "hook": "开场钩子",
      "climax": "本集最高潮",
      "cliffhanger": "结尾悬念"
    }}
  ],
  "season_structure": {{
    "act_1": "第一幕",
    "act_2": "第二幕",
    "act_3": "第三幕"
  }},
  "character_relationships": [
    {{"pair": "A×B", "relationship": "关系描述", "arc": "变化线"}}
  ],
  "narrative_threads": {{
    "main_plot": "主线剧情概要",
    "subplots": ["副线1", "副线2"]
  }}
}}
```"""

# ─── 剧本直接分析模式（用户上传完整剧本时使用） ───
SCRIPT_ANALYSIS_PROMPT = """你是一位专业短剧剧本分析师。用户上传了完整剧本，你需要：

1. 从剧本原文中提取【每一个】有名字或台词的角色。一个都别漏！
2. 分析故事结构和节奏
3. 整理分集大纲

输出严格JSON：
```json
{{
  "characters": [
    {{
      "name": "从剧本中提取的角色名",
      "role": "主角/配角/反派/龙套",
      "gender": "男/女",
      "age": "从剧本推断的年龄",
      "appearance": "从剧本中总结的外貌特征",
      "personality": "从剧本中总结的性格",
      "background": "背景信息",
      "first_appearance": "首次出场位置"
    }}
  ],
  "scene_list": [
    {{ "scene_num": 1, "location": "场景地点", "characters": ["出场角色"], "summary": "场景概要" }}
  ],
  "structure_analysis": "剧本结构分析（200字内）",
  "climax_points": ["高潮1", "高潮2"],
  "suggestions": ["优化建议1", "优化建议2"]
}}
```

⚠️ 关键：characters 数组必须包含剧本里【每一个】有名字或台词的角色。不要偷懒只列主角！"""


class StoryArchitect(AgentV3):
    name = "story_architect"

    def execute(self, task: dict) -> dict:
        genre = task.get("genre", "")
        title = task.get("title", "")
        synopsis = task.get("synopsis", "")
        style_hint = task.get("style_hint", task.get("style", ""))
        full_script = task.get("full_script", "")

        # Showrunner 的创作方向建议
        showrunner_analysis = task.get("showrunner_analysis", {})
        task_breakdown = showrunner_analysis.get("task_breakdown", {}) if isinstance(showrunner_analysis, dict) else {}
        sa_focus = task_breakdown.get("story_architect", {})
        creative_focus = sa_focus.get("focus", "") if isinstance(sa_focus, dict) else ""
        if creative_focus:
            creative_focus = f"创作方向：{creative_focus}"

        # ─── 模式选择：完整剧本 → 直接分析，创意梗概 → 重新生成 ───
        if full_script and len(full_script) > 300:
            logger.info(f"[StoryArchitect] 剧本直接分析模式，剧本长度={len(full_script)}")
            user_prompt = f"完整剧本：\n{full_script[:8000]}\n\n体裁：{genre}\n标题：{title}\n\n请直接分析以上剧本，提取所有角色和场景。"
            system_prompt = SCRIPT_ANALYSIS_PROMPT
            timeout_val = 300
        else:
            user_prompt = USER_PROMPT_TEMPLATE.format(
                genre=genre, title=title, synopsis=synopsis,
                style_hint=style_hint,
                creative_focus=creative_focus if creative_focus else "无特定方向限制，发挥创意",
            )
            system_prompt = SYSTEM_PROMPT
            timeout_val = 300

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=system_prompt,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=timeout_val,
                max_tokens=8192,
            )
            content = result.get("text", result.get("content", "{}"))
            logger.info(f"[StoryArchitect] First 300 chars: {content[:300]}")
            struct = self._extract_json(content)
            if not struct:
                return {"success": False, "error": "无法解析故事架构输出"}

            chars = struct.get("characters", [])
            logger.info(f"[StoryArchitect] 提取到 {len(chars)} 个角色: {[c.get('name','?') for c in chars]}")

            return {
                "success": True,
                "story_structure": struct,
                "characters": chars,
                "episode_outlines": struct.get("episode_outlines", struct.get("scene_list", [])),
            }
        except Exception as e:
            logger.error(f"[StoryArchitect] 失败: {e}")
            return {"success": False, "error": str(e)}

    def _extract_json(self, text: str) -> Optional[dict]:
        import re
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass
        m = re.search(r'\{[\s\S]*\}', text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass
        return None
