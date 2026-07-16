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
        director_analysis = data.get("director_analysis", {})
        visual_style = data.get("visual_style", "")
        sfx_plan = data.get("sfx_plan", "")
        prop_plan = data.get("prop_plan", "")
        logger.info(f"[VideoAgent] received: scene_images={len(scene_images) if isinstance(scene_images, list) else type(scene_images).__name__}, storyboard={len(storyboard) if isinstance(storyboard, list) else type(storyboard).__name__}")
        if director_analysis:
            logger.info(f"[VideoAgent] director_analysis loaded, has tasks={bool(director_analysis.get('tasks'))}")

        # 必须有场景图
        if not scene_images:
            return {"success": False, "error": "导演未提供场景图数据", "pipeline_id": task.get("pipeline_id", "")}

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
            return {"success": False, "error": "导演未提供场景图数据", "pipeline_id": pipeline_id}

        # 建立音频映射：场景→配音文本
        audio_map = {}
        for a in audio_files:
            sid = str(a.get("scene_id", a.get("shot_num", "")))
            audio_map[sid] = a.get("text", "")

        video_clips = []
        seen_urls = set()  # 去重：同一图片URL只生成一次视频
        for i, img in enumerate(scene_images):
            shot_num = img.get("shot_num", i + 1)
            img_url = img.get("url", "")
            
            if not img_url:
                continue

            # 去重：如果这张图已经生成过视频，直接复用
            if img_url in seen_urls:
                logger.info(f"[VideoAgent] 镜{shot_num} 场景图已生成过视频，跳过重复生成")
                # 从已有的 clips 中找到对应的视频 URL
                for existing in video_clips:
                    if existing.get("image_url") == img_url:
                        video_clips.append({
                            "shot_num": shot_num,
                            "video_url": existing["video_url"],
                            "image_url": img_url,
                            "prompt": existing.get("prompt", ""),
                            "duration": img.get("duration_sec", existing.get("duration", 3)),
                        })
                        break
                continue

            seen_urls.add(img_url)

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
            prompt = build_video_prompt(shot_desc, audio_text, genre, reference_image=has_ref, sfx_plan=sfx_plan, prop_plan=prop_plan)
            
            # 调用AgnesAI视频生成（使用分镜时长，2-18秒）
            shot_duration = shot_desc.get("duration_sec", 3)
            if isinstance(shot_duration, (int, float)):
                shot_duration = max(2, min(18, int(shot_duration)))
            else:
                shot_duration = 3
            
            try:
                # Agnes Video V2.0: 异步任务，先创建再轮询
                result = agnes.generate_video(
                    prompt=prompt,
                    image_url=img_url,
                    duration=shot_duration,
                    resolution="720p",
                    max_wait=600,
                    width=1152,
                    height=768,
                    num_frames=121,
                    frame_rate=24,
                )
                
                if result.get("success"):
                    video_url = result.get("video_url", "")
                    if video_url:
                        # 下载到本地存储
                        import requests, os, hashlib
                        try:
                            os.makedirs('/www/wwwroot/storage/videos', exist_ok=True)
                            r = requests.get(video_url, timeout=120)
                            fname = f'shot{shot_num}_{hashlib.md5(video_url.encode()).hexdigest()[:8]}.mp4'
                            fpath = f'/www/wwwroot/storage/videos/{fname}'
                            with open(fpath, 'wb') as f:
                                f.write(r.content)
                            logger.info(f"[VideoAgent] 镜{shot_num} 视频已下载到 {fpath}")
                        except Exception as e:
                            logger.warning(f"[VideoAgent] 镜{shot_num} 下载失败: {e}")
                        
                        video_clips.append({
                            "shot_num": shot_num,
                            "video_url": video_url,
                            "image_url": img_url,
                            "prompt": prompt,
                            "duration": shot_duration,
                        })
                        logger.info(f"[VideoAgent] 镜{shot_num} 视频生成成功")
                    else:
                        logger.warning(f"[VideoAgent] 镜{shot_num} 无视频URL")
                        return {"success": False, "error": f"镜{shot_num} 视频生成无URL", "pipeline_id": pipeline_id}
                else:
                    logger.warning(f"[VideoAgent] 镜{shot_num} 失败: {result.get('error','')[:80]}")
                    return {"success": False, "error": f"镜{shot_num} 生成失败: {result.get('error','')[:80]}", "pipeline_id": pipeline_id}
                    
            except Exception as e:
                logger.error(f"[VideoAgent] 镜{shot_num} 异常: {e}")
            
            # 限流：1次/分钟，等65秒确保不超
            if i < len(scene_images) - 1:
                logger.info("[VideoAgent] 限流等待65s (1 req/min)...")
                time.sleep(65)

        if not video_clips:
            return {"success": False, "error": "所有镜头视频生成失败", "pipeline_id": pipeline_id}

        # 记录用量
        try:
            from services.usage_tracker import log_usage
            total_dur = sum(c.get("duration", 3) for c in video_clips)
            log_usage(
                model_name="seedance",
                provider="agnes",
                model_type="video",
                status="success",
                user_id=task.get("user_id", 0),
                drama_id=pipeline_id,
                video_duration=total_dur,
            )
        except Exception as e:
            logger.warning(f"[VideoAgent] 记录用量失败: {e}")
        
        return {
            "success": True,
            "video_clips": video_clips,
            "pipeline_id": pipeline_id,
        }

    def _check_memory(self, task: dict) -> dict | None:
        return None  # 视频生成不缓存

    def _find_similar_memory(self, task: dict) -> list:
        return []
