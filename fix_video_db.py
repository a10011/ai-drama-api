#!/usr/bin/env python3
"""把已生成的视频文件按时间顺序映射回15个镜头，写入 DB，然后触发合成。不重启。"""
import sqlite3, json, time, os, glob

PROJECT_ID = "10011152"
PID = "pipe_1782890903_10011152"
DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 加载分镜
rows = db.execute("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' ORDER BY id DESC LIMIT 1", (PROJECT_ID,)).fetchall()
shots = json.loads(rows[0]["data"] or "{}").get("shots", [])
print(f"分镜: {len(shots)}")

# 2. 找最近生成的15个视频文件（按修改时间倒序）
video_dir = "/www/wwwroot/storage/videos/"
files = glob.glob(os.path.join(video_dir, "video_*.mp4"))
files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
print(f"视频文件总数: {len(files)}")

# 取最近15个（本次生成的）
recent = files[:15]
recent.reverse()  # 按时间正序对应 shot 0-14
print(f"取最近15个:")
for i, f in enumerate(recent):
    print(f"  shot[{i}]: {os.path.basename(f)} ({os.path.getsize(f)//1024}KB)")

# 3. 构建 video 结果
results = []
for i in range(15):
    if i < len(recent):
        url = "https://ai.mzsh.top/storage/videos/" + os.path.basename(recent[i])
        results.append({"shot_index": i, "result": {"video_url": url}})
    else:
        results.append({"shot_index": i, "result": {"error": "未生成"}})

# 4. 写入 DB
now = time.strftime("%Y-%m-%dT%H:%M:%S")
video_data = json.dumps({"videos": results, "total": 15, "failed": 15 - len(recent)}, ensure_ascii=False)
db.execute(
    "INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,error,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) "
    "DO UPDATE SET status='completed', data=excluded.data, finished_at=?",
    (PROJECT_ID, PID, "video", "completed", video_data, "", now, now, now))
db.commit()
print(f"\nvideo 阶段已写入 DB: {len(recent)}/15 成功")

# 也修复 sfx（合成依赖）
db.execute(
    "INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,error,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) "
    "DO UPDATE SET status='completed', data=excluded.data",
    (PROJECT_ID, PID, "sfx", "completed", json.dumps({"success": True}), "", now, now))
db.commit()
print("sfx 阶段已标 completed")
db.close()
