"""AI 提供者 — 仅使用已验证通过的API（百炼/万相/CosyVoice/DeepSeek/Agnes）
所有 API Key 从 config/api_keys.json 读取，不再硬编码。
"""
import logging, json, time, socket, os, base64, requests
from services.usage_tracker import log_usage
from typing import Dict, List, Optional

# Key 加载
KEYS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
KEYS_FILE = os.path.join(KEYS_DIR, "api_keys.json")

def _get_key(key_id: str) -> str:
    """从集中配置获取 API key"""
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            keys = json.load(f)
        return keys.get(key_id, {}).get("key", "")
    except Exception:
        return ""

def _get_base_url(key_id: str) -> str:
    try:
        with open(KEYS_FILE, "r", encoding="utf-8") as f:
            keys = json.load(f)
        return keys.get(key_id, {}).get("base_url", "")
    except Exception:
        return ""


logger = logging.getLogger(__name__)

# ===== IPv4 强制 =====
_orig_gai = socket.getaddrinfo
def _ipv4_gai(host, port, family=0, *a, **kw):
    return _orig_gai(host, port, socket.AF_INET, *a, **kw)
socket.getaddrinfo = _ipv4_gai
import http.client
_orig_put = http.client.HTTPConnection.putheader
def _ipv4_putheader(self, h, *vs):
    return _orig_put(self, h, *[v.encode("utf-8").decode("latin-1", errors="replace") if isinstance(v, str) else v for v in vs])
http.client.HTTPConnection.putheader = _ipv4_putheader

# ===== Key 常量（从配置读取） =====
ALIYUN_API_KEY = _get_key("aliyun_bailian")
DEEPSEEK_API_KEY = _get_key("deepseek")
DEEPSEEK_BASE_URL = _get_base_url("deepseek") or "https://api.deepseek.com/v1"

MODEL_MAP = {"premium": "deepseek-chat", "standard": "deepseek-chat", "fast": "deepseek-chat"}
AGENT_STEP_MAP = {"script": "premium", "character": "standard", "storyboard": "standard",
                   "scene": "standard", "video": "standard", "tts": "fast",
                   "bgm": "fast", "subtitle": "fast", "composite": "standard"}


class DeepSeekProvider:
    """DeepSeek 官方 API"""
    def __init__(self, model: str = "deepseek-chat"):
        self.model = model
        self.base_url = DEEPSEEK_BASE_URL
        logger.info("DeepSeekProvider → %s (%s)", self.model, self.base_url)

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 2048,
             timeout: int = 60) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=self.base_url,
                        default_headers={"Content-Type": "application/json"})
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout)
        return rsp.choices[0].message.content or ""

    def chat_stream(self, messages: list, temperature: float = 0.3):
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=self.base_url)
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature, stream=True)
        for chunk in rsp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content



# Aliases for compatibility with model_client LLM dispatch
class DoubaoProvider(DeepSeekProvider):
    """Compatibility alias - uses DeepSeek API"""
    pass

class QwenProvider(DeepSeekProvider):
    """Compatibility alias - uses DeepSeek API"""
    pass

class ZhipuProvider:
    """智谱 GLM-5.2 — 分镜专用，OpenAI兼容API"""
    def __init__(self, model: str = "glm-5.2"):
        self.model = model
        self.api_key = _get_key("zhipu")
        self.base_url = _get_base_url("zhipu") or "https://open.bigmodel.cn/api/paas/v4"

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
             timeout: int = 120) -> str:
        import logging as _log
        _log = _log.getLogger(__name__)
        _log.info(f"ZhipuProvider: model={self.model}, msgs={len(messages)}, max_tokens={max_tokens}")
        from openai import OpenAI
        # GLM-5.2是推理模型，需要足够token给reasoning+content
        actual_max = max(max_tokens, 8192)
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=actual_max, timeout=timeout)
        msg = rsp.choices[0].message
        content = msg.content or ""
        # GLM-5.2可能把输出放在reasoning_content，content为空时用reasoning
        if not content and hasattr(msg, 'reasoning_content') and msg.reasoning_content:
            _log.info("ZhipuProvider: using reasoning_content (content was empty)")
            content = msg.reasoning_content
        _log.info(f"ZhipuProvider: got {len(content)} chars, model={rsp.model}")
        return content

