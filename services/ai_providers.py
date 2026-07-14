"""AI 提供者 — 仅使用已验证通过的API（百炼/万相/CosyVoice/DeepSeek/Agnes）
所有 API Key 从 config/api_keys.json 读取，不再硬编码。
"""
import logging, json, time, socket, os, base64, requests
from services.usage_tracker import log_usage
from utils.path_util import local_path_to_url
from app_config import BASE_URL
from typing import Dict, List, Optional

# Key 加载
KEYS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config")
KEYS_FILE = os.path.join(KEYS_DIR, "api_keys.json")

def _get_key(key_id: str) -> str:
    """从集中配置获取 API key
    优先当前线程的会员 Key，没有则走系统配置
    """
    # 检查当前线程是否有会员自己的 Key
    user_keys = get_user_api_keys()
    if user_keys and key_id in user_keys:
        return user_keys[key_id]
    # 回退到系统 Key
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


# ── 线程级会员 Key 覆盖（BYOK） ──
import threading
_user_key_ctx = threading.local()

def set_user_api_keys(user_id: int):
    """在当前线程设置会员自己的 API Key"""
    try:
        from services.user_key_manager import get_user_keys_dict
        keys = get_user_keys_dict(user_id)
        _user_key_ctx.override_keys = keys
    except Exception:
        _user_key_ctx.override_keys = {}

def clear_user_api_keys():
    """清除当前线程的会员 Key 覆盖"""
    _user_key_ctx.override_keys = {}

def get_user_api_keys() -> dict:
    """获取当前线程的会员 Key"""
    return getattr(_user_key_ctx, "override_keys", {})


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
        client = OpenAI(api_key=_get_key("deepseek"), base_url=self.base_url,
                        default_headers={"Content-Type": "application/json"})
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout)
        return rsp.choices[0].message.content or ""

    def chat_stream(self, messages: list, temperature: float = 0.3):
        from openai import OpenAI
        client = OpenAI(api_key=_get_key("deepseek"), base_url=self.base_url)
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature, stream=True)
        for chunk in rsp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content



# Aliases for compatibility with model_client LLM dispatch
class DoubaoProvider:
    """豆包 — 走火山方舟 ARK API（不是 DeepSeek）"""
    def __init__(self, model: str = "doubao-seed-2-1-pro-260628"):
        self.model = model
        self.api_key = _get_key("ark_volc")
        self.base_url = _get_base_url("ark_volc") or "https://ark.cn-beijing.volces.com/api/v3"
        logger.info("DoubaoProvider(ARK) → %s (%s)", self.model, self.base_url)

    def chat(self, messages: list, temperature: float = 0.3, max_tokens: int = 8192,
             timeout: int = 1200) -> str:
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url,
                        default_headers={"Content-Type": "application/json"})
        # 关闭推理思考模式（doubao-seed系列是推理模型，不关会超时）
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature,
            max_tokens=max_tokens, timeout=timeout,
            extra_body={"thinking": {"type": "disabled"}})
        return rsp.choices[0].message.content or ""

    def chat_stream(self, messages: list, temperature: float = 0.3):
        from openai import OpenAI
        client = OpenAI(api_key=self.api_key, base_url=self.base_url)
        rsp = client.chat.completions.create(
            model=self.model, messages=messages, temperature=temperature, stream=True)
        for chunk in rsp:
            if chunk.choices and chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content

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
             timeout: int = 1200) -> str:
        import logging as _log, json as _json
        _log = _log.getLogger(__name__)
        _log.info(f"ZhipuProvider: model={self.model}, msgs={len(messages)}, max_tokens={max_tokens}")
        # GLM-5.2 是推理模型，按官方文档必须用流式（stream:true），否则会超时。
        # 官方文档推荐 temperature=1.0，不传 max_tokens（让模型自行决定推理+输出长度）。
        import requests as _req
        url = f"{self.base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 1.0,  # 官方文档推荐
            "stream": True
        }
        r = _req.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
        if r.status_code != 200:
            raise Exception(f"GLM API失败 {r.status_code}: {r.text[:200]}")
        content_parts = []
        reasoning_parts = []
        for line in r.iter_lines():
            if line:
                line = line.decode('utf-8')
                if line.startswith('data: '):
                    data = line[6:]
                    if data == '[DONE]':
                        break
                    try:
                        chunk = _json.loads(data)
                        delta = chunk.get('choices', [{}])[0].get('delta', {})
                        if delta.get('content'):
                            content_parts.append(delta['content'])
                        if delta.get('reasoning_content'):
                            reasoning_parts.append(delta['reasoning_content'])
                    except Exception:
                        pass
        content = ''.join(content_parts)
        # GLM-5.2 可能 content 为空，用 reasoning_content 兜底
        if not content and reasoning_parts:
            _log.info("ZhipuProvider: using reasoning_content (content was empty)")
            content = ''.join(reasoning_parts)
        _log.info(f"ZhipuProvider: got {len(content)} chars, model={self.model}")
        return content
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

    def generate_image(self, prompt: str, size: str = "1024x1024", timeout: int = 120, **kwargs) -> list:
        result = self.generate(prompt, size.replace("x", "*"), n=1)
        return result.get("images", [])


