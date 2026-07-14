import json, time, logging
from fastapi import APIRouter, Request
from app_db import fetchone, fetchall, execute
logger = logging.getLogger("api.v2")
router = APIRouter(prefix="/v2/characters", tags=["v2"])

@router.get("/personal")
async def v2_characters_list(request: Request):
    user_id = getattr(request.state, "user_id", 0)
    rows = fetchall("SELECT characters FROM projects WHERE user_id=? AND characters IS NOT NULL AND characters!='[]' ORDER BY created_at DESC", (user_id,)) if user_id else []
    seen = set(); chars = []
    for row in rows:
        try:
            for c in json.loads(row.get("characters", "[]")):
                n = c.get("name", "")
                if n and n not in seen:
                    seen.add(n)
                    chars.append(dict(name=n,gender=c.get("gender",""),image_url=c.get("image_url",c.get("photo","")),ref_image_url=c.get("ref_image_url","")))
        except: pass
    return {"success": True, "data": {"characters": chars, "total": len(chars)}}

@router.put("/personal/{char_name}")
async def v2_characters_update(char_name: str, body: dict, request: Request):
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT id, characters FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,))
    if not row: return {"success": False, "error": "no project"}
    chars = json.loads(row.get("characters", "[]"))
    found = False
    for c in chars:
        if c.get("name") == char_name:
            for k,v in body.items():
                if v: c[k] = v
            found = True; break
    if not found: chars.append(body)
    execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), row["id"]))
    return {"success": True}

@router.post("/{char_name}/generate")
async def v2_characters_generate(char_name: str, request: Request):
    from services.model_client import UnifiedModel
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT id, characters FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,))
    if not row: return {"success": False, "error": "no project"}
    chars = json.loads(row.get("characters", "[]"))
    char = next((c for c in chars if c.get("name") == char_name), None)
    if not char: return {"success": False, "error": "char not found"}
    ref = char.get("ref_image_url", "")
    try:
        result = UnifiedModel.image_to_image(prompt="IDENTICAL FACE, photorealistic portrait, real person photo, realistic skin texture and facial details, natural studio lighting,真人头像,写实照片,真实皮肤质感,不是卡通不是动漫不是3D渲染,NOT cartoon NOT anime NOT 3D render NOT illustration, only change clothing and hair style", reference_image=ref, size="1024x1024", timeout=120, strength=0.08) if ref else UnifiedModel.image(prompt=f"{char_name} photorealistic human NOT cartoon NOR anime", size="1024x1024", timeout=120)
    except Exception as e: return {"success": False, "error": str(e)}
    if isinstance(result, dict) and result.get("success"):
        url = result.get("url", "")
        for c in chars:
            if c.get("name") == char_name: c["image_url"] = url; break
        execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), row["id"]))
        return {"success": True, "data": {"image_url": url}}
    return {"success": False, "error": "generate failed"}
