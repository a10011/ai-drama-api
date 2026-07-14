#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row

# 角色
r = db.execute("SELECT characters,script FROM projects WHERE id='10011160'").fetchall()
if r:
    chars = json.loads(r[0]["characters"] or "[]")
    script = r[0]["script"] or ""
    print("=== 角色 %d个 ===" % len(chars))
    for ch in chars:
        print("  %s: portrait=%s" % (ch.get("name",""), (ch.get("portrait_url","") or "")[:50]))
    print("\n剧本里玉漱出现次数: %d" % script.count("玉漱"))
    print("剧本里蒙毅出现次数: %d" % script.count("蒙毅"))

# 分镜
r2 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011160' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
if r2:
    d = json.loads(r2[0]["data"] or "{}")
    shots = d.get("shots", [])
    print("\n=== 分镜 %d个 ===" % len(shots))
    for i, s in enumerate(shots):
        desc = s.get("description","")[:60]
        dlg = s.get("dialogue","")
        chars_in = s.get("char_ages",{})
        print("  shot[%d]: chars=%s" % (i, list(chars_in.keys())))
        print("    desc: %s" % desc)
        if dlg and dlg != "(无台词)":
            print("    dialogue: %s" % dlg[:50])
else:
    print("\n无分镜")
db.close()
