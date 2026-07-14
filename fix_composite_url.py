#!/usr/bin/env python3
"""把最终视频 URL 写入 composite 阶段 data + 更新项目状态为 completed"""
import sqlite3, json, time, glob, os

PROJECT_ID = "10011152"
PID = "pipe_1782890903_10011152"
DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"
BASE_URL = "https://ai.mzsh.top"

# 找最终视频文件
pattern = f"/www/wwwroot/storage/{PID}/videos/final_*.mp4"
matches = glob.glob(pattern)
if not matches:
    pattern = f"/www/wwwroot/storage/videos/*{PROJECT_ID}*.mp4"
    matches = glob.glob(pattern)
if not matches:
    # 兜底：取 pipe 目录最新
    matches = glob.glob(f"/www/wwwroot/storage/{PID}/videos/*.mp4")

matches.sort(key=os.path.getmtime, reverse=True)
if not matches:
    print("ERROR: 找不到最终视频文件"); exit(1)

final_path = matches[0]
final_url = BASE_URL + final_path.replace("/www/wwwroot", "")
print(f"最终视频: {final_url} ({os.path.getsize(final_path)//1024}KB)")

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row
now = time.strftime("%Y-%m-%dT%H:%M:%S")

# 更新 composite 阶段 data
rows = db.execute("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='composite' ORDER BY id DESC LIMIT 1", (PROJECT_ID,)).fetchall()
existing_data = {}
if rows:
    existing_data = json.loads(rows[0]["data"] or "{}")
existing_data["video_url"] = final_url
existing_data["final_video_url"] = final_url
existing_data["duration_sec"] = 80
db.execute(
    "UPDATE pipeline_progress SET status='completed', data=?, finished_at=? WHERE project_id=? AND stage='composite'",
    (json.dumps(existing_data, ensure_ascii=False), now, PROJECT_ID))

# 更新项目状态
db.execute("UPDATE projects SET status='completed', progress=100 WHERE id=?", (PROJECT_ID,))

# 更新 pipelines 表
db.execute("UPDATE pipelines SET status='completed', current_stage='composite' WHERE project_id=? ORDER BY id DESC LIMIT 1", (PROJECT_ID,))

db.commit()
print("composite data 已更新")
print("项目状态 → completed, progress=100")
db.close()
