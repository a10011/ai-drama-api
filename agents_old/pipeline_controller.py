"""流水线调度控制器 — 一键生成短剧全流程编排

变更: 集成总导演智能体，每个镜头注入导演指令（情绪/语气/表情/动作/氛围）
"""
import asyncio
import time
import logging
from typing import Dict, List, Optional, Callable

logger = logging.getLogger(__name__)

# 各阶段依赖关系
STAGE_DEPS = {
    "scene_image": [],
    "character_image": [],
    "face_lock": ["character_image"],
    "video": ["scene_image"],
    "tts": [],
    "bgm": [],
    "subtitle": ["tts"],
    "composite": ["scene_image", "character_image", "video", "tts", "bgm", "subtitle"],
}

STAGE_POOL_LABEL = {
    "scene_image": "image",
    "character_image": "image",
    "face_lock": "face",
    "video": "video",
    "tts": "tts",
    "bgm": "bgm",
    "subtitle": "subtitle",
    "composite": "composite",
}


class PipelineController:
    def __init__(self, max_concurrent=5, pool=None):
        self.max_concurrent = max_concurrent
        if pool:
            self.pool = pool
        else:
            from .concurrency_pool import concurrency_pool
            self.pool = concurrency_pool

    async def run(self, project_id, shots, config=None, script_text: str = ""):
        start = time.time()
        config = config or {}
        model_routes = config.get("model_routes", {})
        agent_instructions = config.get("agent_instructions", {})

        # ============================================================
        # 阶段0: 并行 — 分镜生成 + 角色提取，角色提取完跑造型
        logger.info("阶段0: 并行 分镜生成 + 角色提取...")

        # 0a: 提取角色（从剧本取角色）
        from .agent_script import ScriptAgent
        script_agent = ScriptAgent()
        char_result = script_agent.extract_characters(script_text)
        raw_chars = char_result.data.get("characters", []) if char_result.success else []

        # 同时跑分镜——如果外部没传 shots，自动生成
        if not shots:
            from .agent_storyboard import StoryboardAgent
            sb_agent = StoryboardAgent()
            sb_params = {
                "script_text": script_text,
                "characters": raw_chars,
                "title": project_id,
                "max_shots": config.get("max_shots", 12),
                "costume_models": [],
            }
            # 唯一入口 execute() — 见 agent_contract.md
            sb_result = sb_agent.execute(script_text=script_text, characters=raw_chars, title=project_id, max_shots=config.get("max_shots", 12), costume_models=[], genre=config.get("genre", ""))
            if sb_result.success and sb_result.data.get("shots"):
                shots = sb_result.data["shots"]

        # 0b: 角色造型（等角色提取完才跑）
        costume_models = []
        costume_chars = raw_chars or []
        if costume_chars:
            from .agent_costume import CostumeAgent
            costume_agent = CostumeAgent()
            costume_result = costume_agent.design_batch(costume_chars, script_text[:600], config.get("genre", ""))
            if costume_result.success:
                costume_models = costume_result.data.get("models", [])
                logger.info(f"阶段0: 完成 {len(costume_models)} 个角色造型设计")

        # 0c: 总导演分析每个镜头的指令（等分镜生成完）
        if not shots:
            logger.warning("阶段0: 无分镜数据，跳过导演分析")
            return {"stage": "pipeline", "status": "failed", "error": "无分镜数据"}

        from .agent_director import DirectorAgent, inject_director_instructions, build_video_prompt_with_director, build_tts_params_with_director
        director_agent = DirectorAgent()
        # 唯一入口 execute() — 只接受 script + shots
        director_result = director_agent.execute(script=script_text, shots=shots)
        if director_result and director_result.success:
            inject_director_instructions(shots, director_result.data)
            logger.info("阶段0: 导演指令注入完成")

        # 组装阶段0结果
        stage_results = {"costume_models": costume_models}
        if director_result and director_result.success:
            stage_results["director"] = director_result.data
        # === 阶段1: 场景图 + 立绘（并行，无依赖）===
        scene_tasks = []
        char_tasks = []
        seen_chars = set()

        for i, shot in enumerate(shots):
            sid = shot.get("shot_id", i)
            scene_tasks.append(
                self._run_wrapper("scene_image", sid, self._gen_scene, shot, model_routes)
            )
            char_name = shot.get("character", "")
            if char_name and char_name not in seen_chars:
                seen_chars.add(char_name)
                char_tasks.append(
                    self._run_wrapper("character_image", char_name, self._gen_char, shot, model_routes)
                )

        logger.info("阶段1: %d场景图 + %d立绘" % (len(scene_tasks), len(char_tasks)))
        all_results = await asyncio.gather(
            *scene_tasks, *char_tasks, return_exceptions=True
        )

        for r in all_results:
            if isinstance(r, Exception):
                logger.error("阶段1异常: %s" % r)
            elif isinstance(r, dict):
                stage_results.update(r)

        # === 注入场景图URL到shots中（供stage2视频生成使用）===
        for i, shot in enumerate(shots):
            sid = shot.get("shot_id", i)
            scene_key = "scene_image_%s" % sid
            scene_data = stage_results.get(scene_key, {})
            if isinstance(scene_data, dict):
                img_url = scene_data.get("image_url", "")
                if img_url:
                    shot["image_url"] = img_url
            char_name = shot.get("character", "")
            if char_name:
                char_key = "character_image_%s" % char_name
                char_data = stage_results.get(char_key, {})
                if isinstance(char_data, dict):
                    char_url = char_data.get("image_url", char_data.get("figure_url", ""))
                    if char_url:
                        shot["character_image"] = char_url

        # === 阶段2: 锁脸 + 视频 + TTS + BGM + 字幕（并行）===
        stage2 = []
        face_lock = agent_instructions.get("character", {}).get("face_lock", False)

        for i, shot in enumerate(shots):
            sid = shot.get("shot_id", i)
            char_name = shot.get("character", "")

            if char_name and face_lock:
                stage2.append(
                    self._run_wrapper("face_lock", "%s_%s" % (sid, char_name),
                                      self._gen_face, shot, model_routes)
                )

            # 视频 — 带导演指令
            stage2.append(
                self._run_wrapper("video", sid, self._gen_video, shot, model_routes, audio_url=stage_results.get("tts_%s" % sid, {}).get("audio_url", ""))
            )

            if shot.get("dialogue"):
                # TTS — 带导演指令（情绪/语气）
                stage2.append(
                    self._run_wrapper("tts", sid, self._gen_tts, shot, config)
                )
                stage2.append(
                    self._run_wrapper("subtitle", sid, self._gen_sub, shot)
                )

        # BGM 一次
        stage2.append(
            self._run_wrapper("bgm", "main", self._gen_bgm, shots, config)
        )

        logger.info("阶段2: %d个任务" % len(stage2))
        results2 = await asyncio.gather(*stage2, return_exceptions=True)
        for r in results2:
            if isinstance(r, Exception):
                logger.error("阶段2异常: %s" % r)
            elif isinstance(r, dict):
                stage_results.update(r)

        # === 阶段3: 合成 ===
        comp_result = await self._run_wrapper(
            "composite", project_id, self._gen_comp, shots, stage_results, config
        )
        if isinstance(comp_result, dict):
            stage_results.update(comp_result)

        elapsed = time.time() - start
        logger.info("流水线完成: %.1fs" % elapsed)

        return {
            "project_id": project_id,
            "total_shots": len(shots),
            "stages": stage_results,
            "duration_sec": round(elapsed, 1),
            "output": (comp_result or {}).get("output", "") if isinstance(comp_result, dict) else "",
        }

    async def _run_director(self, script_text: str, shots: list, config: dict) -> dict:
        """调用总导演智能体分析所有分镜"""
        try:
            from .agent_director import DirectorAgent
            director = DirectorAgent()
            result = await asyncio.to_thread(
                director.analyze_all_shots,
                script=script_text or "",
                shots=shots
            )
            if result and result.success:
                return result.data
            return {}
        except Exception as e:
            logger.warning(f"总导演分析失败(继续执行): {e}")
            return {}

    def run_sync(self, project_id, shots, config=None, script_text: str = ""):
        try:
            loop = asyncio.get_running_loop()
            # Already in an event loop - run in a new thread
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.run(project_id, shots, config, script_text))
                return future.result(timeout=900)
        except RuntimeError:
            pass
        # No running loop - normal path
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.run(project_id, shots, config, script_text))
        finally:
            loop.close()

    async def _run_wrapper(self, stage, key, func, *args, **kwargs):
        try:
            if kwargs:
                from functools import partial
                result = await asyncio.to_thread(partial(func, **kwargs), *args)
            else:
                result = await asyncio.to_thread(func, *args)
            return { ("%s_%s" % (stage, key)): result }
        except Exception as e:
            logger.error("阶段%s[%s]异常: %s" % (stage, key, e))
            return None

    def _gen_scene(self, shot, model_routes):
        from .agent_scene import SceneAgent
        a = SceneAgent()
        return a.execute(shot=shot).data

    def _gen_char(self, shot, _model_routes=None):
        """角色生图 — 固定走 seedream，不做模型路由"""
        from .agent_character import CharacterAgent
        a = CharacterAgent()
        char_name = shot.get("character", shot.get("character_name", ""))
        outfit = ""
        props = ""
        char_age = ""
        reference_image = ""
        if isinstance(shot.get("outfit"), dict):
            outfit = shot["outfit"].get(char_name, "")
        if isinstance(shot.get("props"), dict):
            props = shot["props"].get(char_name, "")
        if isinstance(shot.get("char_ages"), dict):
            char_age = shot["char_ages"].get(char_name, "")
        char_img = shot.get("character_image", shot.get("image_url", ""))
        if char_img and (char_img.startswith("http://") or char_img.startswith("https://")):
            reference_image = char_img
        merged = dict(shot)
        merged["outfit"] = outfit
        merged["props"] = props
        merged["char_age"] = char_age
        merged["reference_image"] = reference_image
        return a.execute(shot=merged, reference_image=reference_image, outfit=outfit, props=props, char_age=char_age).data

    def _gen_face(self, shot, model_routes):
        from .agent_character import CharacterAgent
        a = CharacterAgent()
        char_name = shot.get("character", shot.get("character_name", ""))
        char_data = shot.get("char_data", shot.get("character_data", {}))
        ref_image = shot.get("ref_image", shot.get("face_image", ""))
        return a.run(
            action="generate_face_locked_figure",
            char_name=char_name,
            char_data=char_data,
            ref_image=ref_image
        ).data

    def _gen_video(self, shot, model_routes, audio_url=""):
        from .agent_video import VideoAgent
        from .agent_director import build_video_prompt_with_director
        a = VideoAgent()

        # 传音频给 video agent
        shot = dict(shot)
        if audio_url:
            shot["audio_url"] = audio_url

        # 用导演指令构建增强的 video prompt
        enhanced_prompt = build_video_prompt_with_director(shot)
        if enhanced_prompt:
            if shot.get("scene_description"):
                shot["prompt"] = enhanced_prompt
            elif shot.get("prompt"):
                shot["prompt"] = enhanced_prompt

        return a.execute(shot=shot, audio_url=audio_url).data

    def _gen_tts(self, shot, config):
        from .agent_tts import TTSAgent
        from services.ai_providers import _cv2_resolve_voice
        from .agent_tts import EMOTION_VOICE_MAP
        a = TTSAgent()

        emotion = shot.get("_emotion", "平静")
        tone = shot.get("_tone", "正常")

        # 从情绪映射表获取推荐音色
        emo_params = EMOTION_VOICE_MAP.get(emotion, EMOTION_VOICE_MAP["默认"])
        voice = emo_params["voice"]
        speed = 1.0

        # 按角色性别年龄覆盖音色（如果 shot 有角色信息）
        char_gender = shot.get("gender", "")
        char_age = shot.get("age", "")
        if char_gender and char_age:
            from services.ai_providers import auto_select_voice
            voice = auto_select_voice(char_gender, char_age)

        tts_config = {
            "voice": voice,
            "speed": speed,
            "emotion": emotion,
            "tone": tone,
        }
        if isinstance(config, dict):
            tts_config.update(config)

        return a.execute(shot={"dialogue": shot.get("dialogue", "")}, config=tts_config).data

    def _gen_bgm(self, shots, config):
        from .agent_bgm import BGMAgent
        a = BGMAgent()
        return a.execute(shots=shots, config=config).data

    def _gen_sub(self, shot):
        from .agent_subtitle import SubtitleAgent
        a = SubtitleAgent()
        return a.execute(shot=shot).data

    def _gen_comp(self, shots, stage_results, config):
        from .agent_composite import CompositeAgent
        import re, os

        a = CompositeAgent()

        clips = []

        bgm_path = ""
        bgm_data = stage_results.get("bgm_main", {})
        if isinstance(bgm_data, dict):
            bgm_path = bgm_data.get("bgm_path", bgm_data.get("audio_path", ""))

        for i, shot in enumerate(shots):
            shot_id = shot.get("shot_id", i)
            sid = shot_id

            clip = {}

            video_key = "video_%s" % sid
            video_data = stage_results.get(video_key, {})
            if isinstance(video_data, dict):
                clip["video"] = video_data.get("video_url", "")

            tts_key = "tts_%s" % sid
            tts_data = stage_results.get(tts_key, {})
            if isinstance(tts_data, dict):
                audio_url = tts_data.get("audio_url", "")
                clip["audio"] = audio_url

            if bgm_path:
                clip["bgm"] = bgm_path

            sub_key = "subtitle_%s" % sid
            sub_data = stage_results.get(sub_key, {})
            if isinstance(sub_data, dict):
                srt_text = sub_data.get("subtitle_text", "")
                if srt_text:
                    srt_dir = config.get("workdir", "/tmp") if isinstance(config, dict) else "/tmp"
                    os.makedirs(srt_dir, exist_ok=True)
                    srt_path = os.path.join(srt_dir, "shot_%s_subtitle.srt" % sid)
                    try:
                        with open(srt_path, "w") as f:
                            f.write(srt_text)
                        clip["subtitle"] = srt_path
                    except Exception as ex_: logger.warning(f"[pipeline_controller]  {ex_}")

            clips.append(clip)

        return a.execute(clips=clips, output_path="", workdir="/tmp").data
