"""智能体开工前置检查 — 每个智能体必须拿到自己的要素才能干活"""

import sqlite3, json, logging

logger = logging.getLogger(__name__)
DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

# ═══ 导演必须产出（下游全部依赖） ═══
DIRECTOR_REQUIRED_OUTPUT = {
    "genre": "题材类型（古装/现代/仙侠/玄幻/武侠/宫廷/商战/职场/甜宠/悬疑/科幻/恐怖/逆袭/重生/穿越/复仇/军旅/民国/乡村/校园/家庭/搞笑）",
    "core_conflict": "核心冲突一句话",
    "director_vision": "导演创作总纲",
    "emotional_curve": "情绪曲线",
    "highlight_moments": "高光时刻",
    "pacing_notes": "节奏要求",
    "character_archetypes": "角色原型分析",
    "tasks": {
        "storyboard_generation": "分镜任务",
        "character_design": "角色设计任务",
        "scene_generation": "场景任务",
        "tts_voice": "配音任务",
        "bgm_music": "配乐任务",
    },
}

# ═══ 每个智能体必须拿到的要素 ═══
# required_from_director: 必须从导演拿到的
# required_from_upstream: 必须从上游阶段拿到的
# required_from_db: 必须从DB拿到的
PREREQUISITES = {
    # ─── 剧本智能体 ───
    "script": {
        "required_from_director": ["genre", "core_conflict"],
        "required_from_upstream": {},  # 第一个阶段，没有上游
        "required_from_db": ["script_text"],
        "description": "需要导演定的题材方向和核心冲突，加上完整原始剧本",
    },

    # ─── 角色智能体 ───
    "character": {
        "required_from_director": ["genre", "character_archetypes", "tasks.character_design"],
        "required_from_upstream": {},
        "required_from_db": ["script_text"],
        "description": "需要导演的角色原型分析和角色设计任务，加上完整剧本",
    },

    # ─── 分镜智能体 ───
    "storyboard": {
        "required_from_director": ["genre", "core_conflict", "emotional_curve", "highlight_moments", "pacing_notes", "tasks.storyboard_generation"],
        "required_from_upstream": {
            "characters": "character",  # 角色列表（含portrait_url）
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的情绪曲线/高光时刻/节奏要求/分镜任务，加上角色列表和完整剧本",
    },

    # ─── 场景智能体 ───
    "scene": {
        "required_from_director": ["genre", "tasks.scene_generation"],
        "required_from_upstream": {
            "shots": "storyboard",  # 分镜列表（含description/location）
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的场景任务和题材，加上分镜列表和完整剧本",
    },

    # ─── 摄影指导 ───
    "cinematographer": {
        "required_from_director": ["genre", "pacing_notes", "emotional_curve"],
        "required_from_upstream": {
            "shots": "storyboard",
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的节奏要求和情绪曲线，加上分镜列表和完整剧本",
    },

    # ─── 服化道 ───
    "wardrobe": {
        "required_from_director": ["genre", "character_archetypes"],
        "required_from_upstream": {
            "shots": "storyboard",
            "characters": "character",
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的角色原型和题材，加上分镜、角色列表和完整剧本",
    },

    # ─── 配音 ───
    "tts": {
        "required_from_director": ["genre", "tasks.tts_voice"],
        "required_from_upstream": {
            "shots": "storyboard",
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的配音任务（角色声线要求），加上分镜（含dialogue）和完整剧本",
    },

    # ─── BGM ───
    "bgm": {
        "required_from_director": ["genre", "emotional_curve", "tasks.bgm_music"],
        "required_from_upstream": {
            "shots": "storyboard",
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的情绪曲线和配乐任务，加上分镜和完整剧本",
    },

    # ─── 字幕 ───
    "subtitle": {
        "required_from_director": ["genre"],
        "required_from_upstream": {
            "shots": "storyboard",
        },
        "required_from_db": ["script_text"],
        "description": "需要题材（决定字幕风格），加上分镜（含dialogue）和完整剧本",
    },

    # ─── 特效 ───
    "sfx": {
        "required_from_director": ["genre"],
        "required_from_upstream": {
            "shots": "storyboard",
        },
        "required_from_db": ["script_text"],
        "description": "需要题材，加上分镜（含action/effects）和完整剧本",
    },

    # ─── 视频生成 ───
    "video": {
        "required_from_director": ["genre", "director_vision"],
        "required_from_upstream": {
            "shots": "storyboard",
            "scene_images": "scene",  # 场景图
        },
        "required_from_db": ["script_text"],
        "description": "需要导演的创作总纲和题材，加上分镜、场景图和完整剧本",
    },

    # ─── 合成 ───
    "composite": {
        "required_from_director": ["genre"],
        "required_from_upstream": {
            "video_clips": "video",
        },
        "required_from_db": ["script_text"],
        "description": "需要题材，加上所有视频片段和完整剧本",
    },
}


def get_director_data(project_id: str) -> dict:
    """获取导演完整产出"""
    if not project_id:
        return {}
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT data FROM pipeline_progress WHERE project_id=? AND stage='director' AND status='completed' ORDER BY id DESC LIMIT 1",
            (str(project_id),)
        ).fetchone()
        db.close()
        if row:
            return json.loads(row["data"] or "{}")
    except Exception as e:
        logger.warning(f"[Prereq] 获取导演数据失败: {e}")
    return {}


def get_genre_from_director(project_id: str) -> str:
    """从导演获取 genre"""
    data = get_director_data(project_id)
    analysis = data.get("analysis", {})
    if isinstance(analysis, dict):
        genre = analysis.get("genre", "")
        if genre:
            return str(genre).strip()
        ga = str(analysis.get("genre_analysis", ""))
        for kw in ["古装","现代","仙侠","玄幻","武侠","宫廷","商战","职场","甜宠","悬疑","科幻","恐怖","逆袭","重生","穿越","复仇","军旅","民国","乡村","校园","家庭","搞笑","都市"]:
            if kw in ga:
                return kw
    return ""


def _get_nested(data: dict, key: str):
    """支持 tasks.character_design 这种嵌套key"""
    if "." in key:
        parts = key.split(".")
        val = data
        for p in parts:
            if isinstance(val, dict):
                val = val.get(p, {})
            else:
                return None
        return val if val else None
    return data.get(key)


def _get_upstream_data(project_id: str, stage_name: str) -> dict:
    """获取某个上游阶段的产出数据"""
    if not project_id or not stage_name:
        return {}
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT data FROM pipeline_progress WHERE project_id=? AND stage=? AND status='completed' ORDER BY id DESC LIMIT 1",
            (str(project_id), stage_name)
        ).fetchone()
        db.close()
        if row:
            return json.loads(row["data"] or "{}")
    except Exception as e:
        logger.warning(f"[Prereq] 获取{stage_name}产出失败: {e}")
    return {}


def check_prerequisites(stage: str, params: dict, project_id: str = "") -> dict:
    """检查智能体是否拿到所有必要要素。
    返回 {"ready": bool, "missing": [缺的], "filled_params": 补全后的params}
    缺了就尝试从导演/上游/DB补全，补不了就标记缺失。"""
    prereq = PREREQUISITES.get(stage)
    if not prereq:
        # 没定义前置条件的阶段直接放行
        return {"ready": True, "missing": [], "filled_params": params}

    missing = []
    filled = dict(params)

    # ═══ 1. 从导演获取 ═══
    director_data = get_director_data(project_id) if project_id else {}
    analysis = director_data.get("analysis", {})
    tasks = director_data.get("tasks", {})
    refined = director_data.get("refined_script", {})

    for key in prereq.get("required_from_director", []):
        if key == "genre":
            if not filled.get("genre"):
                g = get_genre_from_director(project_id)
                if g:
                    filled["genre"] = g
                    logger.info(f"[Prereq] {stage}: genre从导演补全={g}")
                else:
                    missing.append("genre(导演未定题材)")
            continue

        if key.startswith("tasks."):
            task_key = key.split(".", 1)[1]
            val = tasks.get(task_key, "") if isinstance(tasks, dict) else ""
            if val:
                if not isinstance(filled.get("director_tasks"), dict):
                    filled["director_tasks"] = {}
                filled["director_tasks"][task_key] = val
                logger.info(f"[Prereq] {stage}: director_tasks.{task_key}从导演补全")
            else:
                missing.append(f"导演任务:{task_key}")
            continue

        # analysis 字段
        val = analysis.get(key, "") if isinstance(analysis, dict) else ""
        if val:
            if not isinstance(filled.get("director_analysis"), dict):
                filled["director_analysis"] = {}
            filled["director_analysis"][key] = val
        else:
            # 检查 params 里有没有
            existing = filled.get(key) or (_get_nested(filled.get("director_analysis", {}), key) if filled.get("director_analysis") else "")
            if not existing:
                missing.append(f"导演:{key}")

    # ═══ 2. 从上游获取 ═══
    for key, upstream_stage in prereq.get("required_from_upstream", {}).items():
        if filled.get(key):
            continue  # params 已有
        upstream_data = _get_upstream_data(project_id, upstream_stage)
        # 从上游数据提取对应字段
        field_map = {
            "characters": lambda d: d.get("characters", []),
            "shots": lambda d: d.get("shots", d.get("data", {}).get("shots", [])),
            "scene_images": lambda d: d.get("images", d.get("scene_images", {})),
            "video_clips": lambda d: d.get("clips", d.get("videos", [])),
        }
        extractor = field_map.get(key)
        if extractor:
            val = extractor(upstream_data)
            if val:
                filled[key] = val
                logger.info(f"[Prereq] {stage}: {key}从{upstream_stage}补全")
            else:
                missing.append(f"{key}(需先完成{upstream_stage}阶段)")

    # ═══ 3. 从DB获取 ═══
    for key in prereq.get("required_from_db", []):
        if filled.get(key):
            continue
        if key == "script_text":
            try:
                db = sqlite3.connect(DB_PATH)
                db.row_factory = sqlite3.Row
                r = db.execute("SELECT script FROM projects WHERE id=?", (str(project_id),)).fetchone()
                db.close()
                if r and r["script"]:
                    script = r["script"]
                    # 截断到12000字（超长时保留前10000+后2000）
                    MAX_SCRIPT = 12000
                    if len(script) > MAX_SCRIPT:
                        script = script[:10000] + "\n\n...（中间省略）...\n\n" + script[-2000:]
                        logger.info(f"[Prereq] {stage}: 剧本超长，截断至{MAX_SCRIPT}字")
                    filled["script_text"] = script
                    logger.info(f"[Prereq] {stage}: script_text从DB补全 ({len(script)}字)")
                else:
                    missing.append("script_text(剧本)")
            except Exception:
                missing.append("script_text(剧本)")

    return {
        "ready": len(missing) == 0,
        "missing": missing,
        "filled_params": filled,
    }
