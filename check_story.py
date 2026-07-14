#!/usr/bin/env python3
"""查看10011153的剧本+分镜+导演分析，诊断故事感缺失"""
import sqlite3, json

db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row

# 剧本
r = db.execute("SELECT script,title,genre FROM projects WHERE id='10011153'").fetchall()
if r:
    print("=" * 60)
    print("剧本标题: %s | 类型: %s" % (r[0]["title"], r[0]["genre"]))
    print("剧本全文(%d字):" % len(r[0]["script"] or ""))
    print(r[0]["script"] or "(空)")

# 导演分析
r2 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011153' AND stage='director' ORDER BY id DESC LIMIT 1").fetchall()
if r2:
    d = json.loads(r2[0]["data"] or "{}")
    print("\n" + "=" * 60)
    print("导演分析:")
    analysis = d.get("analysis", {})
    if isinstance(analysis, dict):
        for k, v in analysis.items():
            if isinstance(v, str):
                print("  %s: %s" % (k, v[:200]))

# 分镜
r3 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011153' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
if r3:
    d = json.loads(r3[0]["data"] or "{}")
    shots = d.get("shots", [])
    print("\n" + "=" * 60)
    print("分镜(%d个):" % len(shots))
    for i, s in enumerate(shots):
        desc = s.get("description", "")
        dialogue = s.get("dialogue", "")
        emotion = s.get("emotion", "")
        scene = s.get("scene", "")
        print("\n--- shot[%d] %s ---" % (i, scene))
        print("  描述: %s" % desc[:100])
        print("  台词: %s" % dialogue)
        print("  情绪: %s" % emotion)
        print("  景别: %s | 运镜: %s" % (s.get("shot_type", ""), s.get("camera_movement", "")))

db.close()
