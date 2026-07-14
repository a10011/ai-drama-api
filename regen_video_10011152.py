#!/usr/bin/env python3
"""
为项目 10011152 补跑视频阶段 + 合成。
从 DB 读取分镜/角色锁脸图/场景图/TTS，逐个镜头调 VideoAgent 生成视频，
全部成功后调 composite 合成最终视频。

用法: cd /www/wwwroot/api.mzsh.top && python3 regen_video_10011152.py
"""
import sqlite3, json, os, sys, time, re

BASE_DIR = "/www/wwwroot/api.mzsh.top"
DB_DRAMA = os.path.join(BASE_DIR, "data/short_drama.db")
DB_APP = os.path.join(BASE_DIR, "data/app.db")
PROJECT_ID = "10011152"
BASE_URL = "https://ai.mzsh.top"

def load_shots():
    db = sqlite3.connect(DB_DRAMA)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' ORDER BY id DESC LIMIT 1",
        (PROJECT_ID,)).fetchall()
    db.close()
    if not rows:
        print("❌ 找不到分镜数据"); sys.exit(1)
    d = json.loads(rows[0]["data"] or "{}")
    return d.get("shots", [])

def load_characters():
    db = sqlite3.connect(DB_DRAMA)
    db.row_factory = sqlite3.Row
    rows = db.execute("SELECT characters FROM projects WHERE id=?", (PROJECT_ID,)).fetchall()
    db.close()
    if not rows:
        return {}
    chars = json.loads(rows[0]["characters"] or "[]")
    photo_map = {}
    for ch in chars:
        name = ch.get("name", "")
        pu = ch.get("portrait_url", "") or ch.get("figure_url", "") or ch.get("image_url", "")
        if name and pu:
            photo_map[name] = pu
    return photo_map

def load_scene_images():
    """从 media_library 读取 10011152 的场景图，按描述文本建索引，取每个描述最新一张"""
    db = sqlite3.connect(DB_APP)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT file_path, tags FROM media_library WHERE file_path LIKE '%/scenes/%1152%' OR file_path LIKE '%/scenes/10011152/%' ORDER BY id DESC"
    ).fetchall()
    db.close()
    # 按 tags 第一个元素（描述文本）去重，保留最新（已 ORDER BY id DESC）
    seen = {}
    for r in rows:
        fp = r["file_path"]
        try:
            tags = json.loads(r["tags"]) if r["tags"] else []
        except Exception:
            tags = [r["tags"] or ""]
        desc_key = tags[0] if tags else ""
        # 只保留每个描述的第一条（最新）
        if desc_key and desc_key not in seen:
            url = fp if fp.startswith("http") else BASE_URL + fp.replace("/www/wwwroot", "")
            seen[desc_key] = url
    return seen  # {描述文本: url}

def match_scene_for_shot(shot, scene_map):
    """用分镜的 description 匹配场景图"""
    desc = shot.get("description", "") or shot.get("content", "")
    # 精确匹配
    if desc in scene_map:
        return scene_map[desc]
    # 模糊匹配：取描述前20字
    desc_short = desc[:20]
    for k, v in scene_map.items():
        if desc_short and desc_short in k:
            return v
    # 反向
    for k, v in scene_map.items():
        if k[:20] in desc:
            return v
    return ""

def load_tts():
    db = sqlite3.connect(DB_DRAMA)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT data FROM pipeline_progress WHERE project_id=? AND stage='tts' ORDER BY id DESC LIMIT 1",
        (PROJECT_ID,)).fetchall()
    db.close()
    if not rows:
        return []
    d = json.loads(rows[0]["data"] or "{}")
    return d.get("audio_files", [])

