# -*- coding: utf-8 -*-
"""
VideoAgent V3 — 视频片段生成
模型: AgnesAI video-v2.0 | 2 req/min | 720p | num_frames=8n+1
"""
import json, logging, time
from core.agent_base_v3 import AgentV3
from services.ai_providers import agnes
from prompt_engine import build_video_prompt

logger = logging.getLogger(__name__)


class VideoAgent(AgentV3):
    name = "video"

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        scene_images = data.get("scene_images", [])
        storyboard = data.get("storyboard", data.get("shots", []))
        audio_files = data.get("audio_files", [])
        genre = data.get("genre", "现代")
        logger.info(f"[VideoAgent] received: scene_images={len(scene_images) if isinstance(scene_images, list) else type(scene_images).__name__}, storyboard={len(storyboard) if isinstance(storyboard, list) else type(storyboard).__name__}")

        # 从分镜直接取台词（不再依赖AudioAgent）
        if not audio_files and storyboard:
            audio_files = []
            for s in storyboard:
                dlg = s.get("dialogue", "")
                if dlg:
                    audio_files.append({"shot_num": s.get("shot_num",0), "text": dlg, "character": s.get("focus_character","")})
        pipeline_id = task.get("pipeline_id", "")

        # 兼容：scene_images可能是JSON字符串
        if isinstance(scene_images, str):
            try: scene_images = json.loads(scene_images)
            except: scene_images = []
        if isinstance(storyboard, str):
            try: storyboard = json.loads(storyboard)
            except: storyboard = []
        
        if not scene_images:
            logger.warning("[VideoAgent] 无场景图, 跳过视频生成")
            return {"success": True, "video_clips": [], "pipeline_id": pipeline_id}

        # 建立音频映射：场景→配音文本
        audio_map = {}
        for a in audio_files:
            sid = str(a.get("scene_id", a.get("shot_num", "")))
            audio_map[sid] = a.get("text", "")

        video_clips = []
        for i, img in enumerate(scene_images):
            shot_num = img.get("shot_num", i + 1)
            img_url = img.get("url", "")
            
            if not img_url:
                continue

            # 找对应分镜描述
            shot_desc = ""
            for s in storyboard:
                if s.get("shot_num", s.get("shot_id", 0)) == shot_num:
                    shot_desc = s
                    break
            if not shot_desc:
                shot_desc = img

            # 音频文本
            audio_text = audio_map.get(str(shot_num), "")
            
            # 构建视频提示词（T2V或I2V根据是否有参考图）
            has_ref = bool(img_url)
            prompt = build_video_prompt(shot_desc, audio_text, genre, reference_image=has_ref)
            
            # 调用AgnesAI视频生成（使用分镜时长，2-18秒）
            shot_duration = shot_desc.get("duration_sec", 3)
            if isinstance(shot_duration, (int, float)):
                shot_duration = max(2, min(18, int(shot_duration)))
            else:
                shot_duration = 3
            
            try:
                result = agnes.generate_video(
                    prompt=prompt,
                    image_url=img_url,
                    duration=shot_duration,
                    resolution="720p",
                    max_wait=600,
                )
                
                if result.get("success"):
                    video_url = result.get("video_url", "")
                    if video_url:
                        video_clips.append({
                            "shot_num": shot_num,
                            "video_url": video_url,
                            "image_url": img_url,
                            "prompt": prompt,
                            "duration": 2,
                        })
                        logger.info(f"[VideoAgent] 镜{shot_num} 视频生成成功")
                    else:
                        logger.warning(f"[VideoAgent] 镜{shot_num} 无视频URL")
                else:
                    logger.warning(f"[VideoAgent] 镜{shot_num} 失败: {result.get('error','')[:80]}")
                    
            except Exception as e:
                logger.error(f"[VideoAgent] 镜{shot_num} 异常: {e}")
            
            # 限流：1次/分钟，等65秒确保不超
            if i < len(scene_images) - 1:
                logger.info("[VideoAgent] 限流等待65s (1 req/min)...")
                time.sleep(65)

        return {
            "success": len(video_clips) > 0,
            "video_clips": video_clips,
            "pipeline_id": pipeline_id,
        }

    def _check_memory(self, task: dict) -> dict | None:
        return None  # 视频生成不缓存

    def _find_similar_memory(self, task: dict) -> list:
        return []
