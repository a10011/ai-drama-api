#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
r = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011152' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
d = json.loads(r[0]["data"] or "{}")
shots = d.get("shots", [])
print("=== 分镜总数: %d ===" % len(shots))
for i, s in enumerate(shots):
    desc = s.get("description", "")
    dialogue = s.get("dialogue", "")
    action = s.get("action", "")
    scene = s.get("scene", "")
    location = s.get("location", "")
    print("\n--- shot[%d] ---" % i)
    print("  scene: %s" % scene)
    print("  location: %s" % location)
    print("  description(%d字): %s" % (len(desc), desc))
    print("  dialogue(%d字): %s" % (len(dialogue), dialogue))
    print("  action(%d字): %s" % (len(action), action))
