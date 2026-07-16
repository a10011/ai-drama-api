# -*- coding: utf-8 -*-
"""CompositeAgent — 视频合成：合并所有镜头为成品"""
import json, logging, os, subprocess, time, hashlib
from core.agent_base_v3 import AgentV3

logger = logging.getLogger(__name__)


class CompositeAgent(AgentV3):
    name = "composite"

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        clips = data.get("clips", data.get("video_clips", []))
        pid = data.get("project_id", task.get("pipeline_id", "default"))
        title = data.get("title", "短剧")

        if not clips:
            # Try loading from pipeline_progress
            try:
                import sqlite3
                c = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
                c.row_factory = sqlite3.Row
                r = c.execute("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='video' AND status='completed' ORDER BY id DESC LIMIT 1", (str(pid),)).fetchone()
                c.close()
                if r:
                    vd = json.loads(r["data"] or "{}")
                    clips = vd.get("clips", vd.get("videos", []))
            except:
                pass

        if not clips:
            return {"success": False, "error": "导演未提供视频片段数据", "pipeline_id": pid}

        # 收集音频文件
        audio_files = data.get("tts_audio", data.get("audio_files", []))
        audio_map = {}
        for a in audio_files:
            if isinstance(a, dict):
                idx = a.get("shot_index", a.get("shot_num", a.get("index", "")))
                url = a.get("audio_url", a.get("url", ""))
                if idx and url:
                    audio_map[str(idx)] = url

        # Write concat file
        concat_path = "/tmp/concat_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8] + ".txt"
        audio_concat_path = "/tmp/audio_concat_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8] + ".txt"
        
        with open(concat_path, "w") as cf:
            with open(audio_concat_path, "w") as af:
                for clip in clips:
                    url = clip if isinstance(clip, str) else clip.get("video_url", clip.get("url", ""))
                    if url and url.startswith("http"):
                        local = "/tmp/video_" + hashlib.md5(url.encode()).hexdigest()[:8] + ".mp4"
                        try:
                            import requests
                            r = requests.get(url, timeout=300)
                            if r.status_code == 200:
                                with open(local, "wb") as lf:
                                    lf.write(r.content)
                                cf.write("file '" + local + "'\n")
                        except:
                            continue
                    elif url and os.path.exists(url):
                        cf.write("file '" + url + "'\n")

                    # 收集对应音频
                    clip_idx = str(clip.get("shot_index", clip.get("shot_num", ""))) if isinstance(clip, dict) else ""
                    if clip_idx and clip_idx in audio_map:
                        audio_url = audio_map[clip_idx]
                        if audio_url and audio_url.startswith("http"):
                            audio_local = "/tmp/audio_" + hashlib.md5(audio_url.encode()).hexdigest()[:8] + ".mp3"
                            try:
                                import requests
                                r = requests.get(audio_url, timeout=300)
                                if r.status_code == 200:
                                    with open(audio_local, "wb") as lf:
                                        lf.write(r.content)
                                    af.write("file '" + audio_local + "'\n")
                            except:
                                pass
                        elif audio_url and os.path.exists(audio_url):
                            af.write("file '" + audio_url + "'\n")

        # FFmpeg concat 视频
        out_dir = "/www/wwwroot/storage/videos"
        os.makedirs(out_dir, exist_ok=True)
        out_name = str(pid).replace("/", "_") + "_final.mp4"
        out_path = os.path.join(out_dir, out_name)

        try:
            # 检查是否有音频需要混音
            has_audio = False
            if os.path.exists(audio_concat_path) and os.path.getsize(audio_concat_path) > 0:
                has_audio = True

            if has_audio:
                # 先合并音频
                audio_out = "/tmp/audio_concat_out.mp3"
                subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", audio_concat_path, "-c", "copy", audio_out, "-y"],
                              capture_output=True, timeout=600)

                # 视频+音频混音合成
                subprocess.run(["ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", concat_path,
                    "-i", audio_out,
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-shortest",
                    out_path],
                  capture_output=True, timeout=600)
            else:
                # 纯视频合并
                subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_path,
                              "-c", "copy", out_path, "-y"],
                          capture_output=True, timeout=600)

            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                url = "https://ai.mzsh.top/storage/videos/" + out_name
                logger.info("[Composite] done: " + url)
                
                # 记录用量
                try:
                    from services.usage_tracker import log_usage
                    log_usage(
                        model_name="composite",
                        provider="local",
                        model_type="composite",
                        status="success",
                        user_id=task.get("user_id", 0),
                        drama_id=pid,
                    )
                except Exception as e:
                    logger.warning(f"[CompositeAgent] 记录用量失败: {e}")
                
                return {"success": True, "video_url": url, "local_path": out_path, "pipeline_id": pid}
            return {"success": False, "error": "合成后文件为空", "pipeline_id": pid}
        except Exception as e:
            logger.error("[Composite] " + str(e))
            return {"success": False, "error": str(e)[:200], "pipeline_id": pid}
        finally:
            if os.path.exists(concat_path):
                os.remove(concat_path)
            if os.path.exists(audio_concat_path):
                os.remove(audio_concat_path)
