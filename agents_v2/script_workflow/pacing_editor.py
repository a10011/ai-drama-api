"""
Hermes PacingEditor — 节奏编辑
时长计算、悬念密度、爽点频率
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一位顶级节奏编辑（Pacing Editor），专精于短剧节奏把控。

能力：
1. 时长审核：每集总时长是否在 180-300 秒
2. 悬念密度：3-5分钟必须有悬念点
3. 爽点频率：每集至少 2-3 个爽点
4. 情绪节奏：高低起伏是否合理
5. 转场流畅：场景切换是否自然

输出必须是严格的 JSON。"""

USER_PROMPT_TEMPLATE = """剧本内容：
{episode_script}

请分析本集的节奏，按以下 JSON 输出：
```json
{{
  "total_duration": 180,
  "duration_ok": true,
  "hook_intro": "开头钩子在第几秒，是否有效",
  "climax_timing": "高潮在第几秒，是否合理",
  "cliffhanger": "结尾悬念是否足够",
  "emotional_rhythm": "情绪起伏节奏评价",
  "suspense_density": "悬念密度评价（优/良/中/差）",
  "satisfying_moments": ["爽点1", "爽点2"],
  "issues": [
    {{
      "type": "时长/节奏/悬念",
      "location": "具体位置",
      "severity": "高/中/低",
      "suggestion": "修改建议"
    }}
  ],
  "optimization_suggestions": ["优化建议1", "优化建议2"],
  "overall_rating": "优/良/中/差"
}}
```"""


class PacingEditor(AgentV3):
    name = "pacing_editor"

    def execute(self, task: dict) -> dict:
        upstream_scene = task.get("upstream_scene", {})
        episode_script = upstream_scene.get("episode_script", upstream_scene) if isinstance(upstream_scene, dict) else {}

        user_prompt = USER_PROMPT_TEMPLATE.format(
            episode_script=json.dumps(episode_script, ensure_ascii=False, indent=2),
        )

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=SYSTEM_PROMPT,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=90,
                max_tokens=4096,
            )
            content = result.get("text", result.get("content", "{}"))
            data = self._extract_json(content)
            if not data:
                return {"success": False, "error": "无法解析节奏分析输出"}

            return {"success": True, "pacing_report": data}
        except Exception as e:
            logger.error(f"[PacingEditor] 失败: {e}")
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