class TongyiWanxiangProvider:
    """阿里云百炼 — 生图 (wanx2.1-t2i-plus / wanxiang)"""
    def __init__(self, model: str = "wanx2.1-t2i-plus"):
        self.model = model
        self.api_key = ALIYUN_API_KEY
        self.base_url = "https://dashscope.aliyuncs.com"
        logger.info("TongyiWanxiangProvider → %s", self.model)

    def generate(self, prompt: str, size: str = "1024*1024", n: int = 1, steps: int = 20):
        """万相生图 — 异步提交 + 轮询结果，最大等待60秒"""
        logger.info("万相生图: model=%s prompt=%s size=%s", self.model, prompt[:80], size)
        try:
            # 1. 异步提交
            import requests as _req
            import time as _time
            headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
            # 万相限制: 512-1440
            safe_size = size
            if safe_size not in ("1024*1024", "1024*768", "768*1024"):
                w, h = safe_size.replace("x", "*").split("*", 1)
                try:
                    if int(w) > 1440 or int(h) > 1440:
                        safe_size = "1024*1024"
                except:
                    safe_size = "1024*1024"
            payload = {"model": self.model, "input": {"prompt": prompt},
                       "parameters": {"size": safe_size, "n": n, "steps": steps}}
            resp = _req.post(f"{self.base_url}/api/v1/services/aigc/text2image/image-synthesis",
                            json=payload, headers={**headers, "X-DashScope-Async": "enable"}, timeout=15)
            if resp.status_code != 200:
                raise Exception(f"提交失败 {resp.status_code}: {resp.text[:200]}")
            task_id = resp.json().get("output", {}).get("task_id", "")
            if not task_id:
                raise Exception(f"未获得 task_id: {resp.text[:200]}")

            # 2. 轮询
            for _ in range(20):
                _time.sleep(3)
                qr = _req.get(f"{self.base_url}/api/v1/tasks/{task_id}", headers=headers, timeout=15)
                qj = qr.json()
                st = qj.get("output", {}).get("task_status", "")
                if st == "SUCCEEDED":
                    urls = [r["url"] for r in qj["output"].get("results", []) if r.get("url")]
                    if not urls and "url" in qj.get("output", {}):
                        urls = [qj["output"]["url"]]
                    log_usage("wanxiang")
                    return {"images": urls, "data": qj.get("output", {})}
                elif st in ("FAILED", "CANCELED"):
                    raise Exception(f"任务失败: {qj.get('output', {}).get('message', qr.text[:200])}")
            raise Exception(f"轮询超时(60s): task_id={task_id}")
        except Exception as e:
            logger.error(f"万相生图失败: {e}")
            raise

    def generate_image(self, prompt: str, size: str = "1024x1024") -> list:
        result = self.generate(prompt, size.replace("x", "*"), n=1)
        return result.get("images", [])


class CosyVoiceV2Provider:
    """阿里云百炼 — CosyVoice TTS"""
    def __init__(self):
        self.api_key = _get_key("aliyun_bailian")
        self.base_url = "https://dashscope.aliyuncs.com"
        logger.info("CosyVoiceV2Provider ready")

    def synthesize(self, text: str, voice: str = "longwan", speed: float = 1.0,
                   format: str = "wav", sample_rate: int = 24000,
                   pitch: str = "auto", volume_gain_db: str = "auto",
                   speech_rate: str = "auto", emotion=None,
                   timeout: int = 120) -> bytes:
        import dashscope
        dashscope.api_key = self.api_key
        rsp = dashscope.SpeechSynthesizer.call(
            model="cosyvoice-v1", text=text, voice=voice, format=format,
            sample_rate=sample_rate, pitch=pitch, volume_gain_db=volume_gain_db,
            speech_rate=speech_rate, timeout=timeout)
        if rsp.get_audio_data():
            import base64
            audio_data = rsp.get_audio_data()
            # Save to file and return dict
            out_path = f"/tmp/cosy_{hash(text) & 0xFFFFFFFF:08x}.wav"
            with open(out_path, "wb") as f:
                f.write(audio_data)
            log_usage("cosyvoice")
            return {"audio_file": out_path, "audio_data": base64.b64encode(audio_data).decode(), "model": "cosyvoice"}
        raise Exception(f"TTS 失败: {str(rsp)[:300]}")


