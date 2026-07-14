"""Genre 获取器 — 拿不到就找导演要，绝不自己猜"""
import sqlite3, json, logging

logger = logging.getLogger(__name__)
DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

def get_genre_from_director(project_id: str) -> str:
    """从导演分析结果获取 genre。拿不到返回空字符串。"""
    if not project_id:
        return ""
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT data FROM pipeline_progress WHERE project_id=? AND stage='director' AND status='completed' ORDER BY id DESC LIMIT 1",
            (str(project_id),)
        ).fetchone()
        db.close()
        if row:
            data = json.loads(row["data"] or "{}")
            analysis = data.get("analysis", {})
            if isinstance(analysis, dict):
                genre = analysis.get("genre", "")
                if genre:
                    return str(genre).strip()
            # 导演没明确 genre，从 genre_analysis 推断
            ga = str(analysis.get("genre_analysis", "")) if isinstance(analysis, dict) else ""
            if ga:
                for kw in ["古装","现代","仙侠","玄幻","武侠","宫廷","商战","职场","甜宠","悬疑","科幻","恐怖","逆袭","重生","穿越","复仇","军旅","民国","乡村","校园","家庭","搞笑","都市"]:
                    if kw in ga:
                        return kw
        return ""
    except Exception as e:
        logger.warning(f"[Genre] 从导演获取失败 project={project_id}: {e}")
        return ""
