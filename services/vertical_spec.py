"""
竖屏规格 & 构图提示词（统一导入 model_spec.py）
保留此文件仅为了向后兼容旧代码，所有新代码应直接从 model_spec 导入。
"""
from services.model_spec import (
    SIZE, FRAMING_PROMPT_CN, FRAMING_PROMPT_EN,
    NEGATIVE_DEFAULT, framing_prompt,
)

# ── 向后兼容：保持 VERT 类可用 ──
class VerticalSpec:
    CHARACTER_PORTRAIT: str = SIZE.character_portrait
    SCENE_BACKGROUND: str = SIZE.scene_background
    COVER_THUMBNAIL: str = SIZE.cover_thumbnail
    FALLBACK_SQUARE: str = SIZE.fallback_square
    FALLBACK_WANXIANG: str = SIZE.fallback_wanxiang
    FRAMING_PROMPT: str = FRAMING_PROMPT_CN
    FRAMING_PROMPT_EN: str = FRAMING_PROMPT_EN
    VIDEO_RESOLUTION: str = "720P"

VERT = VerticalSpec()

SIZE_MAP = {
    "character": VERT.CHARACTER_PORTRAIT,
    "scene": VERT.SCENE_BACKGROUND,
    "cover": VERT.COVER_THUMBNAIL,
    "fallback": VERT.FALLBACK_SQUARE,
}
