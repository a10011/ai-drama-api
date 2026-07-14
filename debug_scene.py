#!/usr/bin/env python3
"""调试：找出6个未匹配镜头的场景图"""
import sqlite3, json

db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
rows = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011152' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
shots = json.loads(rows[0]["data"] or "{}").get("shots", [])
missing = [3,7,10,11,13,14]
print("=== 未匹配镜头的描述 ===")
for i in missing:
    print(f"shot[{i}]:")
    print(f"  desc: {shots[i].get('description','')[:80]}")
    print(f"  scene: {shots[i].get('scene','')[:40]}")
    print(f"  location: {shots[i].get('location','')[:40]}")

# 全部场景图描述
db2 = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/app.db")
db2.row_factory = sqlite3.Row
srows = db2.execute("SELECT file_path,tags FROM media_library WHERE file_path LIKE '%/scenes/%1152%' ORDER BY id DESC").fetchall()
print(f"\n=== 全部场景图描述(去重, 共{len(srows)}条) ===")
seen = set()
for r in srows:
    try:
        tags = json.loads(r["tags"]) if r["tags"] else []
    except Exception:
        tags = []
    dk = tags[0][:50] if tags else ""
    if dk and dk not in seen:
        seen.add(dk)
        print(f"  {dk}")
