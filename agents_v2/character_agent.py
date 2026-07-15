# -*- coding: utf-8 -*-
"""
CharacterAgent V3 — 角色提取与合并
肖像生成由 Orchestrator._post_gen_portraits 统一处理（含生图、本地化、回写）。
本 Agent 只负责从上游数据中提取/合并角色列表，不做生图。
"""
import json, logging, re
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)


class CharacterAgent(AgentV3):
    name = "character"
    """角色提取师——从剧本/导演分析中提取角色列表，不做肖像生成"""

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        chars = data.get("characters", [])
        if not chars:
            refined = data.get("refined_script", {})
            if isinstance(refined, dict):
                chars = refined.get("characters", [])
        if not chars:
            return {"success": False, "error": "导演未提供角色数据", "pipeline_id": task.get("pipeline_id", "")}
        
        genre = data.get("genre", "现代")
        pipeline_id = task.get("pipeline_id", "")
        director_task = data.get("director_task", "")
        visual_style = data.get("visual_style", "")
        wardrobe_hint = data.get("wardrobe_hint", data.get("wardrobe_plan", ""))

        seen = set()
        deduped = []
        for c in chars:
            name = c.get("name", "")
            if name and name not in seen:
                seen.add(name)
                # 兼容旧字段名：features → appearance
                if "features" in c and "appearance" not in c:
                    c["appearance"] = c["features"]
                deduped.append(c)

        # 用导演指令增强角色描述
        if wardrobe_hint:
            for c in deduped:
                if c.get("wardrobe") and not c.get("wardrobe_note"):
                    c["wardrobe_note"] = wardrobe_hint[:300]
        if visual_style:
            for c in deduped:
                if c.get("appearance") and not c.get("style_note"):
                    c["style_note"] = visual_style[:200]

        logger.info(f"[CharacterAgent] 合并 {len(deduped)} 个角色")
        return {"success": True, "characters": deduped, "pipeline_id": pipeline_id, "genre": genre}

    def _extract_characters(self, script: str, genre: str, director_task: str = "") -> list:
        """LLM从剧本提取角色"""
        system = "你是角色提取专家。从剧本提取所有出场角色。"
        if director_task:
            system += f"\n\n【导演角色设计指令】{director_task[:500]}"
        user = (
            f"【剧本】\n{script[:3000]}\n\n"
            f"【任务】提取所有角色信息\n"
            f'【输出格式】JSON数组：[{{"name":"","gender":"男/女","age":"","personality":"","role_type":"主角/配角","appearance":"体形+五官+发型+服装固定特征","voice_style":""}}]\n'
        )
        try:
            result = UnifiedModel.llm(prompt=user, system=system, model=None, timeout=60, max_tokens=2048)
            text = result.text if hasattr(result, 'text') else result.get("text", "")
            m = re.search(r'\[.*\]', str(text), re.DOTALL)
            if m:
                chars = json.loads(m.group(0))
                logger.info(f"[CharacterAgent] LLM提取 {len(chars)} 个角色")
                return chars
        except Exception as e:
            logger.warning(f"[CharacterAgent] 角色提取失败: {e}")
        return []
