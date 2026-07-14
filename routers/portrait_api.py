from utils.image_processor import standardize_image
"""
换脸写真 API: 上传照片 → AI保留脸型 → 优化美化
"""
import os, time, json, hashlib, requests
from fastapi import APIRouter, UploadFile, File, Form
from fastapi.responses import JSONResponse
from utils.path_util import local_path_to_url

import logging
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/portrait", tags=["portrait"])

UPLOAD_DIR = "/www/wwwroot/storage/portraits"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _get_ark_key():
    cfg = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config", "api_keys.json")
    with open(cfg) as f:
        keys = json.load(f)
    return keys.get("ark_volc", {}).get("key", "")


@router.post("/enhance")
async def portrait_enhance(
    photo: UploadFile = File(None),
    style: str = Form(""),
    gender: str = Form("女"),
    character_name: str = Form(""),
    character_desc: str = Form(""),
    costume: str = Form(""),
    genre: str = Form("")
):
    """
    上传照片 → 保留轮廓 + 瘦脸美颜 + AI优化 → 角色肖像
    - 有照片: ARK Seedream 参考图生图（保留脸型，美化）
    - 无照片: HiDream 文生图（从描述生成）
    """
    gender_cn = "女" if "女" in gender else "男"
    beauty_prompts = "瘦脸，精致妆容，皮肤美白，五官优化，自然不假，高清写真"
    
    # 角色描述 → prompt
    if character_desc:
        genre = genre or style
        parts = [f"{genre}剧{gender_cn}主角", character_name or "", character_desc]
        if costume: parts.append(f"身穿{costume}")
        parts.append(beauty_prompts)
        prompt = "，".join(p for p in parts if p)
    else:
        prompt = f"一位{gender_cn}性，{style}风格，{beauty_prompts}"
    
    # 处理上传照片
    pub_url = ""
    if photo:
        # 标准化图片：3:4肖像比例，600x800，留脸
        raw_data = await photo.read()
        if len(raw_data) > 10 * 1024 * 1024:
            return {"success": False, "error": "照片太大，不超过10MB"}
        try:
            content = standardize_image(raw_data)
            fname = f"upload_{int(time.time())}_{hashlib.md5(photo.filename.encode()).hexdigest()[:6]}.jpg"
        except Exception:
            # 标准化失败就用原图
            content = raw_data
            ext = os.path.splitext(photo.filename)[1] or ".jpg"
            fname = f"upload_{int(time.time())}_{hashlib.md5(photo.filename.encode()).hexdigest()[:6]}{ext}"
        fpath = os.path.join(UPLOAD_DIR, fname)
        with open(fpath, 'wb') as f:
            f.write(content)
        pub_url = local_path_to_url(fpath)
    
    saved_urls = []
    
    # 有照片 → ARK Seedream 参考图生图（保留轮廓）
    if pub_url:
        api_key = _get_ark_key()
        payload = {
            "model": "doubao-seedream-4-0-250828",
            "prompt": prompt,
            "image": pub_url,
            "n": 2,
            "size": "1920x1920"
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            r = requests.post("https://ark.cn-beijing.volces.com/api/v3/images/generations",
                            json=payload, headers=headers, timeout=60)
            if r.status_code == 200:
                data = r.json()
                for img in data.get("data", []):
                    img_url = img.get("url", "")
                    if img_url:
                        try:
                            dl = requests.get(img_url, timeout=30)
                            out_name = f"enhanced_{int(time.time())}_{hashlib.md5(img_url.encode()).hexdigest()[:6]}.jpg"
                            out_path = os.path.join(UPLOAD_DIR, out_name)
                            with open(out_path, 'wb') as f:
                                f.write(dl.content)
                            saved_urls.append(local_path_to_url(out_path))
                        except Exception:
                            saved_urls.append(img_url)
            else:
                # ARK 失败 → 降级 HiDream
                pass
        except Exception as e:
            pass
    
    # 降级或无照片 → HiDream 文生图
    if not saved_urls:
        from services.model_client import generate_image
        for i in range(2):
            try:
                result = generate_image(prompt, preferred="hidream", size="1024x1024", timeout=120)
                if result.get("success") and result.get("url"):
                    saved_urls.append(result["url"])
            except Exception as ex_: logger.warning(f"[portrait_api]  {ex_}")
    
    return {
        "success": len(saved_urls) > 0,
        "images": saved_urls,
        "prompt": prompt[:200],
        "model": "seedream_with_ref" if pub_url else "hidream",
        "mode": "参考图生图（保留轮廓+瘦脸美颜）" if pub_url else "文生图（无参考照片）"
    }
