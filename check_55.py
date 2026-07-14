#!/usr/bin/env python3
import sqlite3, json
db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
rows = db.execute("SELECT stage,status,data FROM pipeline_progress WHERE project_id='10011155' ORDER BY id").fetchall()
seen = {}
for r in rows:
    seen[r["stage"]] = r["status"]
print("=== 10011155 各阶段 ===")
for s in ["director","script","character","storyboard","cinematographer","wardrobe","scene","sfx","tts","subtitle","bgm","video","composite"]:
    st = seen.get(s, "无")
    mark = "OK" if st=="completed" else ("XX" if st in ("failed","failed_permanent") else "..")
    print("  %s %s: %s" % (mark, s, st))

# TTS
r = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011155' AND stage='tts' ORDER BY id DESC LIMIT 1").fetchall()
if r:
    d = json.loads(r[0]["data"] or "{}")
    afs = d.get("audio_files", [])
    print("\nTTS音频: %d个" % len(afs))
    for af in afs[:3]:
        url = af.get("audio_url","") or af.get("file_path","")
        print("  shot[%s]: %s" % (af.get("shot_index","?"), url[:50]))

# BGM
r2 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011155' AND stage='bgm' ORDER BY id DESC LIMIT 1").fetchall()
if r2:
    d2 = json.loads(r2[0]["data"] or "{}")
    bgm = d2.get("audio_file", d2.get("bgm_url", d2.get("url","")))
    print("\nBGM: %s" % str(bgm)[:60])

# video 数量
r3 = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011155' AND stage='video' ORDER BY id DESC LIMIT 1").fetchall()
if r3:
    d3 = json.loads(r3[0]["data"] or "{}")
    vids = d3.get("videos", [])
    ok = sum(1 for v in vids if isinstance(v.get("result",{}),dict) and v["result"].get("video_url"))
    print("\n视频: %d/%d 成功" % (ok, len(vids)))

db.close()