class SeedAudioProvider:
    """火山语音 — Seed Audio 1.0 (HTTP TTS)"""
    def __init__(self):
        self.name = "seed-audio-1.0"
        self.api_url = "https://openspeech.bytedance.com/api/v3/tts/create"
        with open('/www/wwwroot/api.mzsh.top/config/api_keys.json') as f:
            cfg = json.load(f)
            self._api_key = cfg.get('openspeech', {}).get('key', '')

    def synthesize(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural",
                     speed: float = 1.0, request_id: str = "") -> dict:
        """豆包音频生成 1.0 — HTTP TTS
        返回 dict: {"audio_file": 本地路径, "audio_data": base64, "model": "seed-audio-1.0"}
        """
        if not self._api_key:
            raise RuntimeError("openspeech API Key 未配置")

        payload = {
            "model": "seed-audio-1.0",
            "text_prompt": text,
            "audio_config": {
                "format": "mp3",
                "sample_rate": 24000,
            },
        }

        if voice:
            payload["speaker"] = voice

        if speed != 1.0:
            sr = int((speed - 1.0) * 100)
            sr = max(-50, min(100, sr))
            payload["audio_config"]["speech_rate"] = sr

        headers = {
            "X-Api-Key": self._api_key,
            "Content-Type": "application/json",
        }
        if request_id:
            headers["X-Api-Request-Id"] = request_id

        logger.info(f"[SeedAudioProvider] TTS text={text[:60]}... voice={voice} request_id={request_id}")

        resp = requests.post(self.api_url, headers=headers, json=payload, timeout=120)

        if resp.status_code != 200:
            try:
                err = resp.json()
                msg = err.get("message", str(err.get("code", "")))
            except Exception:
                msg = resp.text[:200]
            raise RuntimeError(f"seed-audio-1.0 TTS 失败 ({resp.status_code}): {msg}")

        data = resp.json()
        if data.get("code", -1) != 0:
            raise RuntimeError(f"seed-audio-1.0 返回错误: {data.get('message', str(data))}")

        audio_b64 = data.get("audio", "")
        if not audio_b64:
            raise RuntimeError("seed-audio-1.0 返回空音频")

        audio_data = base64.b64decode(audio_b64)
        audio_url = data.get("url", "")

        from services.usage_tracker import log_usage
        duration = data.get("original_duration", 0)
        log_usage("seed-audio-1.0", duration=duration, chars=len(text))

        # 保存到本地存储
        os.makedirs("/www/wwwroot/storage/audio", exist_ok=True)
        out_name = f"tts_{uuid.uuid4().hex[:12]}.mp3"
        out_path = f"/www/wwwroot/storage/audio/{out_name}"
        with open(out_path, "wb") as f:
            f.write(audio_data)

        logger.info(f"[SeedAudioProvider] ✅ TTS 成功 duration={duration}s file={out_name}")

        result = {
            "audio_file": out_path,
            "audio_data": audio_b64,
            "duration": duration,
            "model": "seed-audio-1.0",
        }
        if audio_url:
            result["url"] = audio_url
        return result


