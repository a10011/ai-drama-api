#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
r = db.execute("SELECT script FROM projects WHERE id='10011153'").fetchall()
script = r[0]["script"] if r else ""
print("=== 剧本开头500字 ===")
print(script[:500])
r2 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011153' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
d = json.loads(r2[0]["data"] or "{}")
shots = d.get("shots", [])
print("\n=== 分镜%d个 ===" % len(shots))
for i, s in enumerate(shots):
    print("shot[%d] %s: %s" % (i, s.get("scene",""), s.get("description","")[:60]))
db.close()
