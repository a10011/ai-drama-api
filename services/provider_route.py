"""Provider 路由：根据用户 API Key 类型自动判断走火山还是阿里路线
火山方舟(Seedance)：视频自带配音+口型+BGM → 跳过 TTS/BGM/字幕
阿里百炼(HappyHorse)：视频无声 → 需要 TTS/BGM/口型同步全流程
"""
import sqlite3, logging

logger = logging.getLogger(__name__)
DB_PATH = "/www/wwwroot/api.mzsh.top/data/short_drama.db"

# 火山路线跳过的阶段（视频自带）
VOLC_SKIP_STAGES = {"tts", "bgm", "subtitle"}

def detect_provider(user_id: str) -> str:
    """检测用户的 provider：volc(火山) / ali(阿里) / default(系统默认)"""
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT ark_key, ali_key FROM user_api_keys WHERE user_id=?", (str(user_id),)).fetchone()
        db.close()
        if row:
            ark = (row["ark_key"] or "").strip()
            ali = (row["ali_key"] or "").strip()
            if ark and not ali:
                return "volc"
            if ali and not ark:
                return "ali"
            if ark and ali:
                return "volc"  # 都有，优先火山（自带音频更快）
    except Exception as e:
        logger.warning(f"[Provider] 检测失败 user={user_id}: {e}")
    return "volc"  # 系统默认走火山

def get_skip_stages(user_id: str) -> set:
    """返回应该跳过的阶段集合"""
    provider = detect_provider(user_id)
    if provider == "volc":
        logger.info(f"[Provider] user={user_id} → 火山路线，跳过 TTS/BGM/字幕")
        return VOLC_SKIP_STAGES
    logger.info(f"[Provider] user={user_id} → 阿里路线，全流程(含TTS/BGM)")
    return set()

def should_skip_stage(user_id: str, stage: str) -> bool:
    """判断某个阶段是否应该跳过"""
    return stage in get_skip_stages(user_id)
