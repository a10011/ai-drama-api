#!/usr/bin/env python3
"""分析分镜质量：特写占比、描述长度、director_shot有没有、sound_design有没有"""
import sqlite3, json

db = sqlite3.connect("/www/wwwroot/api.mzsh.top/data/short_drama.db")
db.row_factory = sqlite3.Row
r = db.execute("SELECT data FROM pipeline_progress WHERE project_id='10011154' AND stage='storyboard' ORDER BY id DESC LIMIT 1").fetchall()
if not r:
    print("无分镜"); exit()
d = json.loads(r[0]["data"] or "{}")
shots = d.get("shots", [])
print("=== 分镜分析（%d个镜头）===\n" % len(shots))

type_count = {}
total_desc_len = 0
has_director_shot = 0
has_sound = 0
has_dialogue = 0
has_emotion = 0

for i, s in enumerate(shots):
    shot_type = s.get("shot_type", "中景")
    desc = s.get("description", "")
    dialogue = s.get("dialogue", "")
    emotion = s.get("emotion", "")
    director_shot = s.get("director_shot", "")
    sound = s.get("sound_design", "")
    duration = s.get("duration_sec", 0)

    type_count[shot_type] = type_count.get(shot_type, 0) + 1
    total_desc_len += len(desc)
    if director_shot:
        has_director_shot += 1
    if sound and len(sound) > 5:
        has_sound += 1
    if dialogue and dialogue != "(无台词)":
        has_dialogue += 1
    if emotion and emotion != "中性":
        has_emotion += 1

    print("--- shot[%d] ---" % i)
    print("  景别: %s | 运镜: %s | 时长: %ss | 情绪: %s" % (shot_type, s.get("camera_movement",""), duration, emotion))
    print("  描述(%d字): %s" % (len(desc), desc[:80]))
    print("  台词: %s" % (dialogue[:40] if dialogue and dialogue != "(无台词)" else "(无)"))
    print("  director_shot: %s" % ("有(%d字)" % len(director_shot) if director_shot else "❌无"))
    print("  sound_design: %s" % ("有(%d字)" % len(sound) if sound and len(sound) > 5 else "❌无"))

print("\n=== 统计 ===")
print("景别分布: %s" % type_count)
closeup = sum(v for k, v in type_count.items() if "特写" in k)
print("特写占比: %d/%d (%.0f%%)" % (closeup, len(shots), closeup/len(shots)*100 if shots else 0))
print("平均描述长度: %d字" % (total_desc_len // len(shots) if shots else 0))
print("有director_shot: %d/%d" % (has_director_shot, len(shots)))
print("有sound_design: %d/%d" % (has_sound, len(shots)))
print("有台词: %d/%d" % (has_dialogue, len(shots)))
print("有emotion: %d/%d" % (has_emotion, len(shots)))
db.close()
