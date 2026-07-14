"""
Hermes SceneDesigner — 场景编剧
场景分解、视觉叙事、情绪曲线
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位顶级场景编剧（Scene Designer），专精于将故事转化为可拍摄的场景。

能力：
1. 场景分解：将情节点拆分为具体场景
2. 视觉设计：每个场景的视觉风格、镜头语言
3. 情绪曲线：场景之间的情绪起伏衔接
4. 转场设计：自然流畅的转场方式
5. 时长控制：确保每集总时长达标

输出必须是严格的 JSON。"""

USER_PROMPT_TEMPLATE = """剧本信息：
题材: {genre}
标题: {title}
概要: {synopsis}

角色档案：
{character_profiles}

第一集概要：
{episode_info}

{creative_focus}

请将第一集扩展为完整分场剧本，按以下 JSON 结构输出：

```json
{{
  "format": "episode",
  "episode": 1,
  "title": "集标题",
  "total_duration": 180,
  "emotional_arc": "本集情绪曲线描述",
  "scenes": [
    {{
      "scene_id": 1,
      "location": "场景地点",
      "time": "日/夜/黄昏/黎明",
      "interior_exterior": "内景/外景",
      "characters": ["出现的角色名"],
      "summary": "本场概要，30-50字",
      "emotional_tone": "情绪基调",
      "duration_seconds": 30,
      "shots": [
        {{
          "shot_id": 1,
          "camera": "镜头类型（全景/中景/近景/特写/俯拍/仰拍/跟拍/推拉）",
          "image_prompt": "画面描述，20-40字，适合AI视频模型理解",
          "dialogue": "对白内容（无对白则为空）",
          "action": "动作描述",
          "duration": 5
        }}
      ]
    }}
  ],
  "transition_notes": "场景转场设计说明"
}}
```

注意：
- 每个场景包含多个镜头（shots），每个镜头 3-8 秒
- 总时长 180-300 秒
- 镜头描述要可视化，让 AI 视频模型能理解
- 场景数量 6-12 个
"""


class SceneDesigner(AgentV3):
    name = "scene_designer"

    def execute(self, task: dict) -> dict:
        genre = task.get("genre", "")
        title = task.get("title", "")
        synopsis = task.get("synopsis", "")

        # 上游数据
        upstream_story = task.get("upstream_story", {})
        upstream_chars = task.get("upstream_chars", {})

        story_struct = upstream_story.get("story_structure", {}) if isinstance(upstream_story, dict) else {}
        characters = upstream_chars.get("characters", []) if isinstance(upstream_chars, dict) else []

        # 取第一集大纲
        outlines = story_struct.get("episode_outlines", []) if isinstance(story_struct, dict) else []
        ep1 = outlines[0] if outlines else {"ep": 1, "title": title, "summary": synopsis}

        episode_info = json.dumps(ep1, ensure_ascii=False, indent=2)
        character_profiles = json.dumps(characters, ensure_ascii=False, indent=2) if characters else "无"

        user_prompt = USER_PROMPT_TEMPLATE.format(
            genre=genre, title=title, synopsis=synopsis,
            character_profiles=character_profiles,
            episode_info=episode_info,
            creative_focus="",
        )

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=SYSTEM_PROMPT,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=300,
                max_tokens=12288,
            )
            content = result.get("text", result.get("content", "{}"))
            data = self._extract_json(content)
            if not data:
                return {"success": False, "error": "无法解析场景编剧输出"}

            return {
                "success": True,
                "episode_script": data,
                "scenes": data.get("scenes", []),
                "total_duration": data.get("total_duration", 180),
            }
        except Exception as e:
            logger.error(f"[SceneDesigner] 失败: {e}")
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