class CosyVoiceV2Provider:
    """阿里云百炼 — CosyVoice V2 (WebSocket + instruction 控制)"""
    def __init__(self):
        logger.info("CosyVoiceV2Provider ready (instruction support via WebSocket)")
        self.name = "cosyvoice-v2"

    def synthesize(self, text: str, voice: str = "longxiaochun_v2",
                   speed: float = 1.0,
                   format: str = "wav", sample_rate: int = 24000,
                   pitch: str = "auto", volume_gain_db: str = "auto",
                   speech_rate: str = "auto", emotion=None,
                   instruction: str = None,
                   timeout: int = 120) -> dict:
        """
        CosyVoice V2 with instruction control.
        调用方可通过 instruction 参数传递自然语言描述来控制人设/情绪/语速/场景。
        emotion/speed 保留向下兼容——instruction 优先。
        返回 dict: {"audio_file": 本地路径, "audio_data": base64, "model": "cosyvoice-v2"}
        """
        from services.cosy_v2_provider import CosyVoiceV2Provider as _V2
        provider = _V2()
        return provider.synthesize(
            text=text,
            voice=voice or "longxiaochun_v2",
            instruction=instruction,
            timeout=timeout
        )


class CosyVoiceProvider:
    """阿里云百炼 — CosyVoice TTS"""
    def __init__(self, api_key=None):
        self.api_key = _get_key("aliyun_bailian") or api_key
        self.base_url = "https://dashscope.aliyuncs.com"
        logger.info("CosyVoiceProvider ready")

    def synthesize(self, text: str, voice: str = "longwan", format: str = "wav",
                   sample_rate: int = 24000, pitch: str = "auto",
                   volume_gain_db: str = "auto", speech_rate: str = "auto",
                   timeout: int = 1200) -> bytes:
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

    def synthesize(self, text: str, voice: str = "zh-CN-XiaoxiaoNeural",
                     speed: float = 1.0) -> dict:
        """[bugfix] 修复 --rate 参数(原 -0% 导致失败) + 落盘到持久目录 + 检查 returncode"""
        import subprocess, base64, os
        os.makedirs("/www/wwwroot/storage/audio", exist_ok=True)
        out_path = f"/www/wwwroot/storage/audio/edge_{os.urandom(6).hex()}.mp3"
        try:
            cmd = ["edge-tts", "--text", text, "--voice", voice, "--write-media", out_path]
            # speed=1.0 时不传 --rate（原代码生成的 "-0%" 会让 edge-tts 报错）
            if speed and speed != 1.0:
                pct = int((speed - 1) * 100)
                cmd += ["--rate", f"{pct:+d}%"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0:
                logger.warning(f"Edge TTS returncode={r.returncode} stderr={r.stderr[:150]}")
            if os.path.exists(out_path) and os.path.getsize(out_path) > 100:
                with open(out_path, "rb") as f:
                    audio_data = f.read()
                return {"audio_url": f"data:audio/mp3;base64,{base64.b64encode(audio_data).decode()}",
                        "audio_file": out_path,
                        "text": text, "voice": voice, "speed": speed}
            else:
                logger.warning(f"Edge TTS 输出文件无效: {out_path}")
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

    def poll_videoretalk(self, task_id: str, max_wait: int = 1200) -> str:
        """轮询 videoretalk 任务，返回结果视频 URL。完整记录错误信息。"""
        import time
        for i in range(max_wait // 10):
            time.sleep(10)
            r = requests.get(f"{self.base_url}/api/v1/tasks/{task_id}",
                             headers={"Authorization": f"Bearer {self.api_key}"},
                             timeout=15)
            if r.status_code != 200:
                logger.warning(f"VideoRetalk poll HTTP {r.status_code}: {r.text[:100]}")
                continue
            resp = r.json()
            data = resp.get("output", {})
            status = data.get("task_status", "")
            logger.info(f"VideoRetalk poll[{i*10}s]: {status}")
            if status == "SUCCEEDED":
                video_url = data.get("video_url", "")
                usage = resp.get("usage", {})
                logger.info(f"VideoRetalk 成功: dur={usage.get('video_duration','?')}s size={usage.get('size','?')} fps={usage.get('fps','?')}")
                return video_url
            elif status in ("FAILED", "UNKNOWN"):
                code = data.get("code", "Unknown")
                message = data.get("message", "无错误信息")
                # 完整记录错误码和message，方便定位
                logger.error(f"VideoRetalk 失败 code={code} message={message} task={task_id}")
                raise Exception(f"VideoRetalk 失败 [{code}]: {message}")
        raise Exception(f"VideoRetalk 超时(等待{max_wait}s) task={task_id}")


class ARKImageProvider:
    """火山方舟 — 豆包 Seedream 生图"""
    def __init__(self):
        self.api_key = _get_key("ark_volc")
        self.base_url = _get_base_url("ark_volc") or "https://ark.cn-beijing.volces.com/api/v3"

    def generate(self, prompt: str, size: str = "2K", n: int = 1,
                 negative: str = "", style: str = "",
                 request_id: str = "") -> list:
        """文生图。严格按火山方舟官方文档。"""
        payload = {
            "model": "doubao-seedream-5-0-260128",
            "prompt": prompt,
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        r = requests.post(f"{self.base_url}/images/generations",
                          headers=headers, json=payload, timeout=(10, 110))
        if r.status_code != 200:
            raise Exception(f"ARK 生图失败 {r.status_code}: {r.text[:200]}")
        return [item["url"] for item in r.json().get("data", []) if item.get("url")]

    def generate_image(self, prompt: str, size: str = "2K") -> list:
        return self.generate(prompt, size)

    def generate_image_to_image(self, prompt: str, reference_image: str, size: str = "2K", strength: float = 0.55) -> list:
        """图生图：传入参考图URL，基于参考图生成新形象。
        严格按火山方舟官方文档（PDF 第524-527页）：只传 model/prompt/image/size/
        response_format/watermark。strength/negative_prompt/n 文档未定义，已移除。
        锁脸程度靠 prompt 描述控制（调用方已构建详细保脸指令）。"""
        payload = {
            "model": "doubao-seedream-5-0-260128",
            "prompt": prompt,
            "image": reference_image,
            "size": size,
            "response_format": "url",
            "watermark": False,
        }
        r = requests.post(f"{self.base_url}/images/generations",
                          headers={"Authorization": f"Bearer {self.api_key}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=(10, 110))
        if r.status_code != 200:
            raise Exception(f"ARK 图生图失败 {r.status_code}: {r.text[:200]}")
        return [item["url"] for item in r.json().get("data", []) if item.get("url")]


class SeedanceProvider:
    """火山方舟 — 豆包 Seedance 2.0 视频生成（官方 API 标准）
    - generate_audio=True → 自动生成台词+BGM+口型同步
    - 台词写在 prompt 中，模型从文本提取对话做口型对齐
    - 参数均在顶层（不在 parameters 内嵌）
    """
    def __init__(self):
        self.api_key = _get_key("ark_volc")
        self.base_url = _get_base_url("ark_volc") or "https://ark.cn-beijing.volces.com/api/v3"
        # 从 MODEL_REGISTRY 读 model_id
        try:
            from services.model_spec import SPEC
            spec = SPEC.get("seedance", {})
            self.model_id = spec.get("model_id", "doubao-seedance-2-0-260128")
        except Exception:
            self.model_id = "doubao-seedance-2-0-260128"

    def generate_video(self, prompt: str, duration: int = 5, image_url: str = "",
                       resolution: str = "720P", max_wait: int = 1200,
                       first_frame_url: str = "",
                       reference_images: list = None,
                       request_id: str = "", dialogue_text: str = "",
                       model_override: str = "",
                       edit_video_url: str = "") -> dict:
        """生成视频 - 1.5 Pro"""
        actual_model = model_override or self.model_id
        
        content = [{"type": "text", "text": prompt}]
        if edit_video_url:
            content.append({"type": "video_url", "video_url": {"url": edit_video_url}, "role": "reference_video"})
        if edit_video_url:
            content.append({"type": "video_url", "video_url": {"url": edit_video_url},
                "role": "reference_video"})
        ff = first_frame_url or image_url
        # 2.0: 参考图
        if ff:
            content.append({
                "type": "image_url",
                "image_url": {"url": ff},
                "role": "reference_image"
            })
        if reference_images:
            for ref_url in reference_images[:8]:
                if ref_url and ref_url != ff:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": ref_url},
                        "role": "reference_image"
                    })

        # 顶层参数（官方 API 文档标准）
        payload = {
            "model": actual_model,
            "content": content,
            "generate_audio": True,
            "resolution": "720P",              # 生成有声视频（台词+BGM+口型同步）
            "duration": duration,
            "resolution": resolution.lower() if "p" in resolution else "720p",
            "ratio": "adaptive",
            "watermark": False,                  # 不加水印
            "return_last_frame": True,           # 返回尾帧供下一镜衔接
        }

        # 可选：额外台词文本，增强口型对齐稳定性
        if dialogue_text:
            payload["dialogue_text"] = dialogue_text
            logger.info(f"[SeedanceProvider] 台词语音同步: {dialogue_text[:80]}...")
        
        # Submit task
        headers = {"Authorization": f"Bearer {self.api_key}",
                   "Content-Type": "application/json"}
        if request_id:
            headers["X-Request-Id"] = request_id
        r = requests.post(f"{self.base_url}/contents/generations/tasks",
                          headers=headers, json=payload, timeout=(30, 60))
        if r.status_code != 200:
            raise Exception(f"Seedance 提交失败 {r.status_code}: {r.text[:200]}")
        
        result = r.json()
        task_id = result.get("id", "")
        logger.info("[SeedanceProvider] task: " + str(task_id))
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
                content_data = pdata.get("content", {})
                vurl = content_data.get("video_url", "")
                last_frame = content_data.get("last_frame_url", "")
                if vurl:
                    result = {"data": [{"video_url": vurl}]}
                    if last_frame:
                        result["last_frame_url"] = last_frame  # 尾帧供下一镜衔接
                        logger.info(f"Seedance 尾帧: {last_frame[:80]}")
                    return result
                raise Exception(f"Seedance 无视频URL: {json.dumps(pdata)[:200]}")
            elif status in ("failed", "error"):
                raise Exception(f"Seedance 任务失败: {pdata.get('error', pdata)[:200]}")
        
        raise Exception(f"Seedance 轮询超时（20min） task_id={task_id}")




    def query_task(self, task_id: str) -> dict:
        """查询Seedance任务状态"""
        import requests
        headers = {"Authorization": f"Bearer {self.api_key}"}
        r = requests.get(f"{self.base_url}/contents/generations/tasks/{task_id}",
                        headers=headers, timeout=15)
        if r.status_code != 200:
            return {"success": False, "error": "HTTP " + str(r.status_code)}
        data = r.json()
        status = data.get("status", "")
        if status == "succeeded":
            vurl = data.get("content", {}).get("video_url", "")
            return {"success": True, "status": "succeeded", "video_url": vurl}
        elif status in ("failed", "error"):
            return {"success": False, "status": "failed", "error": str(data.get("error", ""))[:200]}
        else:
            return {"success": True, "status": status}


class LiZhenProvider:
    """LiZhen Seedance 2.0 - Kuaizi OpenAPI (async + polling, auto-face-whitelist)"""
    def __init__(self):
        self.api_key = "kz-9ZQxS9M2QXefnqnQVbJnCP5ryhgXDY2wQNF3DtgU"  # hardcoded, _get_key has invisible char issue
        self.base_url = "https://aiopenapi.kuaizi.cn"  # Kuaizi LiZhen API
        self.model_id = "doubao-seedance-2-0-260128"

    def generate_video(self, prompt: str, duration: int = 5, image_url: str = "",
                       resolution: str = "720P", max_wait: int = 3600,
                       first_frame_url: str = "",
                       reference_images: list = None,
                       dialogue_text: str = "",
                       ratio: str = "9:16") -> dict:
        import requests as _r, time as _t
        headers = {"ApiKey": self.api_key, "Content-Type": "application/json"}
        
        images = []
        # 锁脸：只传 first_frame，不混 reference_image
        if first_frame_url:
            images.append({"url": first_frame_url, "role": "reference_image"})
        elif image_url:
            images.append({"url": image_url, "role": "reference_image"})
        if reference_images:
            for ref in reference_images[:8]:
                if ref and ref != first_frame_url:
                    images.append({"url": ref, "role": "reference_image"})
        
        payload = {
            "prompt": prompt,
            "mode": "pro",
            "images": images[:9] if images else None,
            "resolution": resolution.lower() if "p" in resolution else "720p",
            "ratio": ratio,
            "duration": duration if 4 <= duration <= 15 else 5,
            "generate_audio": True,
            "watermark": False,
        }
        if dialogue_text:
            payload["prompt"] = prompt + chr(12289) + chr(21488) + chr(35789) + chr(65306) + dialogue_text
        
        payload = {k: v for k, v in payload.items() if v is not None}
        
        create_url = self.base_url + "/ai-open-platform-api/v1/lz/video/task/create"
        r = _r.post(create_url, json=payload, headers=headers, timeout=30)
        if r.status_code != 200:
            return {"success": False, "data": [], "error": "LiZhen HTTP " + str(r.status_code) + ": " + r.text[:200]}
        resp = r.json()
        if resp.get("code") != 0:
            return {"success": False, "data": [], "error": "LiZhen: " + resp.get("message", "")[:200]}
        task_id = resp.get("data", {}).get("task_id", "")
        if not task_id:
            return {"success": False, "data": [], "error": "no task_id"}
        
        logger.info("[LiZhen] task: " + str(task_id))
        
        query_url = self.base_url + "/ai-open-platform-api/v1/lz/video/task/status"
        for _ in range(max_wait // 5):
            _t.sleep(5)
            r = _r.post(query_url, json={"task_id": task_id}, headers=headers, timeout=15)
            if r.status_code != 200:
                continue
            resp = r.json()
            if resp.get("code") != 0:
                continue
            data = resp.get("data", {})
            status = data.get("status", "")
            if status == "succeeded":
                video_url = data.get("video_url", "")
                last_frame = data.get("last_frame_url", "")
                logger.info("[LiZhen] ok: " + str(video_url)[:80])
                return {"success": True, "data": [{"video_url": video_url, "url": video_url}], "last_frame_url": last_frame}
            elif status == "failed":
                err = data.get("error", "unknown")
                logger.error("[LiZhen] fail: " + str(err))
                return {"success": False, "data": [], "error": "LiZhen: " + str(err)[:200]}
        
        logger.warning("[LiZhen] timeout after " + str(max_wait) + "s, task_id=" + str(task_id))
        return {"success": False, "data": [], "error": "LiZhen timeout", "task_id": task_id}

    def query_task(self, task_id: str) -> dict:
        """主动查询任务状态，返回完整结果"""
        import requests as _r
        headers = {"ApiKey": self.api_key, "Content-Type": "application/json"}
        query_url = self.base_url + "/ai-open-platform-api/v1/lz/video/task/status"
        r = _r.post(query_url, json={"task_id": task_id}, headers=headers, timeout=15)
        if r.status_code != 200:
            return {"success": False, "error": "HTTP " + str(r.status_code)}
        resp = r.json()
        if resp.get("code") != 0:
            return {"success": False, "error": resp.get("message", "unknown")}
        data = resp.get("data", {})
        status = data.get("status", "")
        if status == "succeeded":
            video_url = data.get("video_url", "")
            return {"success": True, "status": "succeeded", "video_url": video_url, "data": data}
        elif status == "failed":
            return {"success": False, "status": "failed", "error": data.get("error", "unknown")}
        else:
            return {"success": True, "status": status, "data": data}


class AgnesAIProvider:
    """AgnesAI Full-Stack: LLM + Image + Video + Download"""
    def __init__(self, api_key=None, model=None):
        self.api_key = _get_key("agnes") or api_key
        self.base_url = _get_base_url("agnes") or "https://apihub.agnes-ai.com/v1"
        self.poll_url = "https://apihub.agnes-ai.com/agnesapi"
        self.default_model = model or "agnes-2.0-flash"
        logger.info("AgnesAIProvider ready (LLM+Image+Video+Download)")

    def chat(self, messages: list, model: str = None, temperature: float = 0.7, max_tokens: int = 4096, timeout: int = 300) -> dict:
        model = model or self.default_model
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        payload = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        r = requests.post(self.base_url + "/chat/completions", headers=headers, json=payload, timeout=(30, timeout))
        if r.status_code != 200:
            raise Exception("Agnes LLM failed " + str(r.status_code) + ": " + r.text[:200])
        data = r.json()
        return {"success": True, "text": data["choices"][0]["message"]["content"], "model": model}

    def generate(self, prompt: str, size: str = "1024x1024", n: int = 1,
                 model: str = "agnes-image-2.1-flash", timeout: int = 120,
                 reference_image: str = "", strength: float = 0.6) -> list:
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        payload = {"model": model, "prompt": prompt, "n": n, "size": size}
        if reference_image:
            payload["image"] = reference_image
            payload["strength"] = strength  # i2i强度: 0=保持原图 1=自由发挥
        r = requests.post(self.base_url + "/images/generations", headers=headers, json=payload, timeout=(30, timeout))
        if r.status_code == 503 or "overloaded" in r.text.lower():
            raise Exception("AGNES_OVERLOADED: " + r.text[:150])
        if r.status_code != 200:
            raise Exception("Agnes image failed " + str(r.status_code) + ": " + r.text[:200])
        urls = [item["url"] for item in r.json().get("data", []) if item.get("url")]
        return urls

    def generate_image(self, prompt: str, size: str = "1024x1024", timeout: int = 120, **kwargs) -> list:
        ref = kwargs.get("reference_image", "")
        strength = kwargs.get("strength", 0.6)
        return self.generate(prompt, size=size, timeout=timeout, reference_image=ref, strength=strength)

    def generate_video(self, prompt: str, image_url: str = "", duration: int = 5, resolution: str = "720p", max_wait: int = 3600) -> dict:
        # 限制: num_frames = 8n+1, 最大18秒=145帧
        duration = max(1, min(18, duration))
        headers = {"Authorization": "Bearer " + self.api_key, "Content-Type": "application/json"}
        h, w = (1280, 720) if "720" in resolution else (1920, 1080)
        num_frames = duration * 8 + 1
        logger.info(f"[AgnesVideo] duration={duration}s, frames={num_frames}")
        payload = {"model": "agnes-video-v2.0", "prompt": prompt, "height": h, "width": w, "num_frames": num_frames, "frame_rate": 8}
        if image_url:
            payload["image"] = image_url  # API uses "image" not "image_url"
        r = requests.post(self.base_url + "/videos", headers=headers, json=payload, timeout=(30, 120))
        if r.status_code == 429:
            raise Exception("Agnes video rate limited (2/min): " + r.text[:150])
        if r.status_code != 200:
            raise Exception("Agnes video submit failed " + str(r.status_code) + ": " + r.text[:200])
        data = r.json()
        video_id = data.get("video_id", data.get("id", data.get("task_id", "")))
        if not video_id:
            raise Exception("Agnes video no id: " + str(data)[:200])
        logger.info("[AgnesVideo] task=" + str(video_id))
        import time as _t
        for i in range(max_wait // 10):
            _t.sleep(10)
            # Poll via /agnesapi (returns URL, unlike /v1/videos/{id})
            pr = requests.get(self.poll_url + "?video_id=" + video_id, headers={"Authorization": "Bearer " + self.api_key}, timeout=30)
            if pr.status_code != 200:
                continue
            data = pr.json()
            status = data.get("status", data.get("internal_status", ""))
            progress = data.get("progress", data.get("internal_progress", 0))
            if status in ("completed", "succeeded", "done"):
                url = data.get("url", data.get("video_url", data.get("output", "")))
                if url:
                    logger.info("[AgnesVideo] completed, url=" + url[:80])
                    return {"success": True, "video_url": url, "model": "agnes-video-v2.0"}
                raise Exception("Agnes video completed but no url")
            elif status in ("failed", "error"):
                raise Exception("Agnes video failed: " + str(data.get("error", ""))[:200])
            if i % 3 == 0:
                logger.info("[AgnesVideo] poll[" + str(i*10) + "s]: " + status + " " + str(progress) + "%")
        raise Exception("Agnes video timeout after " + str(max_wait) + "s")

    def download_to_local(self, url: str, prefix: str = "agnes") -> str:
        r = requests.get(url, timeout=120)
        if r.status_code != 200:
            raise Exception("Download failed " + str(r.status_code))
        import hashlib, os
        h = hashlib.md5(url.encode()).hexdigest()[:12]
        dest = "/www/wwwroot/storage/figures"
        os.makedirs(dest, exist_ok=True)
        local = dest + "/" + prefix + "_" + h + ".jpg"
        with open(local, "wb") as f:
            f.write(r.content)
        from utils.path_util import local_path_to_url
        return local_path_to_url(local)


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

    def generate_image(self, prompt: str, size: str = "1024x1024", timeout: int = 120, **kwargs) -> list:
        ref = kwargs.get("reference_image", "")
        strength = kwargs.get("strength", 0.6)
        return self.generate(prompt, size=size, timeout=timeout, reference_image=ref, strength=strength)
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
            # 本地路径或 /storage/ 路径 → 统一转公网URL
            if reference_image.startswith("/www/wwwroot/") or reference_image.startswith("/storage/"):
                img_input = local_path_to_url(reference_image)
        content = [{"image": img_input}, {"text": prompt}]
        safe_size = size.replace("x","*")
        neg_text = negative or "blurry face, deformed face, different face, face change, plastic surgery, cartoon face, anime face"
        payload = {"model":"wan2.7-image-pro","input":{"messages":[{"role":"user","content":content}]},"parameters":{"size":safe_size,"n":1,"watermark":False,"negative_prompt":neg_text,"strength":strength}}
        headers = {"Authorization":f"Bearer {self.api_key}","Content-Type":"application/json"}
        resp = requests.post(f"{self.base_url}/api/v1/services/aigc/multimodal-generation/generation",json=payload,headers=headers,timeout=50)
        if resp.status_code!=200: raise Exception(f"API error {resp.status_code}: {resp.text[:200]}")
        j = resp.json()
        choices = j.get("output",{}).get("choices",[])
        if choices:
            c = choices[0].get("message",{}).get("content",[])
            urls = [i["image"] for i in c if i.get("type")=="image" and i.get("image")]
            if urls: return urls
        raise Exception(f"No image in response: {str(j.get('output',{}))[:300]}")

bailian_wanxiang_chat = BailianWanxiangChatProvider()
