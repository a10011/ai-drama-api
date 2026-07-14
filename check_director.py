#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
r = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011152' AND stage='director' ORDER BY id DESC LIMIT 1").fetchall()
d = json.loads(r[0]["data"] or "{}")
tasks = d.get("tasks", {})
print("tasks type:", type(tasks).__name__)
if isinstance(tasks, dict):
    print("tasks keys:", list(tasks.keys()))
    for k in list(tasks.keys())[:3]:
        v = tasks[k]
        print("\n--- %s ---" % k)
        print(json.dumps(v, ensure_ascii=False, indent=2)[:600])
elif isinstance(tasks, list):
    print("tasks count: %d" % len(tasks))
    if tasks:
        print(json.dumps(tasks[0], ensure_ascii=False, indent=2)[:600])
analysis = d.get("analysis", {})
print("\n=== analysis ===")
print(json.dumps(analysis, ensure_ascii=False)[:400])
