import json, time, requests, logging, os, hashlib, hmac, base64, uuid
logger = logging.getLogger(__name__)
KLING_AK = ""
KLING_SK = ""
BASE_URL = "https://api.klingai.com"
def _init_keys():
    global KLING_AK, KLING_SK
    from services.ai_providers import _get_key
    KLING_AK = _get_key("kling_ak") or os.environ.get("KLING_AK", "")
    KLING_SK = _get_key("kling_sk") or os.environ.get("KLING_SK", "")
def _sign(method, path, body="", ak="", sk=""):
    if not ak or not sk:
        return {}
    now = int(time.time())
    exp = now + 1800
    header = {"alg": "HMAC-SHA256", "typ": "JWT"}
    payload = {"iss": ak, "exp": exp, "nbf": now, "iat": now, "jti": uuid.uuid4().hex}
    def _b64(data):
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
    msg = _b64(json.dumps(header, separators=(",", ":")).encode())
    msg += "." + _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(sk.encode(), msg.encode(), hashlib.sha256).digest()
    return {"Authorization": "Bearer " + msg + "." + _b64(sig)}
def generate_video(prompt, image_url="", duration=5, cfg_scale=0.5, model="kling-v1.6"):
    _init_keys()
    if not KLING_AK or not KLING_SK:
        raise Exception("Kling AK/SK not configured")
    path = "/v1/videos/generate"
    headers = _sign("POST", path, ak=KLING_AK, sk=KLING_SK)
    headers["Content-Type"] = "application/json"
    payload = {"model": model, "duration": duration}
    if image_url:
        payload["image"] = image_url
        if prompt:
            payload["prompt"] = prompt
    else:
        payload["prompt"] = prompt or "dynamic scene"
    logger.info("Kling submit: %s duration=%d", prompt[:60], duration)
    r = requests.post(BASE_URL + path, json=payload, headers=headers, timeout=30)
    if r.status_code != 200:
        raise Exception("Kling submit failed %d: %s" % (r.status_code, r.text[:300]))
    task_id = r.json().get("data", {}).get("task_id", "")
    if not task_id:
        raise Exception("Kling no task_id: " + r.text[:200])
    logger.info("Kling task_id=%s polling...", task_id)
    for i in range(60):
        time.sleep(5)
        status, videos = _poll_task(task_id)
        if status == "succeed" and videos:
            url = videos[0].get("url", "")
            if url:
                logger.info("Kling succeed: %s", url[:60])
                return url
        elif status in ("failed", "fail"):
            raise Exception("Kling task failed: " + task_id)
        if i % 6 == 0:
            logger.info("Kling poll %ds", i * 5)
    raise Exception("Kling timeout(300s): " + task_id)
def _poll_task(task_id):
    _init_keys()
    path = "/v1/videos/generate/" + task_id
    headers = _sign("GET", path, ak=KLING_AK, sk=KLING_SK)
    if not headers:
        raise Exception("Kling poll sign failed")
    try:
        r = requests.get(BASE_URL + path, headers=headers, timeout=15, verify=False)
        if r.status_code == 200:
            data = r.json().get("data", {}).get("task_response", {})
            return data.get("status", ""), data.get("videos", [])
        return "", []
    except Exception as e:
        logger.warning("Kling poll error: %s", e)
        return "", []