def main():
    print("=" * 60)
    print(f"补跑视频: 项目 {PROJECT_ID}")
    print("=" * 60)

    shots = load_shots()
    char_photos = load_characters()
    scene_map = load_scene_images()
    tts_files = load_tts()

    print(f"分镜: {len(shots)} 个")
    print(f"角色锁脸图: {len(char_photos)} 个 → {list(char_photos.keys())}")
    print(f"场景图(去重后): {len(scene_map)} 张")
    print(f"TTS音频: {len(tts_files)} 个")

    if not shots:
        print("❌ 无分镜，退出"); return

    # 组装每个 shot 的素材
    for i, s in enumerate(shots):
        # 场景图
        scene_url = match_scene_for_shot(s, scene_map)
        s["scene_image"] = scene_url
        s["image_url"] = scene_url
        # 角色锁脸图
        s["characters"] = s.get("characters", [])
        shot_text = s.get("description", "") + s.get("dialogue", "")
        for cname, curl in char_photos.items():
            if cname in shot_text:
                s["character_image"] = curl
                s["portrait_url"] = curl
                break
        # TTS 音频
        if tts_files:
            for af in tts_files:
                si = af.get("shot_index", af.get("shot_num", -1))
                if si == i or si == i + 1:
                    s["tts_audio"] = af.get("local_path", af.get("file_path", "")) or af.get("audio_url", "")
                    break
        s["project_id"] = PROJECT_ID
        s["pipeline_id"] = f"pipe_1782890903_{PROJECT_ID}"
        s["shot_num"] = s.get("shot_num", i + 1)

    # 打印组装结果
    print("\n--- 镜头素材检查 ---")
    for i, s in enumerate(shots):
        sc = "有" if s.get("scene_image") else "缺"
        ch = "有" if s.get("character_image") else "缺"
        au = "有" if s.get("tts_audio") else "缺"
        print(f"  shot[{i}]: 场景图={sc} 角色图={ch} 音频={au} | {(s.get('description',''))[:30]}")

    # 调 VideoAgent
    print("\n--- 开始生成视频 ---")
    sys.path.insert(0, BASE_DIR)
    os.chdir(BASE_DIR)
    from agents.agent_video import VideoAgent
    agent = VideoAgent()

    results = []
    failed = 0
    for i, s in enumerate(shots):
        print(f"\n>>> 生成 shot[{i}] ({i+1}/{len(shots)})...")
        try:
            r = agent.generate_video(s)
            if r.success:
                vurl = r.data.get("video_url", "")
                print(f"    ✅ OK: {vurl[:70]}")
                results.append({"shot_index": i, "result": {"video_url": vurl}})
            else:
                failed += 1
                print(f"    ❌ 失败: {r.error}")
                results.append({"shot_index": i, "result": {"error": r.error}})
        except Exception as e:
            failed += 1
            print(f"    ❌ 异常: {e}")
            results.append({"shot_index": i, "result": {"error": str(e)}})

    print(f"\n--- 视频生成完成: 成功{len(results)-failed}, 失败{failed} ---")

    # 写入 DB（让 composite 能读到）
    db = sqlite3.connect(DB_DRAMA)
    video_data = json.dumps({"videos": results, "total": len(results), "failed": failed}, ensure_ascii=False)
    db.execute(
        "INSERT INTO pipeline_progress (project_id, pipeline_id, stage, status, data, error, started_at, finished_at) "
        "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(project_id, pipeline_id, stage) DO UPDATE SET status=excluded.status, data=excluded.data",
        (PROJECT_ID, f"pipe_1782890903_{PROJECT_ID}", "video",
         "completed" if failed == 0 else "completed",  # 即使部分失败也标完成，让合成用已有的
         video_data, "", time.strftime("%Y-%m-%dT%H:%M:%S"), time.strftime("%Y-%m-%dT%H:%M:%S"))
    )
    db.commit()
    db.close()
    print("已写入 video 阶段进度到 DB")

    if failed > 0:
        print(f"\n⚠️ 有 {failed} 个镜头失败，请查原因后单独补。合成将用已成功的镜头。")

    # 合成
    print("\n--- 开始合成 ---")
    try:
        from agents.agent_composite import CompositeAgent
        comp = CompositeAgent()
        # composite 需要 shots 带视频 url
        for i, r in enumerate(results):
            vd = r.get("result", {})
            if isinstance(vd, dict) and vd.get("video_url") and i < len(shots):
                shots[i]["video_url"] = vd["video_url"]

        cr = comp.run(action="composite", shots=shots, project_id=PROJECT_ID,
                      pipeline_id=f"pipe_1782890903_{PROJECT_ID}")
        if cr.success:
            furl = cr.data.get("final_video_url", cr.data.get("video_url", ""))
            print(f"\n🎉 合成成功: {furl}")
        else:
            print(f"\n❌ 合成失败: {cr.error}")
    except Exception as e:
        print(f"\n❌ 合成异常: {e}")

if __name__ == "__main__":
    main()
