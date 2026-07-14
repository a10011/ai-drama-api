"""智能体4：场景绘图智能体 v2 — 多模型路由"""
import json, time, logging
from typing import Optional, List
from .agent_base_legacy import BaseAgent, AgentResult
from .concurrency_pool import concurrency_pool
from .route_manager import run_with_fallback
from .result_cache import set as cache_set, get as cache_get, download_and_cache as cache_download
from utils.path_util import local_path_to_url
from app_config import BASE_URL
import threading as _sc_thr
_wanxiang_sem = _sc_thr.Semaphore(1)  # 万相限流

logger = logging.getLogger(__name__)

SCENE_DESIGN_PROMPT = """你是一位短剧场景设计师。根据剧本需求，设计场景描述。

【专业知识·场景设计】
▎场景设计作用：
- 场景是角色的情感外化：悲伤时冷清/下雨，喜悦时明亮/花开
- 每个场景要服务剧情：不仅有"在哪"，还要有"感觉"
- 用环境细节暗示剧情走向

▎常见短剧场景类型：
- 总裁办公室：极简+奢华、黑白灰主调、落地窗、质感家具
- 豪宅/别墅：挑高大厅、旋转楼梯、泳池/花园
- 普通家庭：温馨杂乱的客厅、堆满书的桌子、暖光
- 校园教室：旧课桌、黑板板书、午后阳光、风扇声
- 街景：霓虹灯、雨夜、烟火气小摊、人群
- 酒吧/夜店：暗调+彩色灯光、烟雾、热闹/冷清
- 医院：白墙消毒水、冷白光、走廊、病房单间
- 复古场景：老式家具、暖黄灯、木质结构、布艺

▎场景氛围要素：
- 光线：自然光/人工光/侧光/顶光/点光/逆光
- 色调：暖色（红橙黄）/冷色（蓝紫青）/中性
- 景深：前景/中景/背景的层次
- 季节：春天花朵/夏天阳光/秋叶/冬雪
- 时间：清晨薄雾/正午烈日/黄昏斜阳/深夜寂静

返回JSON格式（不要markdown代码块）：
{
  "scene_name": "场景名称",
  "environment": {"type": "室内/室外/虚拟", "location": "具体地点", "time_of_day": "清晨/上午/正午/下午/黄昏/夜晚", "weather": "晴/阴/雨/雪/雾/风"},
  "mood": {"atmosphere": "氛围描述", "lighting": "主光/逆光/侧光/柔光/硬光/霓虹/烛光", "color_temperature": "暖色调/冷色调/中性", "key_colors": ["主色调1", "主色调2"]},
  "props": ["道具1", "道具2"],
  "scene_prompt": "完整的AI绘图提示词（40-80字，必须包含写实电影质感、真实摄影、竖屏9:16构图主体偏上下方留白、环境、光线、氛围。严禁卡通、动漫、手绘风格）"
}"""


def _register_scene(data: bytes, name: str, tags: list = None):
    """注册场景图到媒体库"""
    try:
        from services.media_registry import register_scene as _rs
        return _rs(data, name, tags=tags or [])
    except Exception as e:
        logger.warning(f"[MediaRegistry] scene failed: {e}")
        return None


_ERA_STYLE = {
    "修仙": "仙侠古风，写实电影质感，云雾缭绕，仙气缥缈，东方玄幻，cinematic photography",
    "仙侠": "仙侠古风，写实电影质感，云雾缭绕，仙气缥缈，东方玄幻，cinematic photography",
    "古装": "古风典雅，写实电影质感，工笔细腻，古色古香，cinematic period drama",
    "武侠": "江湖古风，烟雨朦胧，意境苍茫，剑气纵横，cinematic wuxia photography",
    "历史": "古风写实，场景考究，年代感强，古典韵味",
    "民国": "民国风情，旧上海韵味，复古色调，年代质感",
    "都市": "现代都市，时尚简洁，光影通透，现代感强",
    "现代": "现代都市，时尚简洁，光影通透，现代感强",
    "科幻": "科幻未来感，赛博朋克光影，冷色调，金属质感",
    "玄幻": "东方玄幻，色彩瑰丽，梦幻光影，神秘氛围",
    "悬疑": "阴郁暗调，光影对比强烈，氛围感浓，神秘压抑",
    "恐怖": "暗黑风格，冷蓝色调，光影诡异，阴森氛围",
}


