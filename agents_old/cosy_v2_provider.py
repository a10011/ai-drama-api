"""
CosyVoice V2 Provider — 完整 instruction 控制 + 正确 WebSocket 协议
"""
import json, uuid, base64, os, asyncio
import aiohttp
import logging
from services.usage_tracker import log_usage

logger = logging.getLogger(__name__)

def _get_api_key():
    with open('/www/wwwroot/api.mzsh.top/config/api_keys.json') as f:
        return json.load(f)['aliyun_bailian']['key']

async def _synthesize_v2(text: str, voice: str, instruction: str = None,
                          format: str = "wav", sample_rate: int = 24000,
                          timeout: int = 120) -> bytes:
    """
    通过 WebSocket 调用 CosyVoice V2，直接操控 WebSocket 协议（复用旧 SDK 认证方式）
    返回原始 WAV 音频数据 bytes
    """
    api_key = _get_api_key()
    ws_url = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    task_id = uuid.uuid4().hex
    
    headers = {
        "Authorization": f"bearer {api_key}",
        "user-agent": "dashscope/1.25"
    }
    
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout)
    ) as session:
        async with session.ws_connect(
            ws_url, headers=headers, heartbeat=6000,
            autoclose=False, autoping=True
        ) as ws:
            # Step 1: Send start-task
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
                    "input": {},
                    "parameters": {
                        "voice": voice,
                        "format": format,
                        "sample_rate": sample_rate
                    }
                }
            }
            await ws.send_json(start_msg)
            
            # Step 2: Wait for task-started
            task_started = False
            while not task_started:
                msg = await ws.receive(timeout=30)
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    event = data.get("header", {}).get("event", "")
                    if event == "task-started":
                        task_started = True
                elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    raise Exception(f"WS error during task start: {ws.exception()}")
            
            # Step 3: Send continue-task with actual input
            continue_msg = {
                "header": {
                    "action": "continue-task",
                    "task_id": task_id
                },
                "payload": {
                    "input": {
                        "text": text
                    }
                }
            }
            if instruction:
                continue_msg["payload"]["input"]["instruction"] = instruction
            
            await ws.send_json(continue_msg)
            
            # Step 4: Receive streaming results
            audio_data = bytearray()
            task_failed = None
            
            while True:
                msg = await ws.receive(timeout=timeout)
                
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    event = data.get("header", {}).get("event", "")
                    status = data.get("header", {}).get("status", "")
                    
                    # Check for errors in header
                    if data.get("header", {}).get("code"):
                        task_failed = data.get("header", {}).get("message", "Unknown error")
                        break
                    
                    payload = data.get("payload", {})
                    
                    # Extract audio data
                    if "audio" in payload:
                        audio_bytes = base64.b64decode(payload["audio"])
                        audio_data.extend(audio_bytes)
                    
                    # Check status
                    if status == "succeeded":
                        break
                    elif status == "failed":
                        err = payload.get("message", payload.get("error", "Unknown error"))
                        task_failed = err
                        break
                    
                elif msg.type == aiohttp.WSMsgType.BINARY:
                    # Direct binary audio data
                    audio_data.extend(msg.data)
                        
                elif msg.type == aiohttp.WSMsgType.ERROR:
                    task_failed = str(ws.exception())
                    break
                elif msg.type == aiohttp.WSMsgType.CLOSED:
                    break
            
            # Step 5: Send finished
            try:
                finish_msg = {
                    "header": {
                        "action": "finished",
                        "task_id": task_id
                    },
                    "payload": {}
                }
                await ws.send_json(finish_msg)
            except:
                pass
            
            await ws.close()
            
            if task_failed:
                raise Exception(f"CosyVoice V2 failed: {task_failed}")
            
            if len(audio_data) < 100:
                raise Exception(f"CosyVoice V2 returned no audio ({len(audio_data)} bytes)")
            
            return bytes(audio_data)


class CosyVoiceV2Provider:
    """阿里云百炼 — CosyVoice V2 (instruction 控制)"""
    
    def __init__(self):
        self.api_key = _get_api_key()
        logger.info("CosyVoiceV2Provider ready (instruction support)")
    
    def synthesize(self, text: str, voice: str = "longxiaochun_v2", 
                   speed: float = 1.0, format: str = "wav",
                   sample_rate: int = 24000,
                   instruction: str = None,
                   timeout: int = 120) -> dict:
        """
        完整 instruction 控制语音合成。
        
        Args:
            text: 台词文本（干净无控制描述）
            voice: 音色名（v2 版本，如 longxiaochun_v2）
            instruction: 自然语言控制指令（人设+情绪+语速+场景）
            
        Returns:
            {"audio_file": 本地路径, "audio_data": base64, "model": "cosyvoice-v2"}
        """
        import dashscope
        dashscope.api_key = self.api_key
        
        # 语言速通过 instruction 控制，但也可以通过参数微调语速
        # (instruction 控制整体语感，speed 调节基础语速)
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_data = loop.run_until_complete(
                _synthesize_v2(
                    text=text,
                    voice=voice,
                    instruction=instruction,
                    format=format,
                    sample_rate=sample_rate,
                    timeout=timeout
                )
            )
        finally:
            loop.close()
        
        # 落盘
        os.makedirs("/www/wwwroot/storage/audio", exist_ok=True)
        out_path = f"/www/wwwroot/storage/audio/cosy_v2_{uuid.uuid4().hex[:12]}.wav"
        with open(out_path, "wb") as f:
            f.write(audio_data)
        
        log_usage("cosyvoice-v2")
        
        return {
            "audio_file": out_path,
            "audio_data": base64.b64encode(audio_data).decode(),
            "model": "cosyvoice-v2"
        }


if __name__ == "__main__":
    # Quick test
    provider = CosyVoiceV2Provider()
    
    instructions = {
        "basic": None,
        "general": "中年沙场将领，浑厚胸腔低音，嗓音带风沙干涩气声；禁止尖细、奶油少年音、娘娘腔；语速偏慢厚重，句间轻微停顿，气息收束克制",
        "full": "中年沙场将领，浑厚胸腔低音，嗓音带风沙干涩气声；禁止尖细、奶油少年音、娘娘腔、轻浮细嗓；语速偏慢厚重，句间轻微停顿，气息收束克制；与牵挂知己对话，心绪藏淡淡怅然，语调平稳无哭腔，尾音轻轻放缓"
    }
    
    text = "边关战事吃紧，此番我独自奔赴前线，前路关山万里，归期尚且难料。"
    
    for name, inst in instructions.items():
        label = f"instruction={name}"
        if inst:
            label += f" ({len(inst)} chars)"
        print(f"\n=== {label} ===")
        result = provider.synthesize(
            text=text,
            voice="longxiaochun_v2",
            instruction=inst
        )
        print(f"  File: {result['audio_file']}")
        print(f"  Size: {len(result['audio_data'])} bytes (b64)")
        print(f"  OK!")
