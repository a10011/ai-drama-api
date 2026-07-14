"""
Hermes CharacterDev — 角色开发师
人物弧光、性格建模、关系网、台词风格
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位顶级角色开发师（Character Developer），专精于短剧角色塑造。

能力：
1. 角色建模：性格、动机、缺陷、成长空间
2. 人物弧光：从起点到终点的蜕变轨迹
3. 关系网：角色之间的情感张力
4. 台词风格：每个角色的语言特征
5. 视觉设计：外貌、服装、标志性动作

输出必须是严格的 JSON。"""

USER_PROMPT_TEMPLATE = """剧本信息：
题材: {genre}
标题: {title}
概要: {synopsis}

已有角色框架：
{existing_characters}

{creative_focus}

请为这些角色完善深度塑造，并为已有的角色补充完整的台词风格和视觉设计。

输出 JSON 结构：
```json
{{
  "characters": [
    {{
      "name": "角色名",
      "role": "主角/配角/反派",
      "gender": "男/女",
      "age": "年龄",
      "appearance": "外貌特征，40-60字，强调可被AI视觉模型识别的特征",
      "costume": "服装风格，20-30字",
      "signature_pose": "标志性动作/姿态",
      "personality": "性格描述，80-100字",
      "strengths": ["优点1", "优点2"],
      "flaws": ["缺点1", "缺点2"],
      "motivation": "核心驱动力",
      "background": "背景故事，60-100字",
      "growth_arc": "本季成长线",
      "voice_style": "音色风格，如'清冷御姐音'",
      "speech_pattern": "说话习惯，如'喜欢用反问句'",
      "catchphrases": ["口头禅1", "口头禅2"],
      "season2_hint": "下一季发展可能"
    }}
  ],
  "relationships": [
    {{
      "pair": "A×B",
      "type": "敌对/暧昧/师徒/挚友",
      "dynamic": "关系动态描述",
      "arc": "本季关系变化线",
      "key_conflict": "核心冲突点"
    }}
  ],
  "casting_notes": "选角建议或角色类型参考"
}}
```"""


class CharacterDev(AgentV3):
    name = "character_dev"

    def execute(self, task: dict) -> dict:
        genre = task.get("genre", "")
        title = task.get("title", "")
        synopsis = task.get("synopsis", "")

        # 上游故事架构师产出的角色列表
        story_result = task.get("upstream_story", {})
        existing_chars = story_result.get("characters", []) if isinstance(story_result, dict) else []
        existing_characters = json.dumps(existing_chars, ensure_ascii=False, indent=2) if existing_chars else "无"

        # Showrunner 方向指导
        showrunner = task.get("showrunner_analysis", {})
        tb = showrunner.get("task_breakdown", {}) if isinstance(showrunner, dict) else {}
        cd_focus = tb.get("character_dev", {}) if isinstance(tb, dict) else {}
        cf = cd_focus.get("focus", "") if isinstance(cd_focus, dict) else ""

        user_prompt = USER_PROMPT_TEMPLATE.format(
            genre=genre, title=title, synopsis=synopsis,
            existing_characters=existing_characters,
            creative_focus=f"创作方向：{cf}" if cf else "无特定方向限制",
        )

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=SYSTEM_PROMPT,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=120,
                max_tokens=8192,
            )
            content = result.get("text", result.get("content", "{}"))
            data = self._extract_json(content)
            if not data:
                return {"success": False, "error": "无法解析角色开发输出"}

            return {
                "success": True,
                "characters": data.get("characters", []),
                "relationships": data.get("relationships", []),
                "casting_notes": data.get("casting_notes", ""),
            }
        except Exception as e:
            logger.error(f"[CharacterDev] 失败: {e}")
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
