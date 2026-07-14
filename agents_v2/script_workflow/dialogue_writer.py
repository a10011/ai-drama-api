"""
Hermes DialogueWriter — 对白专家
角色声音建模、潜台词、口语节奏
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位顶级对白专家（Dialogue Writer），专精于角色对白创作。

能力：
1. 角色声音：每个角色有独一无二的语言风格
2. 潜台词：表面对话下的真实意图
3. 节奏：对白长短错落，有张力
4. 口语化：对白自然，符合角色身份
5. 戏剧冲突：通过对话推进剧情

输出必须是严格的 JSON。"""

USER_PROMPT_TEMPLATE = """剧本场景数据：
{scenes_json}

角色档案：
{character_profiles}

请为每个场景完善对白，确保：
1. 每个角色说话风格一致（参考角色档案中的 speech_pattern）
2. 对白有潜台词，不只是表面信息
3. 长短句交错，有节奏感
4. 符合角色身份和当下情绪

输出完整的 JSON，替换 scenes 数组中每个 shot 的 dialogue 字段。"""


class DialogueWriter(AgentV3):
    name = "dialogue_writer"

    def execute(self, task: dict) -> dict:
        upstream_scene = task.get("upstream_scene", {})
        upstream_chars = task.get("upstream_chars", {})

        scenes = upstream_scene.get("scenes", []) if isinstance(upstream_scene, dict) else []
        chars = upstream_chars.get("characters", []) if isinstance(upstream_chars, dict) else []

        user_prompt = USER_PROMPT_TEMPLATE.format(
            scenes_json=json.dumps(scenes, ensure_ascii=False, indent=2),
            character_profiles=json.dumps(chars, ensure_ascii=False, indent=2),
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
                return {"success": False, "error": "无法解析对白输出"}

            # 如果返回的是完整场景数组
            if isinstance(data, dict) and data.get("scenes"):
                return {"success": True, "scenes": data["scenes"]}
            elif isinstance(data, dict):
                return {"success": True, "scenes": scenes, "dialogue_notes": data}
            else:
                return {"success": True, "scenes": scenes}

        except Exception as e:
            logger.error(f"[DialogueWriter] 失败: {e}")
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
