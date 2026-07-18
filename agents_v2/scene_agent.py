# -*- coding: utf-8 -*-
"""
SceneAgent V3 — 场景图生成（含人脸锁定）
模型: AgnesAI image-2.1-flash | i2i人脸锁定 | T2I: [主体]+[场景]+[风格]+[光照]+[构图]+[质量]
"""
import json, logging, time
from core.agent_base_v3 import AgentV3
from core.safety_filter import clean_text
from services.model_client import UnifiedModel
from prompt_engine import build_scene_prompt
import requests as _requests

logger = logging.getLogger(__name__)


class SceneAgent(AgentV3):
    name = "scene"
    task_timeout = 1800  # 25张图需要时间
    """场景图执行师——严格按导演和分镜师的描述生成场景图，不自行创作"""  # 25张图需要25*40=1000s


    def _clean_result(self, result: dict) -> dict:
        """递归清洗结果中的文本字段"""
        if not isinstance(result, dict):
            result = self._clean_result(result)
        return result
        for k, v in result.items():
            if isinstance(v, str):
                result[k] = clean_text(v)
            elif isinstance(v, dict):
                result[k] = self._clean_result(v)
            elif isinstance(v, list):
                result[k] = [self._clean_result(item) if isinstance(item, dict) else clean_text(item) if isinstance(item, str) else item for item in v]
        result = self._clean_result(result)
        return result

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        shots = data.get("shots", data.get("storyboard", []))
        genre = data.get("genre", "现代")
        pipeline_id = task.get("pipeline_id", "")
        director_scene_instruction = data.get("director_scene_instruction", "")
        visual_style = data.get("visual_style", "")
        wardrobe_plan = data.get("wardrobe_plan", "")
        prop_plan = data.get("prop_plan", "")
        makeup_plan = data.get("makeup_plan", "")
        sfx_plan = data.get("sfx_plan", "")
        scene_asset_lib = data.get("场景资产库", {})
        
        # 如果是续集，尝试从数据库加载上一集的场景资产库
        if not scene_asset_lib and task.get("data", {}).get("episode", 1) > 1:
            try:
                from services.scene_asset_manager import SceneAssetManager
                sam = SceneAssetManager()
                project_id = task.get("data", {}).get("project_id", "")
                if project_id:
                    prev_asset_lib = sam.get_scene_library(project_id)
                    if prev_asset_lib:
                        logger.info(f"[SceneAgent] 从数据库加载上一集场景资产库")
                        scene_asset_lib = prev_asset_lib
            except Exception as e:
                logger.warning(f"[SceneAgent] 加载场景资产库失败: {e}")

        # 获取角色肖像映射（用于人脸锁定）
        portraits = data.get("portraits", [])
        char_portrait_map = {}
        for p in portraits:
            name = p.get("name", "")
            url = p.get("url", "")
            if name and url:
                char_portrait_map[name] = url

        # 必须有分镜数据
        if not shots:
            return {"success": False, "error": "导演未提供分镜数据", "pipeline_id": pipeline_id}

        # 去重：同场景+同景别=复用，省token
        scene_images = []
        seen = {}  # key=(location, shot_type) -> url
        import time as _time
        _img_count = 0
        for shot in shots:
            shot_num = shot.get("shot_num", shot.get("shot_id", len(scene_images) + 1))
            focus_char = shot.get("focus_character", "")
            
            # 去重：同场景+同景别复用已有图
            loc = shot.get("location", shot.get("scene", ""))
            stype = shot.get("shot_type", shot.get("type", ""))
            cache_key = f"{loc}|{stype}"
            if cache_key in seen:
                scene_images.append({**seen[cache_key], "shot_num": shot.get("shot_num", len(scene_images)+1), "reused": True})
                logger.info(f"[SceneAgent] 镜{shot.get('shot_num','?')} 复用同场景图 -> 省一次调用")
                continue
            
            # 限流: 10 req/min, 间隔6秒
            if _img_count > 0:
                _time.sleep(7)
            
            # 构建场景图提示词（自动判断i2i还是t2i）
            prompt_text, ref_url = build_scene_prompt(shot, genre, char_portrait_map, director_scene_instruction, wardrobe_plan, prop_plan, makeup_plan, sfx_plan, scene_asset_lib)
            
            if ref_url:
                # 图生图模式：用人脸参考图锁定角色
                logger.info(f"[SceneAgent] 镜{shot_num} i2i模式, 锁定角色={focus_char}")
                result = self._generate_i2i(prompt_text, ref_url, pipeline_id, shot_num)
            else:
                # 文生图模式
                logger.info(f"[SceneAgent] 镜{shot_num} t2i模式")
                result = self._generate_t2i(prompt_text, pipeline_id, shot_num)
            
            url = result.get("url", "")
            if url:
                scene_images.append({
                    "shot_num": shot_num,
                    "url": url,
                    "description": shot.get("description", "")[:100],
                    "prompt": prompt_text,
                    "mode": "i2i" if ref_url else "t2i",
                    "focus_character": focus_char,
                })
                _img_count += 1
                seen[cache_key] = {"url": url, "shot_num": shot_num}
                logger.info(f"[SceneAgent] 镜{shot_num} 场景图生成成功")
            else:
                logger.warning(f"[SceneAgent] 镜{shot_num} 失败: {result.get('error','')[:80]}")

        # 记录用量
        try:
            from services.usage_tracker import log_usage
            log_usage(
                model_name="agnes",
                provider="agnes",
                model_type="image",
                status="success",
                user_id=task.get("user_id", 0),
                drama_id=pipeline_id,
                image_count=len(scene_images),
            )
        except Exception as e:
            logger.warning(f"[SceneAgent] 记录用量失败: {e}")
        
        return {
            "success": True,
            "scene_images": scene_images,
            "storyboard": shots,
            "pipeline_id": pipeline_id,
            "genre": genre,
        }

    def _generate_t2i(self, prompt: str, pipeline_id: str, shot_num: int, max_retries: int = 3) -> dict:
        """文生图，自动503重试 — 使用2K+9:16竖屏"""
        for attempt in range(max_retries):
            try:
                result = self.call_with_safety_retry(
                    None, 1,
                    UnifiedModel.image,
                    prompt=prompt,
                    preferred="agnes",
                    size="2K",
                )
                url = result.url if hasattr(result, 'url') else result.get("url", "")
                if url:
                    return {"url": url}
                error = result.error if hasattr(result, 'error') else result.get("error", "")
                if "503" in str(error) or "overloaded" in str(error):
                    wait = 8 * (attempt + 1)
                    logger.info(f"[SceneAgent] 镜{shot_num} 503重试 {attempt+1}/{max_retries}")
                    time.sleep(wait)
                    continue
                return {"url": "", "error": str(error)[:200]}
            except Exception as e:
                if "503" in str(e) or "overloaded" in str(e):
                    time.sleep(8 * (attempt + 1))
                    continue
                return {"url": "", "error": str(e)[:200]}
        return {"url": "", "error": "max retries"}

    def _generate_i2i(self, prompt: str, ref_url: str, pipeline_id: str, shot_num: int, max_retries: int = 3) -> dict:
        """图生图：用人脸参考图保持角色一致性 — 使用2K+9:16竖屏"""
        for attempt in range(max_retries):
            try:
                result = self.call_with_safety_retry(
                    None, 1,
                    UnifiedModel.image,
                    prompt=prompt,
                    preferred="agnes",
                    size="2K",
                    reference_image=ref_url,
                )
                url = result.url if hasattr(result, 'url') else result.get("url", "")
                if url:
                    return {"url": url}
                error = result.error if hasattr(result, 'error') else result.get("error", "")
                if "503" in str(error) or "overloaded" in str(error):
                    time.sleep(8 * (attempt + 1))
                    continue
                return {"url": "", "error": str(error)[:200]}
            except Exception as e:
                if "503" in str(e) or "overloaded" in str(e):
                    time.sleep(8 * (attempt + 1))
                    continue
                return {"url": "", "error": str(e)[:200]}
        return {"url": "", "error": "max retries"}

    def _check_memory(self, task: dict) -> dict | None:
        shots = task.get("data", {}).get("storyboard", [])
        if not shots:
            return None
        key = str(hash(json.dumps([s.get("description","")[:50] for s in shots])))
        return self.memory.lookup("scene_result", key)

    def _find_similar_memory(self, task: dict) -> list:
        genre = task.get("data", {}).get("genre", "")
        return self.memory.find_similar(genre, limit=3) if genre else []

    def _save_memory(self, task: dict, result: dict):
        if not result.get("success"):
            return
        shots = task.get("data", {}).get("storyboard", [])
        key = str(hash(json.dumps([s.get("description","")[:50] for s in shots])))
        self.memory.save(result, "scene_result", key, tags=result.get("genre",""))
