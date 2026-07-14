#!/usr/bin/env python3
"""修复 10011152 的 DB 进度数据：把 scene/sfx/storyboard 写回 completed，
让 /pipeline/step video 阶段能正确加载场景图。纯 DB 操作，不 import agent。"""
import sqlite3, json, time

PROJECT_ID = "10011152"
PID = f"pipe_1782890903_{PROJECT_ID}"
BASE_URL = "https://ai.mzsh.top"
DB = "/www/wwwroot/api.mzsh.top/data/short_drama.db"
DB_APP = "/www/wwwroot/api.mzsh.top/data/app.db"

db = sqlite3.connect(DB)
db.row_factory = sqlite3.Row

# 1. 加载分镜
rows = db.execute("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' ORDER BY id DESC LIMIT 1", (PROJECT_ID,)).fetchall()
if not rows:
    print("ERROR: 无分镜"); exit(1)
shots = json.loads(rows[0]["data"] or "{}").get("shots", [])
print(f"分镜: {len(shots)}")

# 2. 加载角色锁脸图
rows = db.execute("SELECT characters FROM projects WHERE id=?", (PROJECT_ID,)).fetchall()
chars = json.loads(rows[0]["characters"] or "[]")
char_map = {}
for ch in chars:
    name = ch.get("name", "")
    pu = ch.get("portrait_url", "") or ch.get("figure_url", "")
    if name and pu:
        char_map[name] = pu
print(f"角色: {len(char_map)} -> {list(char_map.keys())}")

# 3. 加载场景图
db2 = sqlite3.connect(DB_APP)
db2.row_factory = sqlite3.Row
srows = db2.execute("SELECT file_path,tags FROM media_library WHERE file_path LIKE '%/scenes/%1152%' ORDER BY id DESC").fetchall()
scene_by_desc = {}
for r in srows:
    try:
        tags = json.loads(r["tags"]) if r["tags"] else []
    except Exception:
        tags = []
    dk = tags[0] if tags else ""
    if dk and dk not in scene_by_desc:
        fp = r["file_path"]
        url = fp if fp.startswith("http") else BASE_URL + fp.replace("/www/wwwroot", "")
        scene_by_desc[dk] = url
print(f"场景图(去重): {len(scene_by_desc)}")

# 4. 匹配每个 shot 的场景图
# 手动映射：分镜被改过描述，和场景图 prompt 对不上，按内容关键词映射
MANUAL_MAP = {
    3: "林宸抬手按住马侧的沥泉枪",        # 冲锋预备
    7: "敌方骑兵同时冲出，两股铁甲洪流",   # 大军对冲
    10: "林宸和萧烈的战马擦身而过",        # 关键击杀
    11: "林宸策马立在高坡上，举着还在滴血",  # 胜利
    13: "镜头从林宸的身影慢慢拉升到高空",   # 收尾-全景
    14: "林宸站在高坡上望着脚下的战场",     # 收尾-特写
}

def find_by_keyword(scene_by_desc, keyword):
    """用关键词在场景图描述里找"""
    for k, v in scene_by_desc.items():
        if keyword in k:
            return v
    return ""

image_map = {}
for i, s in enumerate(shots):
    desc = s.get("description", "")
    # 精确匹配
    url = scene_by_desc.get(desc, "")
    # 前20字模糊匹配
    if not url:
        ds = desc[:20]
        for k, v in scene_by_desc.items():
            if ds and (ds in k or k[:20] in desc):
                url = v
                break
    # 手动关键词匹配
    if not url and i in MANUAL_MAP:
        url = find_by_keyword(scene_by_desc, MANUAL_MAP[i])
    if url:
        image_map[str(i)] = url
        shots[i]["image_url"] = url
        shots[i]["scene_image"] = url
print(f"匹配场景图: {len(image_map)}/{len(shots)}")

# 5. 写回 scene/storyboard/sfx
now = time.strftime("%Y-%m-%dT%H:%M:%S")
scene_data = json.dumps({"image_map": image_map, "images": image_map, "shots": shots, "pipeline_id": PID}, ensure_ascii=False)
db.execute(
    "INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,error,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) "
    "DO UPDATE SET status='completed', data=excluded.data, finished_at=?",
    (PROJECT_ID, PID, "scene", "completed", scene_data, "", now, now, now))

sb_data = json.dumps({"shots": shots, "pipeline_id": PID}, ensure_ascii=False)
db.execute(
    "INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,error,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) "
    "DO UPDATE SET data=excluded.data",
    (PROJECT_ID, PID, "storyboard", "completed", sb_data, "", now, now))

db.execute(
    "INSERT INTO pipeline_progress (project_id,pipeline_id,stage,status,data,error,started_at,finished_at) "
    "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id,pipeline_id,stage) "
    "DO UPDATE SET status='completed', data=excluded.data",
    (PROJECT_ID, PID, "sfx", "completed", json.dumps({"success": True}), "", now, now))

db.commit()
db.close()
db2.close()
print("DB 修复完成: scene+sfx+storyboard 已写回")
for i in range(len(shots)):
    print(f"  shot[{i}]: scene={'Y' if str(i) in image_map else 'N'}")
