"""Image standardization: 3:4 portrait crop (600x800)"""
import io, logging
from PIL import Image

logger = logging.getLogger("utils.image_processor")

def standardize_image(data: bytes) -> bytes:
    if len(data) < 200000: return data  # skip if <200KB, already small
    """Crop to 3:4 portrait (600x800), JPEG output"""
    try:
        img = Image.open(io.BytesIO(data))
        w, h = img.size
        target = min(w, int(h * 3/4))
        if target < w:
            left = (w - target) // 2
            img = img.crop((left, 0, left + target, h))
        target_h = min(h, int(w * 4/3))
        if target_h < h:
            top = (h - target_h) // 2
            img = img.crop((0, top, w, top + target_h))
        img = img.resize((600, 800), Image.BILINEAR)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=85)
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"standardize_image failed: {e}")
        return data
