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
            return {"success": False, "error": "无视频片段"}

        # Write concat file
        concat_path = "/tmp/concat_" + hashlib.md5(str(time.time()).encode()).hexdigest()[:8] + ".txt"
        with open(concat_path, "w") as f:
            for clip in clips:
                url = clip if isinstance(clip, str) else clip.get("video_url", clip.get("url", ""))
                if url and url.startswith("http"):
                    # Download to local first
                    local = "/tmp/video_" + hashlib.md5(url.encode()).hexdigest()[:8] + ".mp4"
                    try:
                        import requests
                        r = requests.get(url, timeout=300)
                        if r.status_code == 200:
                            with open(local, "wb") as lf:
                                lf.write(r.content)
                            f.write("file '" + local + "'\n")
                    except:
                        continue
                elif url and os.path.exists(url):
                    f.write("file '" + url + "'\n")

        # FFmpeg concat
        out_dir = "/www/wwwroot/storage/videos"
        os.makedirs(out_dir, exist_ok=True)
        out_name = str(pid).replace("/", "_") + "_final.mp4"
        out_path = os.path.join(out_dir, out_name)

        try:
            subprocess.run(["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_path, "-c", "copy", out_path, "-y"],
                          capture_output=True, timeout=600)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                url = "https://ai.mzsh.top/storage/videos/" + out_name
                logger.info("[Composite] done: " + url)
                return {"success": True, "data": {"video_url": url, "local_path": out_path}}
            return {"success": False, "error": "合成后文件为空"}
        except Exception as e:
            logger.error("[Composite] " + str(e))
            return {"success": False, "error": str(e)[:200]}
        finally:
            if os.path.exists(concat_path):
                os.remove(concat_path)