def _enrich_prompt_with_style(prompt, genre, director_task=""):
    """根据剧集类型给场景 prompt 注入时代风格和氛围描述"""
    style_desc = ""
    for key_word, era_style in _ERA_STYLE.items():
        if key_word in genre:
            style_desc = era_style
            break

    director_style = ""
    if director_task:
        import re
        for pat in [r"风格[：:]\s*([^。，；\n]+)", r"氛围[：:]\s*([^。，；\n]+)", r"色调[：:]\s*([^。，；\n]+)"]:
            m = re.search(pat, director_task)
            if m:
                director_style += m.group(1)

    extra = ""
    if style_desc:
        extra += style_desc
    if director_style:
        extra += ("，" if extra else "") + director_style
    if not extra:
        return prompt

    sep = "。" if not prompt.endswith(("。", "，", ".")) else ""
    return prompt + sep + extra


class SceneAgent(BaseAgent):
    name = "场景绘图智能体"
    description = "场景设计+多模型路由文生图"
    version = "2.1.0"

    def design_scene(self, scene_name: str, script_context: str, model_routes: dict = None) -> AgentResult:
        start = time.time()
        try:
            user_prompt = f"场景名称：{scene_name}\n剧本上下文：{script_context[:2000]}"

            # ── 场景设计缓存 ──
            from .result_cache import get as cache_get, set as cache_set
            cache_key = f"design_{scene_name}_{script_context[:200]}"
            cached = cache_get(cache_key, "scene_design")
            if cached and cached.get("data"):
                logger.info(f"[SceneDesignCache] HIT: {scene_name}")
                return AgentResult(data=cached["data"], duration_ms=0)

            result = self._call_llm_json(SCENE_DESIGN_PROMPT, user_prompt, retries=1)
            if isinstance(result, dict):
                if 'scenes' not in result:
                    result['scenes'] = [{
                        'name': result.get('scene_name', '场景'),
                        'description': str(result.get('environment', {})) + '\n' + str(result.get('mood', {})),
                        'weather': result.get('environment', {}).get('weather', '晴') if isinstance(result.get('environment'), dict) else '晴',
                        'lighting': result.get('mood', {}).get('lighting', '自然光') if isinstance(result.get('mood'), dict) else '自然光',
                        'mood': result.get('mood', {}).get('atmosphere', '中性') if isinstance(result.get('mood'), dict) else '中性',
                    }]
            cache_set(cache_key, "scene_design", data={"data": result})
            return AgentResult(data=result, duration_ms=int((time.time() - start) * 1000))
        except Exception as e:
            logger.error(f"场景设计失败: {e}")
            return AgentResult(success=False, error=str(e))

    def generate_scene_image(self, scene_prompt: str, model_routes: dict = None, director_task: str = '', reference_image: str = '', genre: str = '', project_id: int = 0) -> AgentResult:
        """多模型图片生成 — 统一 via UnifiedModel"""
        import time
        logger_local = logging.getLogger(__name__)
        start = time.time()
        scene_prompt = _enrich_prompt_with_style(scene_prompt, genre, director_task)

        from services.model_client import UnifiedModel
        from services.vertical_spec import VERT
        scene_size = VERT.SCENE_BACKGROUND
        result = None

        # i2i: Seedream img2img with character reference
        if reference_image and (reference_image.startswith('http') or reference_image.startswith('/storage/')):
            # Convert local /storage/ path to public HTTPS URL
            if reference_image.startswith('/storage/'):
                reference_image = BASE_URL + reference_image
            try:
                logger_local.info(f"[SceneAgent] i2i with ref: {reference_image[:80]}...")
                result = UnifiedModel.image_to_image(
                    prompt=scene_prompt,
                    reference_image=reference_image,
                    size=scene_size,
                    timeout=120,
                    strength=0.25,
                    project_id=project_id
                )
                if result and result.get("success"):
                    logger_local.info("[SceneAgent] i2i OK")
                else:
                    logger_local.warning(f"[SceneAgent] i2i fail, fallback t2i")
                    result = None
            except Exception as e_img:
                logger_local.warning(f"[SceneAgent] i2i ex: {e_img}, fallback t2i")
                result = None

        if not result or not result.get("success"):
            try:
                result = UnifiedModel.image(
                    prompt=scene_prompt,
                    size=scene_size,
                    timeout=60,
                    project_id=project_id
                )
            except Exception as e:
                logger_local.warning(f"UnifiedModel.image 异常: {e}，回退ffmpeg占位图")
        if result and result.get("success"):
            url = result["url"]
            cache_set(scene_prompt, "scene", scene_size, {"image_url": url, "model": result["model"]})
            cache_download(url, scene_prompt, result["model"], scene_size)
            # Register in media library
            try:
                import requests
                r = requests.get(url, timeout=10, verify=False)
                if r.status_code == 200:
                    _register_scene(r.content, scene_prompt[:30] or "scene",
                        tags=[scene_prompt[:30] or "scene", ""],
                        user_id=0)
            except Exception:
                logger_local.warning('[SceneAgent] register fail', exc_info=True)
            return AgentResult(
                data={"image_url": url, "model": result["model"], "prompt": scene_prompt},
                duration_ms=int((time.time()-start)*1000)
            )
        else:
            # 全部失败 → 回退到纯色 FFmpeg 占位图
            import os, subprocess
            fb = f"/www/wwwroot/storage/scenes/{int(time.time()*1000)}_{abs(hash(scene_prompt))}.jpg"
            colors = ["0x4477AA","0x88BB66","0xCC6644","0xAA4477","0x44AA88"]
            ci = abs(hash(scene_prompt)) % len(colors)
            subprocess.run(["ffmpeg","-y","-f","lavfi","-i",
                f"color=c={colors[ci]}:s=1024x576:d=1",
                "-frames:v","1",fb], capture_output=True, timeout=5)
            if os.path.exists(fb) and os.path.getsize(fb) > 100:
                # Register in media library
                try:
                    with open(fb, "rb") as sf:
                        sdata = sf.read()
                    _register_scene(sdata, scene_prompt[:30] or "scene",
                        tags=[scene_prompt[:30] or "scene", ""],
                        user_id=0)
                except Exception:
                    logger_local.warning("[SceneAgent] FFmpeg register fail", exc_info=True)
                return AgentResult(
                    data={"image_url": local_path_to_url(fb), "model": "ffmpeg", "prompt": scene_prompt},
                    duration_ms=int((time.time()-start)*1000),
                    success=True
                )
            return AgentResult(success=False, error=f"所有模型失败: {result.get('error', '') if result else 'unknown'}")

    def run(self, action: str = "design", **kwargs) -> AgentResult:
        model_routes = kwargs.get("model_routes")
        if action == "generate_image":
            # 优先用 kwargs 直接传入的 scene_prompt
            scene_prompt = kwargs.get("scene_prompt", kwargs.get("prompt", ""))
            if not scene_prompt:
                # 其次从 shot 对象提取
                shot = kwargs.get("shot", {})
                if isinstance(shot, dict):
                    scene_prompt = shot.get("scene_prompt", shot.get("prompt", ""))
                    if not scene_prompt:
                        desc = shot.get("description", "")
                        env = shot.get("environment", "")
                        sp = shot.get("scene", "")
                        scene_prompt = (desc + "，环境：" + env + "，" + sp)[:200] or ""
            if scene_prompt:
                return self.generate_scene_image(scene_prompt, model_routes, reference_image=kwargs.get('reference_image', ''), project_id=kwargs.get('project_id', 0))
            return AgentResult(success=False, error="无场景提示词")
        if action == "design" or action == "generate":
            # 优先：前端 StoryboardEditor 单镜生图，直接用 shot_description
            shot_desc = kwargs.get("shot_description", "")
            if action == "generate" and shot_desc:
                logger.info(f"[SceneAgent] 单镜生图: {shot_desc[:80]}...")
                return self.generate_scene_image(shot_desc, model_routes, reference_image=kwargs.get('reference_image', ''), genre=kwargs.get('genre', ''), project_id=kwargs.get('project_id', 0))
            
            result = self.design_scene(
                kwargs.get("scene_name", ""),
                kwargs.get("script_context", ""),
                model_routes
            )
            if result.success:
                scene_prompt = result.data.get("scene_prompt", "")
                if scene_prompt:
                    img_result = self.generate_scene_image(scene_prompt, model_routes, reference_image=kwargs.get('reference_image', ''))
                    if img_result.success:
                        result.data["image_url"] = img_result.data.get("image_url", "")
                        result.data["image_model"] = img_result.data.get("model", "")
            return result
        elif action == "batch_generate":
            """批量并发生图 — 每张图独立线程 + 30秒硬超时，不卡死"""
            from concurrent.futures import ThreadPoolExecutor, as_completed
            shots = kwargs.get("shots", [])
            genre_val = kwargs.get("genre", "都市")
            # Build character photo lookup for face preservation (锁脸)
            characters = kwargs.get("characters", [])
            char_photo_map = {}
            if characters:
                for ch in characters:
                    name = ch.get("name", "")
                    if name:
                        photo = ch.get("photo", ch.get("avatar", ch.get("image_url", "")))
                        if photo and (str(photo).startswith("http") or str(photo).startswith("/storage")):
                            # Convert local /storage/ path to public HTTPS URL
                            if str(photo).startswith("/storage/"):
                                photo = BASE_URL + photo
                            char_photo_map[name] = photo
            results = []
            image_map = {}

            def _gen_one(shot: dict, idx: int) -> tuple:
                desc = shot.get("description", shot.get("scene_description", shot.get("content", "")))
                env = shot.get("environment", shot.get("scene", ""))
                # === Find character reference image for this shot (锁脸) ===
                reference_image = ""
                if char_photo_map:
                    # 1) Check char_ages — explicit per-shot character mapping from wardrobe stage
                    char_ages = shot.get("char_ages", {})
                    if isinstance(char_ages, dict):
                        for char_name in char_ages:
                            if char_name in char_photo_map:
                                reference_image = char_photo_map[char_name]
                                break
                    # 2) Fallback: scan description / dialogue for character names
                    if not reference_image:
                        shot_text = f"{desc} {shot.get('dialogue', '')} {shot.get('scene', '')}"
                        for char_name in char_photo_map:
                            if char_name in shot_text:
                                reference_image = char_photo_map[char_name]
                                break

                # === Agent Toolkit: 场景生图前优化 + 一致性校验 ===
                if getattr(self, "tool_registry", None):
                    try:
                        mood = kwargs.get("mood", "")
                        time_of_day = kwargs.get("time", "")
                        genre_str = kwargs.get("genre", "")
                        script_ctx = shot.get("script_context", shot.get("description", desc))
                        
                        tool_check = self._try_tool_redo([
                            {"name": "scene_atmosphere_prompt",
                             "params": {"scene_description": desc, "mood": mood, "time_of_day": time_of_day}, "weight": 0.3},
                            {"name": "scene_consistency_check",
                             "params": {"scene_description": desc, "script_context": script_ctx, "expected_mood": mood}, "weight": 1.0},
                        ], min_score=65)
                        
                        # 用氛围工具优化后的 prompt 替换原始描述
                        for tr in tool_check.get("tool_results", []):
                            if tr.get("data", {}).get("prompt"):
                                desc = tr["data"]["prompt"]
                                logger.info(f"镜头{idx} 氛围prompt已优化")
                                break
                        
                        if tool_check["should_redo"]:
                            logger.info(f"镜头{idx} 一致性校验低分({tool_check['score']:.0f})，强制重试")
                            # 用反馈增强描述确保与剧本一致
                            desc = desc + "\n确保包含剧本关键元素，情感基调与剧本一致"
                    except Exception:
                        pass

                prompt = f"{desc}，环境：{env}，风格：{genre_val}短剧场景，cinematic lighting，8K，photorealistic，hyper-realistic，shot on ARRI Alexa 65，real photography，live-action film still，NOT CGI NOT cartoon NOT anime NOT illustration NOT painting NOT 3D render"
                try:
                    img_result = self.generate_scene_image(prompt, model_routes, reference_image=reference_image)
                    if img_result and img_result.success:
                        url = img_result.data.get("image_url", "")
                        return (idx, url, True)
                except Exception as e:
                    logger.warning(f"[SceneAgent] 镜头{idx}生图失败: {e}")
                return (idx, "", False)
            
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_gen_one, s, i): i for i, s in enumerate(shots[:8])}
                for f in as_completed(futures, timeout=120):
                    try:
                        idx, url, ok = f.result()
                        results.append({"shot_index": idx, "image_url": url, "success": ok})
                        if url:
                            image_map[str(idx)] = url
                        if url and idx < len(shots):
                            shots[idx]["image_url"] = url
                    except Exception as e:
                        idx = futures.get(f, 0)
                        logger.warning(f"[SceneAgent] 镜头{idx}超时: {e}")
                        results.append({"shot_index": idx, "image_url": "", "success": False})
            return AgentResult(data={
                "images": results,
                "image_map": image_map,
                "total": len(shots),
                "success_count": sum(1 for r in results if r["success"])
            })
        elif action == "gen_image_direct":
            return self.generate_scene_image(
                kwargs.get("scene_prompt", kwargs.get("prompt", "")),
                model_routes
            )
        return AgentResult(success=False, error=f"未知动作: {action}")