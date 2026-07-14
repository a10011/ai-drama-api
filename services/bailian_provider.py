import subprocess, json, time, logging, os

logger = logging.getLogger(__name__)

class VideoTaskTimeout(Exception):
    def __init__(self, msg, task_id):
        super().__init__(msg)
        self.task_id = task_id



def _safe_json(stdout, label="bailian"):
    """Safe JSON parse with retry on empty response"""
    log = logging.getLogger(__name__)
    for attempt in range(3):
        if stdout and stdout.strip():
            try:
                return json.loads(stdout)
            except json.JSONDecodeError:
                log.warning(f"[{label}] JSON parse failed attempt {attempt+1}: {stdout[:200]}")
                time.sleep(2)
        else:
            log.warning(f"[{label}] Empty response attempt {attempt+1}")
            time.sleep(3)
    log.error(f"[{label}] All 3 attempts failed, returning empty dict")
    return {}

class BailianVideoProvider:
    def __init__(self):
        from services.ai_providers import _get_key
        self.api_key = _get_key("aliyun_bailian")
        self.base_url = "https://dashscope.aliyuncs.com"

    def _curl(self, method, url, headers, body=None):
        """Use subprocess curl for reliable HTTP (avoids httpx hang)"""
        cmd = ["curl", "-s", "--max-time", "60", "-X", method, url]
        for h in headers:
            cmd += ["-H", h]
        if body:
            cmd += ["-d", body]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=65)
        if result.returncode != 0:
            raise Exception(f"curl failed: {result.stderr[:200]}")
        return result.stdout

    def generate_video(self, prompt: str, image_url: str = "", audio_url: str = "", duration: int = 5,
                       model: str = "happyhorse-1.0-t2v", max_wait: int = 300,
                       resolution: str = "720P") -> str:
        """统一接口: 异步提交+轮询, 返回视频下载URL"""
        if not self.api_key:
            raise Exception("Bailian API key not configured")

        media = []
        if image_url:
            media.append({"type": "first_frame", "url": image_url})
        if audio_url:
            media.append({"type": "driving_audio", "url": audio_url})
        input_data = {"prompt": prompt, "media": media} if media else {"prompt": prompt}
        payload = {
            "model": model,
            "input": input_data,
            "parameters": {
            "duration": duration,
            "resolution": resolution,
            "ratio": "9:16",
            "prompt_extend": True,
            "watermark": False
        }
        }

        headers = [
            f"Authorization: Bearer {self.api_key}",
            "Content-Type: application/json",
            "X-DashScope-Async: enable"
        ]
        body = json.dumps(payload, ensure_ascii=False)
        submit_url = f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis"

        prompt = self._truncate_prompt(prompt, 800) if hasattr(self, '_truncate_prompt') else prompt[:800]
        logger.info(f"Bailian submit: {model} prompt_len={len(prompt)} prompt={prompt[:60]}")
        stdout = self._curl("POST", submit_url, headers, body)
        resp = _safe_json(stdout, "bailian")
        task_id = resp.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"Bailian no task_id: {stdout[:200]}")

        logger.info(f"Bailian task_id={task_id}, polling (max {max_wait}s)...")
        base_delay, max_delay, total = 5, 60, 0
        while total < max_wait:
            delay = min(base_delay * (1.5 ** (total // 30)), max_delay)
            time.sleep(delay)
            total += delay
            try:
                poll_url = f"{self.base_url}/api/v1/tasks/{task_id}"
                poll_headers = [f"Authorization: Bearer {self.api_key}"]
                stdout2 = self._curl("GET", poll_url, poll_headers)
                data = _safe_json(stdout2, "bailian_poll")
                output = data.get("output", {})
                status = output.get("task_status", "")
                if status == "SUCCEEDED":
                    video_url = output.get("video_url", output.get("url", ""))
                    if video_url:
                        logger.info(f"Bailian succeeded ({total}s): {video_url[:60]}")
                        return video_url
                elif status in ("FAILED", "CANCELED"):
                    msg = output.get("message", "") or status
                    raise Exception(f"Bailian task {status}: {msg}")
                if total % 20 < delay:
                    logger.info(f"Bailian poll {total}s: {status}")
            except Exception as e:
                if "FAILED" in str(e) or "CANCELED" in str(e):
                    raise
                logger.warning(f"Bailian poll retry ({total}s): {e}")
                continue
        raise Exception(f"Bailian poll timeout ({max_wait}s): task_id={task_id}")


    @staticmethod
    def _truncate_prompt(prompt: str, max_chars: int = 800) -> str:
        """截断 prompt 到安全长度，保留最核心的内容"""
        if len(prompt) <= max_chars:
            return prompt
        # 按优先级保留：director_shot > 场景描述 > 其他
        # 找到关键标记，按优先级拼接
        parts = {"director": "", "scene": "", "camera": "", "other": ""}
        for section in prompt.split(" | "):
            sec_lower = section.lower()
            if "director shot" in sec_lower or "机位单" in section:
                parts["director"] = section
            elif "scene:" in sec_lower or "atmosphere:" in sec_lower:
                parts["scene"] = section
            elif "camera:" in sec_lower:
                parts["camera"] = section
            else:
                parts["other"] += section + " | "
        result = parts["director"]
        remaining = max_chars - len(result) - 10
        if remaining > 0 and parts["scene"]:
            result += " | " + parts["scene"][:remaining]
            remaining = max_chars - len(result) - 10
        if remaining > 0 and parts["camera"]:
            result += " | " + parts["camera"][:remaining]
            remaining = max_chars - len(result) - 10
        if remaining > 0 and parts["other"]:
            result += " | " + parts["other"][:remaining]
        return result[:max_chars]

    def generate_r2v(self, prompt: str, reference_images: list, duration: int = 5,
                     resolution: str = "720P", ratio: str = "9:16", max_wait: int = 300) -> str:
        """HappyHorse reference-to-video: 1-9张参考图+文字→锁脸视频"""
        if not self.api_key:
            raise Exception("Bailian API key not configured")

        media = [{"type": "reference_image", "url": url} for url in reference_images]
        payload = {
            "model": "happyhorse-1.1-r2v",
            "input": {"prompt": prompt, "media": media},
            "parameters": {
                "duration": duration,
                "resolution": resolution,
                "ratio": ratio,
                "watermark": False
            }
        }
        headers = [
            f"Authorization: Bearer {self.api_key}",
            "Content-Type: application/json",
            "X-DashScope-Async: enable"
        ]
        body = json.dumps(payload, ensure_ascii=False)
        submit_url = f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis"

        prompt = self._truncate_prompt(prompt, 800)
        logger.info(f"HappyHorse R2V submit: {len(reference_images)} refs, prompt_len={len(prompt)} prompt={prompt[:60]}")
        stdout = self._curl("POST", submit_url, headers, body)
        resp = _safe_json(stdout, "bailian")
        task_id = resp.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"HappyHorse R2V no task_id: {stdout[:200]}")
        return self._poll_task(task_id, max_wait, "HappyHorse R2V")

    def generate_video_edit(self, video_url: str, prompt: str, reference_images: list = None,
                            resolution: str = "720P", max_wait: int = 300) -> str:
        """HappyHorse video edit: 输入视频+参考图+文字→编辑视频"""
        if not self.api_key:
            raise Exception("Bailian API key not configured")

        media = [{"type": "video", "url": video_url}]
        if reference_images:
            for url in reference_images:
                media.append({"type": "reference_image", "url": url})

        payload = {
            "model": "happyhorse-1.0-video-edit",
            "input": {"prompt": prompt, "media": media},
            "parameters": {"resolution": resolution, "watermark": False}
        }
        headers = [
            f"Authorization: Bearer {self.api_key}",
            "Content-Type: application/json",
            "X-DashScope-Async: enable"
        ]
        body = json.dumps(payload, ensure_ascii=False)
        submit_url = f"{self.base_url}/api/v1/services/aigc/video-generation/video-synthesis"

        logger.info(f"HappyHorse VideoEdit submit: video={video_url[:40]} prompt={prompt[:60]}")
        stdout = self._curl("POST", submit_url, headers, body)
        resp = _safe_json(stdout, "bailian")
        task_id = resp.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"HappyHorse VideoEdit no task_id: {stdout[:200]}")
        return self._poll_task(task_id, max_wait, "HappyHorse VideoEdit")

    def _poll_task(self, task_id: str, max_wait: int, label: str) -> str:
        """轮询异步任务直到完成，返回视频URL"""
        base_delay, max_delay, total = 5, 30, 0
        while total < max_wait:
            delay = min(base_delay * (1.5 ** (total // 30)), max_delay)
            time.sleep(delay)
            total += delay
            try:
                poll_url = f"{self.base_url}/api/v1/tasks/{task_id}"
                poll_headers = [f"Authorization: Bearer {self.api_key}"]
                stdout = self._curl("GET", poll_url, poll_headers)
                data = _safe_json(stdout, "bailian_poll")
                output = data.get("output", {})
                status = output.get("task_status", "")
                if status == "SUCCEEDED":
                    video_url = output.get("video_url", output.get("url", ""))
                    if video_url:
                        logger.info(f"{label} succeeded ({total}s): {video_url[:60]}")
                        return video_url
                elif status in ("FAILED", "CANCELED"):
                    msg = output.get("message", "") or status
                    raise Exception(f"{label} {status}: {msg}")
                if total % 15 < delay:
                    logger.info(f"{label} poll {total}s: {status}")
            except Exception as e:
                if "FAILED" in str(e) or "CANCELED" in str(e):
                    raise
                logger.warning(f"{label} poll retry ({total}s): {e}")
                continue
        # 超时不丢弃 task_id：任务在阿里云还在跑（已扣费），返回给上层记住继续轮询
        raise VideoTaskTimeout(f"{label} poll timeout ({max_wait}s): task_id={task_id}", task_id)

    def generate_liveportrait(self, image_url: str, audio_url: str, max_wait: int = 300) -> str:
        """LivePortrait — 锁脸对口型。传角色立绘+TTS音频 → 说话视频"""
        payload = {
            "model": "liveportrait",
            "input": {"image_url": image_url, "audio_url": audio_url},
            "parameters": {}
        }
        headers = [
            f"Authorization: Bearer {self.api_key}",
            "Content-Type: application/json",
            "X-DashScope-Async: enable"
        ]
        body = json.dumps(payload, ensure_ascii=False)
        submit_url = f"{self.base_url}/api/v1/services/aigc/image2video/video-synthesis/"

        logger.info(f"LivePortrait submit: image={image_url[:50]} audio={audio_url[:50]}")
        stdout = self._curl("POST", submit_url, headers, body)
        resp = _safe_json(stdout, "bailian")
        task_id = resp.get("output", {}).get("task_id", "")
        if not task_id:
            raise Exception(f"LivePortrait no task_id: {stdout[:200]}")

        logger.info(f"LivePortrait task_id={task_id}, polling (max {max_wait}s)...")
        base_delay, max_delay, total = 5, 60, 0
        while total < max_wait:
            delay = min(base_delay * (1.5 ** (total // 30)), max_delay)
            time.sleep(delay)
            total += delay
            try:
                poll_url = f"{self.base_url}/api/v1/tasks/{task_id}"
                poll_headers = [f"Authorization: Bearer {self.api_key}"]
                stdout2 = self._curl("GET", poll_url, poll_headers)
                data = _safe_json(stdout2, "bailian_poll")
                output = data.get("output", {})
                status = output.get("task_status", "")
                if status == "SUCCEEDED":
                    video_url = output.get("video_url", output.get("url", ""))
                    if video_url:
                        logger.info(f"LivePortrait succeeded ({total}s): {video_url[:60]}")
                        return video_url
                elif status in ("FAILED", "CANCELED"):
                    msg = output.get("message", "") or status
                    raise Exception(f"LivePortrait {status}: {msg}")
                if total % 20 < delay:
                    logger.info(f"LivePortrait poll {total}s: {status}")
            except Exception as e:
                if "FAILED" in str(e) or "CANCELED" in str(e):
                    raise
                logger.warning(f"LivePortrait poll retry ({total}s): {e}")
                continue
        raise Exception(f"LivePortrait poll timeout ({max_wait}s): task_id={task_id}")