"""
CosyVoice V2 Provider — 完整 WebSocket 协议 + instruction 控制
"""
import asyncio, json, uuid, base64, os, logging, time
import aiohttp
from services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

_WS_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
_STORAGE_DIR = "/www/wwwroot/storage/audio"

def _get_api_key():
    with open('/www/wwwroot/api.mzsh.top/config/api_keys.json') as f:
        return json.load(f)['aliyun_bailian']['key']


async def _synthesize_v2(text: str, voice: str, instruction: str = None,
                          timeout: int = 120) -> bytes:
    """
    通过 WebSocket 调用 CosyVoice V2。
    协议：run-task（含 text/instruction）→ task-started → 多轮 result-generated
    （含 binary audio chunks）→ task-finished
    返回完整 WAV 音频数据 bytes。
    """
    api_key = _get_api_key()
    task_id = uuid.uuid4().hex
    headers = {"Authorization": f"bearer {api_key}", "user-agent": "dashscope/1.25"}
    audio_chunks = []

    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        async with session.ws_connect(
            _WS_URL, headers=headers, heartbeat=6000,
            autoclose=False, autoping=True
        ) as ws:
            # === Step 1: Send run-task (text in input) ===
            inp = {"text": text}
            if instruction:
                inp["instruction"] = instruction

            start_msg = {
                "header": {
                    "action": "run-task",
                    "task_id": task_id,
                    "streaming": "out"
                },
                "payload": {
                    "model": "cosyvoice-v2",
                    "task_group": "audio",
                    "task": "tts",
                    "function": "SpeechSynthesizer",
                    "input": inp,
                    "parameters": {
                        "voice": voice,
                        "format": "wav",
                        "sample_rate": 24000
                    }
                }
            }
            await ws.send_json(start_msg)

            # === Step 2: Read results (text + binary audio) ===
            task_failed = None
            done = False

            while not done:
                try:
                    msg = await asyncio.wait_for(ws.receive(), timeout=15)
                except asyncio.TimeoutError:
                    if audio_chunks:
                        break  # have audio, call it good
                    raise TimeoutError("CosyVoice V2: no response in 15s")

                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    event = data.get("header", {}).get("event", "")
                    status = data.get("header", {}).get("status", "")

                    # Check errors
                    err_code = data.get("header", {}).get("error_code", "")
                    if err_code:
                        task_failed = f"{err_code}: {data['header'].get('error_message', '')}"
                        break

                    status = data.get("header", {}).get("status", "")
                    if status == "failed":
                        task_failed = str(data.get("payload", {}))
                        break

                elif msg.type == aiohttp.WSMsgType.BINARY:
                    audio_chunks.append(msg.data)

                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    task_failed = str(ws.exception())
                    break

                # Check task-finished event
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("header", {}).get("event") == "task-finished":
                        done = True

            await ws.close()

    if task_failed:
        raise Exception(f"CosyVoice V2 error: {task_failed}")

    audio_data = b"".join(audio_chunks)
    if len(audio_data) < 100:
        raise Exception(f"CosyVoice V2 returned no audio ({len(audio_data)} bytes)")

    return audio_data


class CosyVoiceV2Provider:
    """阿里云百炼 — CosyVoice V2 with instruction control"""

    def __init__(self):
        logger.info("[CosyVoiceV2] Ready (instruction control via WebSocket)")

    def synthesize(self, text: str,
                   voice: str = "longxiaochun_v2",
                   instruction: str = None,
                   timeout: int = 120) -> dict:
        """
        完整 instruction 控制语音合成。

        Args:
            text: 台词文本（干净，不含控制描述）
            voice: CosyVoice V2 音色（如 longxiaochun_v2）
            instruction: 自然语言控制指令（人设 + 情绪 + 语速 + 场景）

        Returns:
            {"audio_file": 路径, "audio_data": base64, "model": "cosyvoice-v2"}
        """
        start = time.time()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_data = loop.run_until_complete(
                _synthesize_v2(
                    text=text,
                    voice=voice,
                    instruction=instruction,
                    timeout=timeout
                )
            )
        finally:
            loop.close()

        elapsed = time.time() - start
        logger.info(f"[CosyVoiceV2] TTS done in {elapsed:.1f}s, audio={len(audio_data)} bytes")

        # 落盘到持久存储
        os.makedirs(_STORAGE_DIR, exist_ok=True)
        out_path = f"{_STORAGE_DIR}/cv2_{uuid.uuid4().hex[:12]}.wav"
        with open(out_path, "wb") as f:
            f.write(audio_data)

        log_usage("cosyvoice-v2")

        return {
            "audio_file": out_path,
            "audio_data": base64.b64encode(audio_data).decode(),
            "model": "cosyvoice-v2",
            "duration_sec": round(len(audio_data) / 48000, 1)
        }
