"""
Hermes WorkflowEngine — 剧本工作流编排引擎
独立于短剧管线，通过 API 触发，内部编排多 Agent 协作
"""
import json, logging, time, asyncio
from typing import Optional

from .showrunner_agent import ShowrunnerAgent
from .story_architect import StoryArchitect
from .character_dev import CharacterDev
from .scene_designer import SceneDesigner
from .dialogue_writer import DialogueWriter
from .pacing_editor import PacingEditor
from .script_reviewer import ScriptReviewer

logger = logging.getLogger(__name__)

# 最大返修次数
MAX_REVISION_ROUNDS = 3


class ScriptWorkflowEngine:
    """剧本工作流引擎 — 编排多 Agent 协作"""

    def __init__(self, user_id: int = 0, llm_model: str = "deepseek-reasoner"):
        self.user_id = user_id
        self.llm_model = llm_model
        self.showrunner = ShowrunnerAgent(user_id)
        self.story_architect = StoryArchitect(user_id)
        self.character_dev = CharacterDev(user_id)
        self.scene_designer = SceneDesigner(user_id)
        self.dialogue_writer = DialogueWriter(user_id)
        self.pacing_editor = PacingEditor(user_id)
        self.reviewer = ScriptReviewer(user_id)

    async def run_precise(self, task: dict) -> dict:
        """
        精编模式：串行执行，质量最高
        Showrunner → 故事架构师 → 角色开发师 → 场景编剧 → 对白专家 → 节奏编辑 → 审核
        """
        start = time.time()
        workflow_id = f"sw_{int(time.time() * 1000)}_{task.get('user_id', 0)}"
        logger.info(f"[Workflow:{workflow_id}] 精编模式开始")

        try:
            task["_llm_model"] = task.get("_llm_model", self.llm_model)
            # Step 1: Showrunner 分析
            logger.info("[Workflow] Step 1: Showrunner 分析")
            showrunner_result = self.showrunner.run(task)
            if not showrunner_result.get("success"):
                return self._error("Showrunner 分析失败", showrunner_result.get("error", ""), workflow_id)
            task["showrunner_analysis"] = showrunner_result.get("analysis", {})

            # Step 2: 故事架构师
            logger.info("[Workflow] Step 2: 故事架构师")
            story_result = self.story_architect.run(task)
            if not story_result.get("success"):
                return self._error("故事架构失败", story_result.get("error", ""), workflow_id)
            task["upstream_story"] = story_result

            # Step 3: 角色开发师
            logger.info("[Workflow] Step 3: 角色开发师")
            char_result = self.character_dev.run(task)
            if not char_result.get("success"):
                # 角色失败不中止，使用故事架构师的基础角色
                char_result = {"success": True, "characters": story_result.get("characters", [])}
            task["upstream_chars"] = char_result

            # Step 4: 场景编剧
            logger.info("[Workflow] Step 4: 场景编剧")
            scene_result = self.scene_designer.run(task)
            if not scene_result.get("success"):
                return self._error("场景编剧失败", scene_result.get("error", ""), workflow_id)
            task["upstream_scene"] = scene_result

            # Step 5: 对白专家
            logger.info("[Workflow] Step 5: 对白专家")
            dialogue_result = self.dialogue_writer.run(task)
            if dialogue_result.get("success"):
                task["upstream_scene"] = {
                    **task["upstream_scene"],
                    "scenes": dialogue_result.get("scenes", scene_result.get("scenes", [])),
                }

            # Step 6: 节奏编辑
            logger.info("[Workflow] Step 6: 节奏编辑")
            pacing_result = self.pacing_editor.run(task)
            if not pacing_result.get("success"):
                pacing_result = {"success": True, "pacing_report": {"overall_rating": "未评估"}}
            task["upstream_pacing"] = pacing_result

            # Step 7: 质量审核 + 返修循环
            logger.info("[Workflow] Step 7: 质量审核")
            review_result = self.reviewer.run(task)
            passed = False
            revision_round = 0
            final_review = review_result.get("review", {})

            while not passed and revision_round < MAX_REVISION_ROUNDS:
                review_data = review_result.get("review", {})
                passed = review_data.get("passed", False)
                score = review_data.get("overall_score", 0)

                if passed or score >= 70:
                    passed = True
                    break

                revision_round += 1
                logger.info(f"[Workflow] 返修第 {revision_round} 轮")
                # 标记需要返修，重新跑场景和对白
                if score < 70:
                    task["_revision_hints"] = review_data.get("issues", [])
                    scene_result = self.scene_designer.run(task)
                    if scene_result.get("success"):
                        task["upstream_scene"] = scene_result
                    review_result = self.reviewer.run(task)
                    final_review = review_result.get("review", {})

            elapsed = time.time() - start
            logger.info(f"[Workflow:{workflow_id}] 完成，耗时 {elapsed:.1f}s")

            return {
                "success": True,
                "workflow_id": workflow_id,
                "elapsed_seconds": round(elapsed, 1),
                "showrunner_analysis": showrunner_result.get("analysis", {}),
                "story_structure": story_result.get("story_structure", {}),
                "characters": char_result.get("characters", []),
                "relationships": char_result.get("relationships", []),
                "episode_script": scene_result.get("episode_script", {}),
                "pacing_report": pacing_result.get("pacing_report", {}),
                "review": final_review,
                "revision_rounds": revision_round,
            }

        except Exception as e:
            logger.error(f"[Workflow:{workflow_id}] 异常: {e}", exc_info=True)
            return self._error("工作流异常", str(e), workflow_id)

    async def run_fast(self, task: dict) -> dict:
        """
        快速模式：并行执行，效率优先
        Showrunner → (故事架构师 + 角色开发师 并行) → (场景编剧 + 对白专家 并行) → 节奏编辑 → 审核
        """
        start = time.time()
        workflow_id = f"sw_{int(time.time() * 1000)}_{task.get('user_id', 0)}"
        logger.info(f"[Workflow:{workflow_id}] 快速模式开始")

        try:
            # Step 1: Showrunner
            showrunner_result = self.showrunner.run(task)
            task["showrunner_analysis"] = showrunner_result.get("analysis", {})

            # Step 2: 故事架构师 + 角色开发师 并行
            story_task = dict(task)
            char_task = dict(task)
            # 用事件循环并行
            loop = asyncio.get_event_loop()
            story_future = loop.run_in_executor(None, self.story_architect.run, story_task)
            char_future = loop.run_in_executor(None, self.character_dev.run, char_task)
            story_result = await story_future
            char_result = await char_future

            task["upstream_story"] = story_result if story_result.get("success") else {}
            task["upstream_chars"] = char_result if char_result.get("success") else {}

            if not story_result.get("success"):
                return self._error("故事架构失败", story_result.get("error", ""), workflow_id)

            # Step 3: 场景编剧 + 对白专家 并行
            scene_future = loop.run_in_executor(None, self.scene_designer.run, dict(task))
            await scene_future
            # 简化版 - 快速模式用一把出

            scene_result = task.get("upstream_scene", {})
            if not isinstance(scene_result, dict) or not scene_result.get("success"):
                scene_result = self.scene_designer.run(task)

            task["upstream_scene"] = scene_result

            # Step 4: 节奏编辑 + 审核 并行
            pacing_result = self.pacing_editor.run(task)
            task["upstream_pacing"] = pacing_result

            review_result = self.reviewer.run(task)

            elapsed = time.time() - start
            return {
                "success": True,
                "workflow_id": workflow_id,
                "elapsed_seconds": round(elapsed, 1),
                "mode": "fast",
                "showrunner_analysis": showrunner_result.get("analysis", {}),
                "story_structure": story_result.get("story_structure", {}),
                "characters": char_result.get("characters", []),
                "episode_script": scene_result.get("episode_script", {}),
                "pacing_report": pacing_result.get("pacing_report", {}),
                "review": review_result.get("review", {}),
            }

        except Exception as e:
            logger.error(f"[Workflow:{workflow_id}] 异常: {e}", exc_info=True)
            return self._error("工作流异常", str(e), workflow_id)

    async def run_deep(self, task: dict) -> dict:
        """
        深度迭代模式：逐层审核返修，精雕细琢
        """
        return await self.run_precise(task)  # 当前精编模式已含返修循环

    def _error(self, stage: str, msg: str, workflow_id: str) -> dict:
        return {
            "success": False,
            "workflow_id": workflow_id,
            "error_stage": stage,
            "error": msg,
        }
