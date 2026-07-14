#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
r = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011155' AND stage='video' ORDER BY id DESC LIMIT 1").fetchall()
if r:
    d = json.loads(r[0]["data"] or "{}")
    vids = d.get("videos", [])
    print("DB videos: %d" % len(vids))
    for v in vids:
        si = v.get("shot_index", "?")
        rd = v.get("result", {}) if isinstance(v.get("result"), dict) else {}
        url = rd.get("video_url", "") or v.get("video_url", "")
        print("  shot[%s]: %s" % (si, url[:60] if url else "(无)"))
else:
    print("无video记录")
db.close()
