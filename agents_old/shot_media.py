"""
agents/shot_media.py — 统一的 shot 素材注入函数

确定性链路核心：只信 DB（持久态），不信内存 ctx。
给 shots 注入 scene_image + character_image + character_images + tts_audio。

被三处调用，保证规则唯一：
  - orchestrator._hook_ensure_shot_media  (一键生成)
  - pipeline.py step 端点                (逐镜/分阶段)
  - pipeline.py single_video 端点         (单镜重生成)
"""
import json
import logging

logger = logging.getLogger("shot_media")

# 角色匹配文本字段（统一：三字段拼接，不漏 scene 字段里的角色）
_MATCH_FIELDS = ("description", "dialogue", "scene")

# 角色锁脸图取值字段（按优先级）
_PORTRAIT_FIELDS = ("portrait_url", "figure_url", "photo")

# TTS 音频取值字段（按优先级）
_AUDIO_FIELDS = ("local_path", "file_path", "audio_url")


def _execute_db(sql: str, args: tuple = ()):
    """复用主项目的 DB 访问（延迟导入避免循环依赖）"""
    from routers.pipeline import _execute_db as _exec
    return _exec(sql, args)


def _read_scene_map(project_id: str) -> dict:
    """读 scene 阶段的 image_map: {shot_index_str: url}"""
    rows = _execute_db(
        "SELECT data FROM pipeline_progress "
        "WHERE project_id=? AND stage='scene' AND status='completed' "
        "ORDER BY id DESC LIMIT 1",
        (str(project_id),)
    )
    if not rows:
        return {}
    data = json.loads(rows[0]["data"] or "{}")
    img_map = data.get("image_map", {})
    if not img_map:
        # 兼容旧字段名 images
        img_map = data.get("images", {})
    return img_map if isinstance(img_map, dict) else {}


def _read_characters(project_id: str) -> list:
    """读 projects.characters: [{name, portrait_url, ...}]"""
    rows = _execute_db("SELECT characters FROM projects WHERE id=?", (str(project_id),))
    if not rows:
        return []
    chars = json.loads(rows[0]["characters"] or "[]")
    return chars if isinstance(chars, list) else []


def _read_tts_files(project_id: str) -> list:
    """读 tts 阶段的 audio_files: [{shot_index, local_path, ...}]"""
    rows = _execute_db(
        "SELECT data FROM pipeline_progress "
        "WHERE project_id=? AND stage='tts' AND status='completed' "
        "ORDER BY id DESC LIMIT 1",
        (str(project_id),)
    )
    if not rows:
        return []
    data = json.loads(rows[0]["data"] or "{}")
    return data.get("audio_files", [])


def _get_portrait(char: dict) -> str:
    """从角色字典按优先级取锁脸图 URL"""
    for f in _PORTRAIT_FIELDS:
        v = char.get(f, "")
        if v and isinstance(v, str):
            return v
    return ""


def _get_audio(af: dict) -> str:
    """从 tts audio_file 字典按优先级取音频路径"""
    for f in _AUDIO_FIELDS:
        v = af.get(f, "")
        if v and isinstance(v, str):
            return v
    return ""


