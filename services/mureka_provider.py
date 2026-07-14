"""Mureka(昆仑万维)AI 音乐生成 Provider

API 文档: https://platform.mureka.cn/docs/
端点:
  POST /v1/song/generate  — 提交生成任务，返回 task id
  GET  /v1/song/query/{task_id} — 轮询任务状态，成功后返回音频 URL

认证: Bearer token
异步: 提交后轮询，状态 preparing→queued→running→streaming→reviewing→succeeded
"""
import logging, time, json, requests

logger = logging.getLogger(__name__)

BASE_URL = "https://api.mureka.cn"
# API key 从 config/api_keys.json 的 mureka 项读取；若不存在则用环境变量
_API_KEY = ""

def _get_key():
    global _API_KEY
    if _API_KEY:
        return _API_KEY
    try:
        with open("/www/wwwroot/api.mzsh.top/config/api_keys.json", "r", encoding="utf-8") as f:
            keys = json.load(f)
        _API_KEY = keys.get("mureka", {}).get("key", "")
    except Exception:
        pass
    if not _API_KEY:
        import os
        _API_KEY = os.environ.get("MUREKA_API_KEY", "")
    return _API_KEY


class MurekaProvider:
    """Mureka AI 音乐生成"""

    def __init__(self):
        self.api_key = _get_key()
        logger.info("MurekaProvider ready (key=%s)", "set" if self.api_key else "EMPTY")

    def generate_song(self, lyrics: str, prompt: str = "", model: str = "auto",
                      gender: str = "", n: int = 1, max_wait: int = 300) -> dict:
        """生成歌曲。返回 {success, url, audio_url, status, task_id, error}

        lyrics: 歌词(必填,可用 [主歌][副歌] 标签分段)
        prompt: 音乐风格提示(如 "r&b, slow, passionate, male vocal")
        model: auto/mureka-7.6/mureka-o2/mureka-8/mureka-9
        gender: female/male(人声性别倾向)
        n: 生成数量(1-3)
        max_wait: 最大等待秒数
        """
        if not self.api_key:
            return {"success": False, "error": "Mureka API key 未配置"}
        if not lyrics or not lyrics.strip():
            return {"success": False, "error": "歌词不能为空"}

        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"lyrics": lyrics[:3000], "model": model, "n": n}
        if prompt:
            payload["prompt"] = prompt[:1024]
        if gender in ("female", "male"):
            payload["gender"] = gender

        # 1. 提交任务
        try:
            r = requests.post(f"{BASE_URL}/v1/song/generate", headers=headers, json=payload, timeout=30)
            if r.status_code != 200:
                return {"success": False, "error": f"提交失败 {r.status_code}: {r.text[:200]}"}
            data = r.json()
            task_id = data.get("id", "")
            if not task_id:
                return {"success": False, "error": f"无 task_id: {r.text[:200]}"}
            logger.info(f"[Mureka] 提交成功 task_id={task_id} model={data.get('model','?')}")
        except Exception as e:
            return {"success": False, "error": f"提交异常: {str(e)[:150]}"}

        # 2. 轮询结果
        poll_url = f"{BASE_URL}/v1/song/query/{task_id}"
        deadline = time.time() + max_wait
        poll_interval = 8  # 音乐生成较慢，8秒轮询一次
        while time.time() < deadline:
            try:
                time.sleep(poll_interval)
                pr = requests.get(poll_url, headers=headers, timeout=15)
                if pr.status_code != 200:
                    continue
                pdata = pr.json()
                status = pdata.get("status", "")
                logger.info(f"[Mureka] 轮询 task={task_id} status={status}")

                if status == "succeeded":
                    choices = pdata.get("choices", [])
                    if choices:
                        ch = choices[0]
                        audio_url = ch.get("audio_url") or ch.get("url", "")
                        if audio_url:
                            return {
                                "success": True,
                                "url": audio_url,
                                "audio_url": audio_url,
                                "status": "succeeded",
                                "task_id": task_id,
                                "model": pdata.get("model", model),
                                "title": ch.get("title", ""),
                                "duration": ch.get("duration", 0),
                            }
                    return {"success": False, "error": "succeeded 但无音频URL", "task_id": task_id}

                elif status in ("failed", "timeouted", "cancelled"):
                    reason = pdata.get("failed_reason", status)
                    return {"success": False, "error": f"任务{status}: {reason}", "task_id": task_id}

                # preparing/queued/running/streaming/reviewing → 继续等
            except Exception as e:
                logger.warning(f"[Mureka] 轮询异常: {e}")
                continue

        return {"success": False, "error": f"轮询超时({max_wait}s)", "task_id": task_id}

    def generate_instrumental(self, prompt: str, model: str = "auto",
                              gender: str = "", max_wait: int = 300) -> dict:
        """生成纯音乐(BGM 用)。用一段中性歌词 + instrumental 风格提示。
        Mureka 无直接 instrumental 参数,用 prompt 指定纯音乐风格 + 简短歌词。"""
        # BGM 用简短哼唱式歌词,避免人声歌词干扰背景音乐
        bgm_lyrics = "[主歌]\n嗯 啊 啊\n[副歌]\n啦 啦 啦"
        bgm_prompt = f"instrumental, background music, {prompt}" if prompt else "instrumental, background music, soft, ambient"
        return self.generate_song(
            lyrics=bgm_lyrics, prompt=bgm_prompt, model=model,
            gender=gender, n=1, max_wait=max_wait
        )


# 模块级单例
mureka = MurekaProvider()
