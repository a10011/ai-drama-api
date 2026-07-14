#!/usr/bin/env python3
"""管道健康检查脚本"""
import sqlite3, json, urllib.request

result = {"pm2_ok": False, "alerts": [], "summary": ""}

# PM2 check via HTTP health endpoint (more reliable than subprocess)
try:
    r = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=5)
    result["pm2_ok"] = json.loads(r.read()).get("status") == "ok"
except:
    result["pm2_ok"] = False

# Pipeline check
db = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
db.row_factory = sqlite3.Row

# Recent failures (last 30 min, unique stage+pipeline)
failed = db.execute("""
    SELECT DISTINCT pipeline_id, stage, error, started_at FROM pipeline_progress 
    WHERE status='failed' AND started_at > datetime('now','localtime','-30 minutes')
    ORDER BY pipeline_id, stage
""").fetchall()

# Stuck pipelines
stuck = db.execute("""
    SELECT pipeline_id, stage, started_at FROM pipeline_progress 
    WHERE status='running' AND started_at < datetime('now','localtime','-10 minutes')
""").fetchall()

# Latest pipeline
latest = db.execute("""
    SELECT pipeline_id, stage, status FROM pipeline_progress 
    WHERE pipeline_id = (SELECT pipeline_id FROM pipeline_progress ORDER BY id DESC LIMIT 1)
""").fetchall()
done = sum(1 for r in latest if r['status']=='completed')
total = len(latest)
fail = sum(1 for r in latest if r['status']=='failed')
pid = latest[0]['pipeline_id'] if latest else "N/A"

result["summary"] = f"最新管道 {pid[:30]}... {done}/{total} 完成, {fail} 失败"
result["stuck_count"] = len(stuck)
result["recent_fail_count"] = len(failed)

if not result["pm2_ok"]:
    result["alerts"].append("PM2_DOWN")
if stuck:
    result["alerts"].append(f"PIPELINE_STUCK:{stuck[0]['stage']}")
if failed:
    # Group by pipeline
    by_pipe = {}
    for f in failed:
        by_pipe.setdefault(f['pipeline_id'], []).append(f['stage'])
    result["alerts"].append(f"STAGE_FAILED: {len(by_pipe)} 个管道有失败阶段")

db.close()
print(json.dumps(result, ensure_ascii=False))
