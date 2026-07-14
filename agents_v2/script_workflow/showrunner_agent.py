"""
Hermes ShowrunnerAgent - 总编剧/导演
深度剧本分析 + 任务拆解
"""
import json, logging, time, asyncio
from typing import Optional

from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SHOWRUNNER_SYSTEM_PROMPT = """你是一位顶级短剧总编剧（Showrunner），你擅长：
1. 分析用户的创意输入，理解核心诉求
2. 将创意拆解为可执行的任务，分派给不同的专业编剧
3. 评审各专业编剧的产出，给出修改意见
4. 处理争议，做最终创意决策

你的风格：专业、犀利、有判断力，不一味迎合用户，敢于提建设性批评。

输出格式：JSON
{
    "analysis": "对创意的深度分析，150-200字",
    "creative_direction": "明确的创作方向建议",
    "task_breakdown": {
        "story_architect": {"priority": "high", "focus": "需要重点关注什么"},
        "character_dev": {"priority": "high", "focus": "角色设计方向"},
        "scene_designer": {"priority": "medium", "focus": ""},
        "dialogue_writer": {"priority": "medium", "focus": ""},
        "pacing_editor": {"priority": "medium", "focus": ""}
    },
    "quality_criteria": "衡量这个剧本好坏的标准"
}"""

# 完整剧本深度分析模式
SCRIPT_DEEP_ANALYSIS_PROMPT = """你是一位顶级短剧导演和总编剧。用户上传了一份【完整剧本】，你的任务是逐字精读这份剧本，然后给出专业深度分析。

请逐段分析：
1. 每个出场的角色是谁？ta的性格、动机、人物关系是什么？
2. 剧本的结构：开场→冲突→高潮→收尾，每段的节奏如何？
3. 有哪些值得保留的亮点？有哪些需要改进的地方？
4. 如果有下一集，应该往哪个方向发展？

输出JSON：
{
    "deep_analysis": "对剧本的完整深度分析（300-500字），包括角色解读、情节结构、节奏评估",
    "character_insights": [
        {"name": "角色名", "role": "主角/配角/龙套", "personality_read": "从剧本中读出的性格", "motivation": "核心动机", "arc_suggestion": "建议的成长线"}
    ],
    "structure_review": {
        "opening": "开场评价",
        "conflict": "冲突评价",
        "climax": "高潮评价",
        "ending": "收尾评价"
    },
    "strengths": ["亮点1", "亮点2"],
    "improvements": ["改进建议1", "改进建议2"],
    "next_episode_hint": "下一集发展方向建议",
    "task_breakdown": {
        "story_architect": {"priority": "high", "focus": ""},
        "character_dev": {"priority": "high", "focus": ""},
        "scene_designer": {"priority": "medium", "focus": ""},
        "dialogue_writer": {"priority": "medium", "focus": ""},
        "pacing_editor": {"priority": "medium", "focus": ""}
    },
    "quality_criteria": "衡量标准"
}

CRITICAL: 必须读完整个剧本，给出有深度的分析，而不是泛泛而谈！"""


class ShowrunnerAgent(AgentV3):
    name = "showrunner"

    def execute(self, task: dict) -> dict:
        genre = task.get("genre", "")
        title = task.get("title", "")
        synopsis = task.get("synopsis", "")
        style_hint = task.get("style_hint", task.get("style", ""))
        full_script = task.get("full_script", "")

        # 选择模式：完整剧本 → 深度分析，创意梗概 → 常规分析
        if full_script and len(full_script) > 300:
            logger.info(f"[Showrunner] 完整剧本深度分析模式，剧本长度={len(full_script)}")
            user_prompt = (
                f"体裁: {genre}\n"
                f"标题: {title}\n"
                f"风格: {style_hint}\n\n"
                f"========== 完整剧本 ==========\n"
                f"{full_script[:8000]}\n"
                f"==============================\n\n"
                f"请逐字精读以上剧本，给出深度分析和角色解读。"
            )
            system_prompt = SCRIPT_DEEP_ANALYSIS_PROMPT
            timeout_val = 120
        else:
            user_prompt = f"题材: {genre}\n标题: {title}\n概要: {synopsis}\n风格: {style_hint}\n\n请分析这个创意，给出创作方向建议和任务拆解方案。"
            system_prompt = SHOWRUNNER_SYSTEM_PROMPT
            timeout_val = 60

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=system_prompt,
                model=task.get("_llm_model", "deepseek-reasoner"),
                timeout=timeout_val,
                max_tokens=4096,
            )
            content = result.get("text", result.get("content", "{}"))
            analysis = self._extract_json(content)
            if not analysis:
                analysis = {"analysis": "分析完成", "task_breakdown": {}}

            return {
                "success": True,
                "analysis": analysis,
                "creative_direction": analysis.get("creative_direction", ""),
                "deep_analysis": analysis.get("deep_analysis", ""),
                "task_breakdown": analysis.get("task_breakdown", {}),
            }
        except Exception as e:
            logger.error(f"[Showrunner] 执行失败: {e}")
            return {"success": False, "error": str(e)}

    def _extract_json(self, text: str) -> Optional[dict]:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        import re
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
