"""
Hermes ScriptReviewer — 质量审核
多维评分、问题检测、返修建议
"""
import json, logging
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

REVIEW_SYSTEM_PROMPT = """你是一位严格的剧本质量审核专家（Script Reviewer）。你从以下维度审核剧本质量：

1. 情节逻辑（25%）：是否有逻辑漏洞、前后矛盾
2. 角色一致性（20%）：角色行为是否符合性格设定
3. 对白质量（20%）：对白是否自然、有潜台词、符合角色
4. 节奏合理（15%）：时长、悬念、爽点是否达标
5. 格式合规（10%）：JSON 结构完整，字段齐全
6. 创意新颖（10%）：是否有独特的创意亮点

评分标准：
- 90-100: 优秀，可直接使用
- 80-89: 良好，微调即可
- 70-79: 及格，需要返修
- 0-69: 不合格，需要大幅修改

输出必须是严格的 JSON，不要输出其他文字。"""

REVIEW_USER_TEMPLATE = """请审核以下剧本：

【故事结构】
{story_structure}

【角色档案】
{character_profiles}

【第一集剧本】
{episode_script}

【节奏报告】
{pacing_report}

请输出审核报告：
```json
{{
  "passed": true/false,
  "overall_score": 85,
  "dimension_scores": {{
    "plot_logic": 85,
    "character_consistency": 80,
    "dialogue_quality": 85,
    "pacing": 90,
    "format": 95,
    "creativity": 75
  }},
  "issues": [
    {{
      "category": "plot/character/dialogue/pacing/format",
      "severity": "high/mid/low",
      "description": "问题描述",
      "suggestion": "修改建议"
    }}
  ],
  "strengths": ["亮点1", "亮点2"],
  "suggestions": ["改进建议1", "改进建议2"],
  "verdict": "通过/需要返修/不通过"
}}
```"""


class ScriptReviewer(AgentV3):
    name = "script_reviewer"

    def execute(self, task: dict) -> dict:
        upstream_story = task.get("upstream_story", {})
        upstream_chars = task.get("upstream_chars", {})
        upstream_scene = task.get("upstream_scene", {})
        pacing_result = task.get("upstream_pacing", {})

        story_struct = upstream_story.get("story_structure", {}) if isinstance(upstream_story, dict) else {}
        chars = upstream_chars.get("characters", []) if isinstance(upstream_chars, dict) else []
        script = upstream_scene.get("episode_script", upstream_scene) if isinstance(upstream_scene, dict) else {}
        pacing = pacing_result.get("pacing_report", {}) if isinstance(pacing_result, dict) else {}

        user_prompt = REVIEW_USER_TEMPLATE.format(
            story_structure=json.dumps(story_struct, ensure_ascii=False, indent=2)[:3000],
            character_profiles=json.dumps(chars, ensure_ascii=False, indent=2)[:3000],
            episode_script=json.dumps(script, ensure_ascii=False, indent=2)[:5000],
            pacing_report=json.dumps(pacing, ensure_ascii=False, indent=2),
        )

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=REVIEW_SYSTEM_PROMPT,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=90,
                max_tokens=4096,
            )
            content = result.get("text", result.get("content", "{}"))
            data = self._extract_json(content)
            if not data:
                return {"success": False, "error": "无法解析审核输出"}

            return {"success": True, "review": data}
        except Exception as e:
            logger.error(f"[ScriptReviewer] 失败: {e}")
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