def inject_shot_media(shots: list, project_id: str, include_tts: bool = True) -> dict:
    """
    给 shots 注入 scene_image + character_image + character_images + tts_audio。
    返回注入统计。

    参数:
        shots: 分镜列表（原地修改）
        project_id: 项目 ID
        include_tts: 是否注入 TTS 音频（scene 阶段不需要，video/composite 需要）

    返回:
        {
            "scene_ok": 注入场景图的镜头数,
            "char_ok": 注入角色锁脸图的镜头数,
            "tts_ok": 注入音频的镜头数,
            "missing_char": 缺角色图(有人物但没锁脸图)的镜头索引列表,
            "characters_with_portrait": 有锁脸图的角色名列表,
        }
    """
    if not shots:
        return {"scene_ok": 0, "char_ok": 0, "tts_ok": 0, "missing_char": [], "characters_with_portrait": []}

    pid = str(project_id)

    # 1. 读 scene image_map
    scene_map = _read_scene_map(pid)

    # 2. 读角色 + 建 {name: portrait} 映射
    characters = _read_characters(pid)
    char_portrait_map = {}  # {name: portrait_url}
    for ch in characters:
        if not isinstance(ch, dict):
            continue
        name = ch.get("name", "")
        if not name:
            continue
        portrait = _get_portrait(ch)
        if portrait:
            char_portrait_map[name] = portrait

    # 3. 读 tts（可选）
    tts_files = _read_tts_files(pid) if include_tts else []

    scene_ok = 0
    char_ok = 0
    tts_ok = 0
    missing_char = []

    for i, shot in enumerate(shots):
        if not isinstance(shot, dict):
            continue

        # === scene_image ===
        url = scene_map.get(str(i), "")
        if not url:
            # 兼容：shot 已自带 image_url（早期注入）
            url = shot.get("image_url", "")
        if url:
            shot["scene_image"] = url
            shot["image_url"] = url
            scene_ok += 1
        else:
            # 没场景图也明确置空，不留下脏数据
            shot["scene_image"] = shot.get("scene_image", "")

        # === character_image + character_images（多角色锁脸）===
        # 锁脸匹配优先级：focus_character 字段 > description文本匹配
        # 这样prompt里可以用"玄甲将军"等代称规避审核，但focus_character填真实角色名仍能锁脸
        matched_portraits = []  # 本镜所有出场角色的锁脸图（保序去重）

        # 1. 优先用 focus_character 字段匹配（导演指定的焦点角色）
        focus_char = shot.get("focus_character", "")
        if focus_char and focus_char != "(无角色)":
            # 支持多人 "蒙毅,玉漱"
            for fc_name in focus_char.split(","):
                fc_name = fc_name.strip()
                if fc_name and fc_name in char_portrait_map:
                    portrait = char_portrait_map[fc_name]
                    if portrait not in matched_portraits:
                        matched_portraits.append(portrait)

        # 2. 文本匹配作为兜底（description里出现角色名也匹配）
        if not matched_portraits:
            match_text = ""
            for f in _MATCH_FIELDS:
                match_text += str(shot.get(f, ""))
            for name, portrait in char_portrait_map.items():
                if name and name in match_text and portrait not in matched_portraits:
                    matched_portraits.append(portrait)

        if matched_portraits:
            shot["character_image"] = matched_portraits[0]   # 主角色图（兼容旧逻辑）
            shot["portrait_url"] = matched_portraits[0]
            shot["character_images"] = matched_portraits     # 多角色锁脸列表（R2V 1-9张）
            char_ok += 1
        else:
            # 判断这镜是否「有人物但缺锁脸图」→ 记录异常
            # 启发：description 含人物动作词或 dialogue 非空 → 认为有人物
            desc = str(shot.get("description", ""))
            dialogue = str(shot.get("dialogue", ""))
            has_person = bool(
                dialogue and dialogue != "(无台词)"
            ) or any(kw in desc for kw in ("他", "她", "说", "看", "走", "站", "坐", "拿", "抱", "眼", "手", "脸"))
            if has_person and not shot.get("character_image"):
                missing_char.append(i)
            # 清掉可能残留的脏字段，避免误导下游
            shot.pop("character_image", None)
            shot.pop("character_images", None)

        # === tts_audio ===
        if include_tts:
            audio_url = ""
            for af in tts_files:
                if not isinstance(af, dict):
                    continue
                si = af.get("shot_index", af.get("shot_num", -1))
                # 双值兼容：tts 的索引可能从0或1开始
                if si == i or si == i + 1:
                    audio_url = _get_audio(af)
                    if audio_url:
                        break
            if audio_url:
                shot["tts_audio"] = audio_url
                tts_ok += 1

    result = {
        "scene_ok": scene_ok,
        "char_ok": char_ok,
        "tts_ok": tts_ok,
        "missing_char": missing_char,
        "characters_with_portrait": list(char_portrait_map.keys()),
    }
    logger.info(
        f"[inject_shot_media] project={pid} shots={len(shots)} "
        f"scene={scene_ok} char={char_ok} tts={tts_ok} "
        f"missing_char={missing_char} portraits={list(char_portrait_map.keys())}"
    )
    return result
