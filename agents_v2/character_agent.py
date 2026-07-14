# -*- coding: utf-8 -*-
"""
CharacterAgent V3 — 角色肖像生成
模型: AgnesAI image-2.1-flash | 提示词: [主体]+[场景]+[风格]+[光照]+[构图]+[质量]
"""
import json, logging, re
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel
from prompt_engine import build_portrait_prompt, build_t2i_prompt
import requests as _requests

logger = logging.getLogger(__name__)


class CharacterAgent(AgentV3):
    name = "character"
    """角色肖像执行师——严格按导演给定的人物描述生成肖像，不自行修改角色设定"""

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        chars = data.get("characters", [])
        if not chars:
            refined = data.get("refined_script", {})
            if isinstance(refined, dict):
                chars = refined.get("characters", [])
        if not chars:
            season = data.get("season_plan", {})
            if isinstance(season, dict):
                chars = season.get("characters", [])
        
        genre = data.get("genre", "现代")
        pipeline_id = task.get("pipeline_id", "")

        if not chars:
            # LLM从剧本提取角色
            script = data.get("script_text", "")
            if script and len(script) > 20:
                chars = self._extract_characters(script, genre)
        
        if not chars:
            logger.warning("[CharacterAgent] 无角色数据")
            return {"success": True, "portraits": [], "pipeline_id": pipeline_id}

        portraits = []
        import time as _time
        _img_count = 0
        for ch in chars:
            name = ch.get("name", f"角色{len(portraits)+1}")
            
            # 限流: 10 req/min, 每张图至少间隔6秒
            if _img_count > 0:
                _time.sleep(7)
            
            # 查记忆缓存
            cached = self.memory.lookup("portrait", name)
            if cached and cached.get("value", {}).get("url"):
                portraits.append(cached["value"])
                logger.info(f"[CharacterAgent] {name} 缓存命中")
                continue

            # 构建标准提示词
            prompt = build_portrait_prompt(ch, genre)
            
            # 调用AgnesAI生图（带503重试）
            result = self._generate_image(prompt, pipeline_id, name)
            
            url = result.get("url", "")
            if url:
                entry = {
                    "name": name,
                    "url": url,
                    "gender": ch.get("gender", ""),
                    "age": ch.get("age", ""),
                    "personality": ch.get("personality", ""),
                    "appearance": ch.get("appearance", ""),
                    "voice_style": ch.get("voice_style", ""),
                    "role_type": ch.get("role_type", ""),
                    "prompt": prompt,
                }
                portraits.append(entry)
                self.memory.save(entry, "portrait", name, tags=genre)
                _img_count += 1
                logger.info(f"[CharacterAgent] {name} 肖像生成成功")
            else:
                logger.warning(f"[CharacterAgent] {name} 生图失败: {result.get('error','')[:80]}")

        return {
            "success": True,
            "portraits": portraits,
            "pipeline_id": pipeline_id,
            "genre": genre,
        }

    def _generate_image(self, prompt: str, pipeline_id: str, name: str, max_retries: int = 3) -> dict:
        """调用AgnesAI生图，自动重试503"""
        import time
        for attempt in range(max_retries):
            try:
                result = UnifiedModel.image(
                    prompt=prompt,
                    preferred="agnes",
                    size="1024x1024",
                )
                url = result.url if hasattr(result, 'url') else result.get("url", "")
                if url:
                    return {"url": url}
                error = result.error if hasattr(result, 'error') else result.get("error", "")
                if "memory_overloaded" in str(error) or "503" in str(error):
                    wait = 5 * (attempt + 1)
                    logger.info(f"[CharacterAgent] 503重试 {attempt+1}/{max_retries}, 等{wait}s")
                    time.sleep(wait)
                    continue
                return {"url": "", "error": str(error)[:200]}
            except Exception as e:
                err = str(e)
                if "503" in err or "overloaded" in err:
                    time.sleep(5 * (attempt + 1))
                    continue
                return {"url": "", "error": err[:200]}
        return {"url": "", "error": "max retries exceeded"}

    def _extract_characters(self, script: str, genre: str) -> list:
        """LLM从剧本提取角色"""
        system = "你是角色提取专家。从剧本提取所有出场角色。"
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

    def _check_memory(self, task: dict) -> dict | None:
        data = task.get("data", {})
        chars = data.get("characters", []) or data.get("refined_script", {}).get("characters", [])
        if not chars:
            return None
        portraits = []
        import time as _time
        _img_count = 0
        for ch in chars:
            cached = self.memory.lookup("portrait", ch.get("name", ""))
            if cached:
                portraits.append(cached["value"])
            else:
                return None
        return {"success": True, "portraits": portraits, "pipeline_id": task.get("pipeline_id", "")}

    def _find_similar_memory(self, task: dict) -> list:
        genre = task.get("data", {}).get("genre", "")
        return self.memory.find_similar(genre, limit=5) if genre else []

    def _save_memory(self, task: dict, result: dict):
        pass  # 已在 execute 逐角色保存