class CosyVoiceProvider:
    """阿里云百炼 — CosyVoice TTS"""
    def __init__(self, api_key=None):
        self.api_key = _get_key("aliyun_bailian") or api_key
        self.base_url = "https://dashscope.aliyuncs.com"
        logger.info("CosyVoiceProvider ready")

    def synthesize(self, text: str, voice: str = "longwan", format: str = "wav",
                   sample_rate: int = 24000, pitch: str = "auto",
                   volume_gain_db: str = "auto", speech_rate: str = "auto",
                   timeout: int = 120) -> bytes:
        import dashscope
        dashscope.api_key = self.api_key
        rsp = dashscope.SpeechSynthesizer.call(
            model="cosyvoice-v1", text=text, voice=voice, format=format,
            sample_rate=sample_rate, pitch=pitch, volume_gain_db=volume_gain_db,
            speech_rate=speech_rate, timeout=timeout)
        if rsp.get_audio_data():
            import base64
            audio_data = rsp.get_audio_data()
            # Save to file and return dict
            out_path = f"/tmp/cosy_{hash(text) & 0xFFFFFFFF:08x}.wav"
            with open(out_path, "wb") as f:
                f.write(audio_data)
            log_usage("cosyvoice")
            return {"audio_file": out_path, "audio_data": base64.b64encode(audio_data).decode(), "model": "cosyvoice"}
        raise Exception(f"TTS 失败: {str(rsp)[:300]}")


class EdgeTTSProvider:
    """Edge TTS — 免费离线 TTS（纯本地）"""
    def __init__(self):
        logger.info("EdgeTTSProvider ready")

    def generate_tts(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural",
                     speed: float = 1.0) -> dict:
        import subprocess, json, base64, os
        out_path = f"/tmp/tts_{os.urandom(4).hex()}.mp3"
        try:
            subprocess.run(["edge-tts", "--text", text,
                          "--voice", voice,
                          "--rate", f"+{int((speed-1)*50)}%" if speed > 1 else f"-{int((1-speed)*50)}%",
                          "--write-media", out_path], capture_output=True, timeout=30)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                with open(out_path, "rb") as f:
                    audio_data = f.read()
                return {"audio_url": f"data:audio/mp3;base64,{base64.b64encode(audio_data).decode()}",
                        "text": text, "voice": voice, "speed": speed}
        except Exception as e:
            logger.warning(f"Edge TTS失败: {e}")
        return {"audio_url": "", "text": text, "voice": voice, "mock": True}


class BailianVideoProvider:
    """阿里云百炼 — 文生视频 (wan2.7-t2v) + 口型同步 (videoretalk)"""
    def __init__(self, api_key=None):
        self.api_key = _get_key("aliyun_bailian") or api_key
        self.base_url = "https://dashscope.aliyuncs.com"
        logger.info("BailianVideoProvider ready")

    def submit_video(self, prompt: str, duration: int = 5) -> str:
        """提交 wan2.7-t2v 视频生成任务，返回 task_id"""
        payload = {
            "model": "wan2.7-t2v",
            "input": {"prompt": prompt},
            "parameters": {"duration": duration, "resolution": "720P", "ratio": "9:16"}
        }
        r = requests.post(
            f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis",
            headers={"Authorization": f"Bearer {self.api_key}",
                      "Content-Type": "application/json",
                      "X-DashScope-Async": "enable"},
            json=payload, timeout=30)
        if r.status_code != 200:
            raise Exception(f"视频提交失败 {r.status_code}: {r.text[:200]}")
        return r.json().get("output", {}).get("task_id", "")

    def poll_task(self, task_id: str, max_wait: int = 300) -> str:
        """轮询异步任务，返回结果 URL"""
        import time
        for i in range(max_wait // 10):
            time.sleep(10)
            r = requests.get(f"{self.base_url}/api/v1/tasks/{task_id}",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=15)
            if r.status_code != 200:
                continue
            data = r.json().get("output", {})
            status = data.get("task_status", "")
            if status == "SUCCEEDED":
                return data.get("video_url", "")
            elif status in ("FAILED", "CANCELED", "UNKNOWN"):
                raise Exception(f"视频任务失败: {data.get('message', '')}")
        raise Exception("视频任务超时")

    def submit_videoretalk(self, video_url: str, audio_url: str, ref_image: str = "") -> str:
        """提交 videoretalk 口型同步任务，返回 task_id"""
        payload = {
            "model": "videoretalk",
            "input": {"video_url": video_url, "audio_url": audio_url},
            "parameters": {"video_extension": False}
        }
        if ref_image:
            payload["input"]["ref_image_url"] = ref_image

        r = requests.post(
            f"{self.base_url}/api/v1/services/aigc/image2video/video-synthesis/",
            headers={"Authorization": f"Bearer {self.api_key}",
                      "Content-Type": "application/json",
                      "X-DashScope-Async": "enable"},
            json=payload, timeout=30)
        if r.status_code != 200:
            raise Exception(f"VideoRetalk 提交失败 {r.status_code}: {r.text[:200]}")
        return r.json().get("output", {}).get("task_id", "")

    def poll_videoretalk(self, task_id: str, max_wait: int = 600) -> str:
        """轮询 videoretalk 任务，返回结果视频 URL"""
        import time
        for i in range(max_wait // 10):
            time.sleep(10)
            r = requests.get(f"{self.base_url}/api/v1/tasks/{task_id}",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=15)
            if r.status_code != 200:
                continue
            data = r.json().get("output", {})
            status = data.get("task_status", "")
            logger.info(f"VideoRetalk poll[{i*10}s]: {status}")
            if status == "SUCCEEDED":
                return data.get("video_url", "")
            elif status in ("FAILED", "UNKNOWN"):
                raise Exception(f"VideoRetalk 失败: {data.get('message', '')}")
        raise Exception("VideoRetalk 超时")


class ARKImageProvider:
    """火山方舟 — 豆包 Seedream 生图"""
    def __init__(self):
        self.api_key = _get_key("ark_volc")
        self.base_url = _get_base_url("ark_volc") or "https://ark.cn-beijing.volces.com/api/v3"

    def generate(self, prompt: str, size: str = "1920x2560", n: int = 1,
                 negative: str = "", style: str = "") -> list:
        payload = {
            "model": "doubao-seedream-5-0-260128",
            "prompt": prompt,
            "n": n,
            "size": size,
            "watermark": False
        }
        if negative:
            payload["negative_prompt"] = negative
        if style:
            payload["style"] = style
        r = requests.post(f"{self.base_url}/images/generations",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=(15, 60))
        if r.status_code != 200:
            raise Exception(f"ARK 生图失败 {r.status_code}: {r.text[:200]}")
        return [item["url"] for item in r.json().get("data", []) if item.get("url")]

    def generate_image(self, prompt: str, size: str = "1920x2560") -> list:
        return self.generate(prompt, size)

    def generate_image_to_image(self, prompt: str, reference_image: str, size: str = "1920x2560", strength: float = 0.55) -> list:
        """图生图：传入参考图URL，基于参考图生成新形象
        strength: 0-1, 越低越像原图(0.3=几乎不变), 越高越自由(0.8=大幅变化)。默认0.55平衡保留脸型+美颜优化"""
        payload = {
            "model": "doubao-seedream-5-0-260128",
            "prompt": prompt,
            "n": 1,
            "size": size,
            "image": reference_image,
            "response_format": "url",
            "watermark": False,
            "negative_prompt": "cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic, wrinkles, pores, blemishes, spots, freckles, moles, scars, acne, rough skin, uneven skin tone, dark circles, eye bags, oily skin, sagging skin, large pores, flabby skin",
            "strength": strength
        }
        r = requests.post(f"{self.base_url}/images/generations",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=(15, 120))
        if r.status_code != 200:
            raise Exception(f"ARK 图生图失败 {r.status_code}: {r.text[:200]}")
        return [item["url"] for item in r.json().get("data", []) if item.get("url")]


class SeedanceProvider:
    """火山方舟 — 豆包 Seedance 视频生成（需先创建 endpoint）"""
    def __init__(self):
        self.api_key = _get_key("ark_volc")
        self.base_url = _get_base_url("ark_volc") or "https://ark.cn-beijing.volces.com/api/v3"
        self.model_id = "doubao-seedance-1-5-pro-251215"

    def generate_video(self, prompt: str, duration: int = 5, image_url: str = "", resolution: str = "720P", max_wait: int = 600) -> dict:
        # ARK 视频生成: 异步任务 API
        # POST /contents/generations/tasks → 返回 task_id
        # GET  /contents/generations/tasks/{id} → 轮询结果
        content = [{"type": "text", "text": prompt}]
        if image_url:
            content.append({"type": "image_url", "image_url": {"url": image_url}})
        
        payload = {
            "model": self.model_id,
            "content": content,
            "parameters": {
                "duration": duration,
                "resolution": resolution.lower().replace("p", "p") if "p" in resolution else "720p",
                "ratio": "9:16",
            }
        }
        
        # Submit task
        r = requests.post(f"{self.base_url}/contents/generations/tasks",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=(15, 30))
        if r.status_code != 200:
            raise Exception(f"Seedance 提交失败 {r.status_code}: {r.text[:200]}")
        
        result = r.json()
        task_id = result.get("id", "")
        if not task_id:
            raise Exception(f"Seedance 无 task_id: {r.text[:200]}")
        
        # Poll for completion
        import time
        max_polls = max(max_wait // 10, 1)
        poll_url = f"{self.base_url}/contents/generations/tasks/{task_id}"
        for _ in range(max_polls):
            time.sleep(10)
            pr = requests.get(poll_url,
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            timeout=15)
            if pr.status_code != 200:
                continue
            pdata = pr.json()
            status = pdata.get("status", "")
            if status == "succeeded":
                vurl = pdata.get("content", {}).get("video_url", "")
                if vurl:
                    return {"data": [{"video_url": vurl}]}
                raise Exception(f"Seedance 无视频URL: {json.dumps(pdata)[:200]}")
            elif status in ("failed", "error"):
                raise Exception(f"Seedance 任务失败: {pdata.get('error', pdata)[:200]}")
        
        raise Exception(f"Seedance 轮询超时（20min） task_id={task_id}")


class AgnesAIProvider:
    """Agnes Hub — 备用生图（免费优先）"""
    def __init__(self, api_key=None):
        self.api_key = _get_key("agnes") or api_key
        self.base_url = _get_base_url("agnes") or "https://apihub.agnes-ai.com/v1"
        logger.info("AgnesAIProvider ready")

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1,
                 model: str = "agnes-image-2.1-flash", timeout: int = 30) -> list:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        payload = {"model": model, "prompt": prompt, "n": n, "size": size}
        r = requests.post(f"{self.base_url}/images/generations",
                          headers=headers, json=payload, timeout=(min(timeout, 15), timeout))
        if r.status_code != 200:
            raise Exception(f"Agnes 生图失败 {r.status_code}: {r.text[:200]}")
        urls = [item["url"] for item in r.json().get("data", []) if item.get("url")]
        return urls

    def generate_image(self, prompt: str, size: str = "1024x1024") -> list:
        """别名 — 兼容智能体调用"""
        return self.generate(prompt, size=size)


# ===== 智能体图片生成路由（Agnes → 智象 → ARK → 百炼 轮询） =====
def smart_generate_image(prompt: str, size: str = "1024*1024") -> list:
    """智能生图：Agnes 优先（免费），不行轮询智象/ARK/百炼"""
    providers = [
        ("Agnes", lambda: AgnesAIProvider().generate(prompt, size.replace("*", "x"))),
        ("ARK", lambda: ARKImageProvider().generate(prompt, size.replace("x", "x"))),
        ("百炼", lambda: TongyiWanxiangProvider().generate(prompt, size)),
    ]
    for name, fn in providers:
        try:
            urls = fn()
            if urls:
                logger.info(f"智能生图: {name} 成功 -> {urls[0][:80]}")
                return urls
        except Exception as e:
            logger.warning(f"智能生图: {name} 失败 {e}")
            continue
    raise Exception("所有生图服务均失败")



# ===== KlingProvider wrapper（兼容 model_client） =====
class KlingProvider:
    """可灵视频生成 Provider — 兼容 model_client 的 _get_provider"""
    def generate_video(self, prompt, image_url="", duration=5, model="kling-v2-6", **kwargs):
        from services.kling_provider import generate_video as _kling_gen
        url = _kling_gen(prompt, image_url, duration=duration, model=model)
        return [url] if url else []

KlingProvider = KlingProvider  # 覆盖旧的 None

# ===== 兼容旧版调用 =====
seedance = SeedanceProvider()
# KlingProvider = None  # removed — now a real class
JimengProvider = None
agnes = AgnesAIProvider()


# ===== HappyHorse wrapper（兼容 model_client） =====
class _HappyHorseProxy:
    def generate_video(self, prompt, image_url="", duration=5, resolution="720P"):
        from services.bailian_provider import BailianVideoProvider
        p = BailianVideoProvider()
        url = p.generate_video(prompt, image_url, duration=duration, resolution=resolution)
        return [url] if url else []

    def generate_r2v(self, prompt, reference_images=None, duration=5, resolution="720P", ratio="9:16", **kwargs):
        from services.bailian_provider import BailianVideoProvider
        p = BailianVideoProvider()
        if not reference_images:
            raise Exception("generate_r2v 需要至少1张参考图（角色肖像）")
        url = p.generate_r2v(prompt, reference_images, duration=duration, resolution=resolution, ratio=ratio)
        return [url] if url else []

class _Wan27Proxy:
    """万相2.7图生视频 — 支持首帧+音频驱动口型"""
    def generate_video(self, prompt, image_url="", duration=5, resolution="720P", audio_url=""):
        from services.bailian_provider import BailianVideoProvider
        p = BailianVideoProvider()
        url = p.generate_video(
            prompt, image_url, audio_url=audio_url, duration=duration,
            model="wan2.7-i2v-2026-04-25", resolution=resolution
        )
        return [url] if url else []

wan2_7_i2v = _Wan27Proxy()

happyhorse = _HappyHorseProxy()


class HiDreamImageProvider:
    """千象HiDream AI — hidreamai.com 文生图（异步提交+轮询）"""
    def __init__(self):
        cfg = _get_key("hidream")
        if isinstance(cfg, dict):
            self.api_key = cfg.get("key", "") or ""
        else:
            self.api_key = cfg or ""
        self.base_url = "https://www.hidreamai.com"
        logger.info("HiDreamImageProvider ready (async)")

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1,
                 timeout: int = 60) -> list:
        """异步提交 + 轮询，最大等待 timeout 秒"""
        import requests as _req, uuid, time as _time
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        # 解析尺寸
        w, h = (size.replace("x", "*").split("*", 1) + ["1024"])[:2]
        res = f"{w}*{h}"
        payload = {
            "prompt": prompt,
            "aspect_ratio": "1:1",
            "img_count": n,
            "version": "v2",
            "resolution": res,
            "request_id": str(uuid.uuid4())  # ⚠️ Could use external order_id if provided
        }
        # 提交
        r = _req.post(f"{self.base_url}/api-pub/gw/v3/image/txt2img/async",
                      headers=headers, json=payload, timeout=15)
        if r.status_code != 200:
            raise Exception(f"HiDream 提交失败 {r.status_code}: {r.text[:200]}")
        j = r.json()
        if j.get("code") != 0:
            raise Exception(f"HiDream 提交返回错误: {j.get('message', r.text[:200])}")
        task_id = j.get("result", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"HiDream 未返回task_id: {r.text[:200]}")
        # 轮询
        for i in range(timeout // 3):
            _time.sleep(3)
            try:
                q = _req.get(f"{self.base_url}/api-pub/gw/v3/image/txt2img/async/results",
                            headers=headers, params={"task_id": task_id}, timeout=10)
                qj = q.json()
                if qj.get("code") != 0:
                    continue  # 临时错误，重试
                results = qj.get("result", {}).get("sub_task_results", [])
                for r_item in results:
                    st = r_item.get("task_status")
                    if st == 1:  # 完成
                        img_url = r_item.get("image", "")
                        if img_url:
                            logger.info(f"HiDream 生图成功: {img_url[:80]}")
                            log_usage("hidream")
                            return [img_url]
                    elif st not in (0, 2):  # 失败
                        raise Exception(f"HiDream 任务失败: {json.dumps(r_item, ensure_ascii=False)[:100]}")
                    else:
                        continue  # 等待/处理中
            except Exception as e:
                if "任务失败" in str(e):
                    raise
                logger.warning(f"HiDream 轮询{i+1}异常: {e}")
                continue
        raise Exception(f"HiDream 轮询超时({timeout}s): task_id={task_id}")

    def generate_image(self, prompt: str, size: str = "1024x1024") -> list:
        return self.generate(prompt, size=size)
class BailianWanxiangChatProvider:
    """万相2.7 Chat API — 生图 (wan2.7-image-pro)"""
    def __init__(self):
        import json
        with open("/www/wwwroot/api.mzsh.top/config/api_keys.json") as f:
            keys = json.load(f)
        self.api_key = keys.get("aliyun_bailian", {}).get("key", "")
        self.base_url = "https://dashscope.aliyuncs.com"
    
    def generate_image(self, prompt: str, size: str = "1024*1024", n: int = 1, **kw) -> list:
        import requests, time
        payload = {"model":"wan2.7-image-pro","input":{"messages":[{"role":"user","content":[{"text":prompt}]}]},"parameters":{"size":size,"n":n,"watermark":False}}
        headers = {"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"}
        resp = requests.post(f"{self.base_url}/api/v1/services/aigc/image-generation/generation",json=payload,headers=headers,timeout=120)
        if resp.status_code!=200: raise Exception(f"API error {resp.status_code}: {resp.text[:200]}")
        j = resp.json()
        task_id = j.get("output",{}).get("task_id","")
        if not task_id: raise Exception(f"No task_id: {str(j)[:300]}")
        for _ in range(30):
            time.sleep(2)
            r2 = requests.get(f"{self.base_url}/api/v1/services/aigc/image-generation/generation/{task_id}",headers=headers,timeout=30, verify=False)
            if r2.status_code!=200: continue
            d = r2.json()
            status = d.get("output",{}).get("task_status","")
            if status=="SUCCEEDED":
                urls = [c["image"] for c in d.get("output",{}).get("results",[])]
                if urls: return urls
            if status in ("FAILED","CANCELED"): raise Exception(f"Task {status}")
        raise Exception(f"Generation timed out task={task_id}")
    
    def generate_image_to_image(self, prompt: str, reference_image: str, size: str = "1024*1024", strength: float = 0.3, negative: str = "") -> list:
        import requests, time, base64
        # 直接传 URL 给万相（阿里云内网可直接访问 OSS，不经过服务器中转）
        img_input = reference_image
        if reference_image and reference_image.startswith("http"):
            img_input = reference_image  # 直接传 URL，万相在阿里云内网能访问
        elif isinstance(reference_image, bytes):
            b64 = base64.b64encode(reference_image).decode("utf-8")
            img_input = f"data:image/png;base64,{b64}"
        elif reference_image and not reference_image.startswith("data:"):
            # 本地路径或 /storage/ 路径
            if reference_image.startswith("/www/wwwroot/"):
                img_input = "https://ai.mzsh.top/" + reference_image[len("/www/wwwroot/"):]
            elif reference_image.startswith("/storage/"):
                img_input = "https://ai.mzsh.top" + reference_image
        content = [{"image": img_input}, {"text": prompt}]
        safe_size = size.replace("x","*")
        neg_text = negative or "blurry face, deformed face, different face, face change, plastic surgery, cartoon face, anime face"
        payload = {"model":"wan2.7-image-pro","input":{"messages":[{"role":"user","content":content}]},"parameters":{"size":safe_size,"n":1,"watermark":False,"negative_prompt":neg_text,"strength":strength}}
        headers = {"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"}
        resp = requests.post(f"{self.base_url}/api/v1/services/aigc/multimodal-generation/generation",json=payload,headers=headers,timeout=120)
        if resp.status_code!=200: raise Exception(f"API error {resp.status_code}: {resp.text[:200]}")
        j = resp.json()
        choices = j.get("output",{}).get("choices",[])
        if choices:
            c = choices[0].get("message",{}).get("content",[])
            urls = [i["image"] for i in c if i.get("type")=="image" and i.get("image")]
            if urls: return urls
        raise Exception(f"No image in response: {str(j.get('output',{}))[:300]}")

bailian_wanxiang_chat = BailianWanxiangChatProvider()
