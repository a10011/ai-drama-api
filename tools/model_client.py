# ============================================================
# 统一 AI 模型调用层 — model_client.py
# 生产级重写：结构化数据、连接复用、速率限制、完善日志
# ============================================================
import logging
import time
import json
import re
import os
import ssl
import hashlib
import urllib.request
import concurrent.futures
from typing import Optional, Dict, Any, List, TypedDict, Union
from dataclasses import dataclass, field
from enum import Enum

from services.retry_manager import enqueue as _retry_enqueue, get_next_interval
from services.validate_input import validate_input
from services.vertical_spec import VERT, framing_prompt

logger = logging.getLogger(__name__)

# ── 结构化数据类型 ──────────────────────────────────────────

class ModelResult(TypedDict, total=False):
    """统一的模型调用返回结构"""
    success: bool
    url: str
    audio_path: str
    model: str
    error: str
    retrying: bool
    retry_id: Optional[str]
    next_retry_at: Optional[float]
    validation: Optional[Dict[str, Any]]

class ModelConfig(TypedDict):
    """模型配置结构"""
    provider: str
    service: str
    model: str
    type: str
    size: Optional[str]
    timeout: int
    static: Optional[bool]

class ProviderEntry(TypedDict):
    """Provider 注册条目"""
    module: Optional[str]
    symbol: Optional[str]
    is_callable: bool

@dataclass
class RetryState:
    """重试状态跟踪"""
    model_name: str
    attempt: int = 0
    last_error: str = ""
    last_error_code: int = 0

# ── 枚举 ──────────────────────────────────────────────────

class ErrorCategory(Enum):
    """错误分类"""
    CLIENT_ERROR = "client_error"
    SERVER_ERROR = "server_error"
    TIMEOUT = "timeout"
    NETWORK = "network"
    EMPTY_RESPONSE = "empty_response"
    UNKNOWN = "unknown"

# ── 模型注册表 ──────────────────────────────────────────────

MODEL_REGISTRY: Dict[str, ModelConfig] = {
    # ── 图片模型 ──
    "seedream": {
        "provider": "ARKImageProvider", "service": "ark_volc",
        "model": "doubao-seedream-4-0-250828",
        "type": "image", "size": "1920x1920", "timeout": 60,
    },
    "hidream": {
        "provider": "HiDreamImageProvider", "service": "hidream",
        "model": "z1-image",
        "type": "image", "size": VERT.FALLBACK_SQUARE, "timeout": 60,
    },
    "agnes": {
        "provider": "AgnesAIProvider", "service": "agnes",
        "model": "agnes-image-2.1-flash",
        "type": "image", "size": VERT.FALLBACK_SQUARE, "timeout": 30,
    },
    "wanxiang": {
        "provider": "TongyiWanxiangProvider", "service": "aliyun_bailian",
        "model": "wanx2.1-t2i-plus",
        "type": "image", "size": VERT.FALLBACK_WANXIANG, "timeout": 60,
    },
    # ── 视频模型 ──
    "kling": {
        "provider": "KlingProvider", "service": "kling",
        "model": "kling-v2-6",
        "type": "video", "timeout": 60,
    },
    "seedance": {
        "provider": "SeedanceProvider", "service": "ark_volc",
        "model": "doubao-seedance-2-0-260128",
        "type": "video", "timeout": 120,
    },
    "wan2.7_t2v": {
        "provider": "wan2.7_t2v", "service": "aliyun_bailian",
        "model": "wan2.7-t2v",
        "type": "video", "timeout": 900,
    },
    "wan2.7_i2v": {
        "provider": "wan2.7_i2v", "service": "aliyun_bailian",
        "model": "wan2.7-i2v-2026-04-25",
        "type": "video", "timeout": 300,
    },
    "happyhorse": {
        "provider": "happyhorse", "service": "happyhorse",
        "model": "happyhorse-1.0-t2v",
        "type": "video", "timeout": 300,
    },
    # ── TTS 模型 ──
    "edge-tts": {
        "provider": "EdgeTTSProvider", "service": "edge",
        "model": "edge-tts",
        "type": "tts", "timeout": 15,
    },
    "cosyvoice": {
        "provider": "cosyvoice", "service": "aliyun_bailian",
        "model": "cosyvoice-v2",
        "type": "tts", "timeout": 20,
    },
    # ── 其他 ──
    "local_bgm": {
        "provider": "local", "service": "local",
        "model": "local_bgm",
        "type": "bgm", "timeout": 5, "static": True,
    },
    "music_api": {
        "provider": "music_api", "service": "music",
        "model": "bgm-generator",
        "type": "bgm", "timeout": 30,
    },
}

# ── 路由链 ──────────────────────────────────────────

# ── 生态链（阿里系/火山系 各自完整的模型搭配，不混用） ──
ECOSYSTEM_CHAINS: Dict[str, Dict[str, List[str]]] = {
    "volc": {   # 火山引擎 — 豆包系列
        "image": ["seedream"],
        "video": ["seedance", "kling"],  # wan2.7_i2v 对口型优先，有音+图时嘴型匹配
        "tts":   ["edge-tts"],
        "bgm":   ["music_api", "local_bgm"],
    },
    "aliyun": {  # 阿里云 — 百炼系列
        "image": ["wanxiang"],
        "video": ["seedance", "kling"],  # wan2.7_i2v 对口型优先
        "tts":   ["cosyvoice"],
        "bgm":   ["music_api", "local_bgm"],
    },
}

# 当前生效生态链（可在 pipeline 请求中指定 ecosystem 参数切换）
CURRENT_ECOSYSTEM = "volc"

def _get_chain(category: str) -> List[str]:
    """获取当前生态链中指定类别的模型列表"""
    ecosystem = ECOSYSTEM_CHAINS.get(CURRENT_ECOSYSTEM, ECOSYSTEM_CHAINS["volc"])
    return ecosystem.get(category, [])

# 兼容旧代码
ROUTING_CHAINS = ECOSYSTEM_CHAINS[CURRENT_ECOSYSTEM]
# 全类别兜底（供降级使用）
ALL_MODELS: Dict[str, List[str]] = {
    "image": ["seedream", "wanxiang", "hidream"],
    "video": ["seedance", "kling"],  # 对口型模型优先
    "tts":   ["edge-tts", "cosyvoice"],
    "bgm":   ["music_api", "local_bgm"],
}

# ── Provider 调度表（模块级常量，避免重复创建） ──────────

_PROVIDER_DISPATCH: Dict[str, ProviderEntry] = {
    "ARKImageProvider":       {"module": "services.ai_providers", "symbol": "ARKImageProvider", "is_callable": True},
    "TongyiWanxiangProvider": {"module": "services.ai_providers", "symbol": "TongyiWanxiangProvider", "is_callable": True},
    "AgnesAIProvider":        {"module": "services.ai_providers", "symbol": "AgnesAIProvider", "is_callable": True},
    "HiDreamImageProvider":   {"module": "services.ai_providers", "symbol": "HiDreamImageProvider", "is_callable": True},
    "KlingProvider":          {"module": "services.ai_providers", "symbol": "KlingProvider", "is_callable": True},
    "EdgeTTSProvider":        {"module": "services.ai_providers", "symbol": "EdgeTTSProvider", "is_callable": True},
    "SeedanceProvider":       {"module": "services.ai_providers", "symbol": "SeedanceProvider", "is_callable": True},
    "cosyvoice":              {"module": "services.ai_providers", "symbol": "cosyvoice_provider", "is_callable": False},
    "music_api":              {"module": "services.ai_providers", "symbol": "music_api_provider", "is_callable": False},
    "happyhorse":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "wan2.7_t2v":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "wan2.7_i2v":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "local":                  {"module": None, "symbol": None, "is_callable": False},
}

# ── 模型实例缓存（单例） ──────────────────────────────

_provider_instances: Dict[str, Any] = {}

def _get_provider(model_name: str) -> Any:
    """延迟加载 provider 实例，带错误处理"""
    if model_name in _provider_instances:
        return _provider_instances[model_name]
    
    try:
        cfg = MODEL_REGISTRY.get(model_name)
        if not cfg:
            raise ValueError(f"未知模型: {model_name}")
        
        provider_cls = cfg["provider"]
        entry = _PROVIDER_DISPATCH.get(provider_cls)
        if not entry:
            raise ValueError(f"未实现的 Provider: {provider_cls}")
        
        if entry["module"] is None:
            _provider_instances[model_name] = None
            return None
        
        try:
            mod = __import__(entry["module"], fromlist=[entry["symbol"]])
            obj = getattr(mod, entry["symbol"])
            instance = obj() if entry["is_callable"] else obj
            _provider_instances[model_name] = instance
            logger.debug(f"[ModelClient] Provider 加载成功: {model_name} -> {provider_cls}")
            return instance
        except ImportError as e:
            logger.error(f"[ModelClient] 导入 Provider 模块失败: {entry['module']} - {e}")
            raise
        except AttributeError as e:
            logger.error(f"[ModelClient] Provider 符号未找到: {entry['symbol']} - {e}")
            raise
            
    except Exception as e:
        logger.error(f"[ModelClient] 获取 Provider 失败: {model_name} - {e}")
        raise

# ── 错误分类工具函数 ──────────────────────────────────

def _classify_error(error: Exception, error_str: str) -> ErrorCategory:
    """分类错误类型"""
    if isinstance(error, TimeoutError) or "timeout" in error_str.lower():
        return ErrorCategory.TIMEOUT
    if isinstance(error, (ConnectionError, urllib.error.URLError)):
        return ErrorCategory.NETWORK
    code_match = re.search(r'\b(4\d{2}|5\d{2})\b', error_str)
    if code_match:
        code = int(code_match.group(1))
        if 400 <= code < 500:
            return ErrorCategory.CLIENT_ERROR
        elif code >= 500:
            return ErrorCategory.SERVER_ERROR
    return ErrorCategory.UNKNOWN

def _extract_error_code(error_str: str) -> int:
    """从错误字符串提取 HTTP 状态码"""
    code_match = re.search(r'\b(4\d{2}|5\d{2})\b', error_str)
    return int(code_match.group(1)) if code_match else 0

# ── 重试逻辑 ──────────────────────────────────────────

def _should_retry(category: ErrorCategory, attempt: int) -> bool:
    """判断是否应该重试"""
    if attempt >= 1:
        return False
    if category in (ErrorCategory.SERVER_ERROR, ErrorCategory.TIMEOUT, ErrorCategory.NETWORK):
        return True
    return False

def _get_retry_delay(category: ErrorCategory) -> int:
    """获取重试延迟（秒）"""
    if category == ErrorCategory.SERVER_ERROR:
        return 3
    elif category in (ErrorCategory.TIMEOUT, ErrorCategory.NETWORK):
        return 2
    return 1

# ── 图片生成核心逻辑 ──────────────────────────────────

def _generate_image_with_model(
    model_name: str,
    prompt: str,
    size: str,
    timeout: int,
    retry_state: Optional[RetryState] = None
) -> Optional[List[str]]:
    """调用单个图片模型，返回 URL 列表"""
    try:
        provider = _get_provider(model_name)
        if provider is None:
            logger.warning(f"[ModelClient] {model_name} provider 为 None")
            return None
        
        urls: List[str] = []
        
        # 追加竖屏构图提示
        framed_prompt = prompt + "\n\n" + framing_prompt()
        
        if model_name == "seedream":
            urls = provider.generate(
                framed_prompt, size,
                negative="cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic",
                style="photography"
            )
        elif model_name == "wanxiang":
            result = provider.generate(framed_prompt, size)
            if isinstance(result, dict):
                urls = result.get("images", []) or (["使用中"] if result.get("url") else [])
            else:
                urls = result if isinstance(result, list) else []
            if not urls:
                logger.warning(f"[ModelClient] 万相原始返回: {json.dumps(result, ensure_ascii=False)[:300]}")
        elif model_name == "agnes":
            urls = provider.generate(framed_prompt, size, timeout=timeout)
            if not urls:
                logger.warning(f"[ModelClient] Agnes 原始返回: urls={urls!r}")
        elif model_name == "hidream":
            urls = provider.generate(framed_prompt, size)
            if not urls:
                logger.warning(f"[ModelClient] HiDream 原始返回: urls={urls!r}")
        else:
            urls = provider.generate_image(framed_prompt, size)
        
        return urls if urls else []
        
    except Exception as e:
        error_str = str(e)
        category = _classify_error(e, error_str)
        error_code = _extract_error_code(error_str)
        
        if retry_state:
            retry_state.last_error = error_str
            retry_state.last_error_code = error_code
        
        logger.warning(
            f"[ModelClient] {model_name} 调用失败: "
            f"category={category.value}, code={error_code}, "
            f"error={error_str[:150]}"
        )
        
        if _should_retry(category, retry_state.attempt if retry_state else 0):
            delay = _get_retry_delay(category)
            logger.info(f"[ModelClient] {model_name} 将在 {delay}s 后重试")
            time.sleep(delay)
            if retry_state:
                retry_state.attempt += 1
            return _generate_image_with_model(model_name, prompt, size, timeout, retry_state)
        
        return None

# ── 视频生成核心逻辑 ──────────────────────────────────

def _generate_video_with_model(
    model_name: str,
    prompt: str,
    image_url: str,
    audio_url: str,
    timeout: int,
    resolution: str,
    retry_state: Optional[RetryState] = None
) -> Optional[List[str]]:
    """调用单个视频模型，返回 URL 列表"""
    try:
        provider = _get_provider(model_name)
        if provider is None:
            logger.warning(f"[ModelClient] {model_name} provider 为 None")
            return None
        
        urls: List[str] = []
        
        if model_name == "wan2.7_t2v":
            from services.bailian_provider import BailianVideoProvider as BVP
            bp = BVP()
            url = bp.generate_video(
                prompt or "角色自然表演",
                image_url="", audio_url=audio_url or "",
                model="wan2.7-t2v",
                max_wait=timeout or 600,
                resolution=resolution
            )
            urls = [url] if url else []
        elif model_name == "wan2.7_i2v":
            if not image_url and not audio_url:
                logger.warning(f"[ModelClient] wan2.7_i2v 跳过：无图片/音频输入")
                return []
            from services.bailian_provider import BailianVideoProvider as BVP
            bp = BVP()
            # 有配音时强化口型指令，让嘴型动作更明显
            sync_prompt = prompt or "角色自然表演"
            if audio_url:
                sync_prompt += "。角色正在大声说话，嘴巴张大，嘴型动作夸张明显，嘴唇开合清晰可见，表情生动投入"
            url = bp.generate_video(
                sync_prompt,
                image_url, audio_url=audio_url,
                model="wan2.7-i2v-2026-04-25",
                max_wait=timeout or 300,
                resolution=resolution
            )
            urls = [url] if url else []
        elif model_name == "kling":
            urls = provider.generate_video(prompt, image_url, resolution=resolution)
        elif model_name == "seedance":
            result = provider.generate_video(prompt, duration=5, max_wait=timeout or 600)
            video_url = ""
            if isinstance(result, dict):
                data = result.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    video_url = data[0].get("video_url", data[0].get("url", ""))
                elif isinstance(data, dict):
                    video_url = data.get("video_url", data.get("url", ""))
            urls = [video_url] if video_url else []
            if not video_url:
                logger.warning(f"[ModelClient] seedance 返回无视频URL: {json.dumps(result, ensure_ascii=False)[:300]}")
        elif model_name == "happyhorse":
            urls = provider.generate_video(prompt or "角色自然表演", image_url, resolution=resolution)
        else:
            logger.warning(f"[ModelClient] 未知视频模型: {model_name}")
            return []
        
        return urls if urls else []
        
    except Exception as e:
        error_str = str(e)
        category = _classify_error(e, error_str)
        error_code = _extract_error_code(error_str)
        
        if retry_state:
            retry_state.last_error = error_str
            retry_state.last_error_code = error_code
        
        logger.warning(
            f"[ModelClient] {model_name} 视频调用失败: "
            f"category={category.value}, code={error_code}, "
            f"error={error_str[:150]}"
        )
        
        if _should_retry(category, retry_state.attempt if retry_state else 0):
            delay = _get_retry_delay(category)
            logger.info(f"[ModelClient] {model_name} 将在 {delay}s 后重试")
            time.sleep(delay)
            if retry_state:
                retry_state.attempt += 1
            return _generate_video_with_model(model_name, prompt, image_url, audio_url, timeout, resolution, retry_state)
        
        return None

# ── 公共 API 函数 ──────────────────────────────────────────

def generate_image(
    prompt: str,
    preferred: str = None,
    size: str = None,
    model: str = None,
    timeout: int = None,
    pipeline_id: str = ""
) -> ModelResult:
    """图片生成。同模型重试，不降级切换，返回结构化结果"""
    chain = _get_chain("image")[:] or ALL_MODELS["image"][:]
    if preferred and preferred in chain:
        chain = [preferred] + [m for m in chain if m != preferred]
    elif preferred and preferred not in chain:
        chain = [preferred] + chain
    
    last_error = ""
    
    for model_name in chain:
        cfg = MODEL_REGISTRY.get(model_name)
        if not cfg:
            continue
        if cfg.get("static"):
            continue
        
        final_size = size or cfg.get("size", "1024x1024")
        final_timeout = timeout or cfg["timeout"]
        
        try:
            retry_state = RetryState(model_name=model_name)
            urls = _generate_image_with_model(model_name, prompt, final_size, final_timeout, retry_state)
            
            if urls and len(urls) > 0:
                logger.info(
                    f"[ModelClient] ✅ {model_name} 成功 "
                    f"(timeout={final_timeout}s) url={urls[0][:80]}"
                )
                return ModelResult(
                    success=True,
                    url=urls[0],
                    model=model_name,
                    error=""
                )
            
            # Retry same model instead of degrading
            retry_count = 0
            max_same_model_retries = 2
            while retry_count < max_same_model_retries and (not urls or len(urls) == 0):
                retry_count += 1
                logger.warning(f"[ModelClient] ⚠️ {model_name} 返回空 → 同模型重试 ({retry_count}/{max_same_model_retries})")
                import time as _time
                _time.sleep(2 * retry_count)
                urls = _generate_image_with_model(model_name, prompt, final_size, final_timeout, retry_state)
                if urls and len(urls) > 0:
                    logger.info(f"[ModelClient] ✅ {model_name} 重试成功 (attempt {retry_count})")
                    return ModelResult(
                        success=True,
                        url=urls[0],
                        model=model_name,
                        error=""
                    )
            
            logger.warning(
                f"[ModelClient] ⚠️ {model_name} {max_same_model_retries}x 重试仍空 | "
                f"provider={cfg['provider']} | → 切换模型"
            )
            last_error = f"[{model_name}] 视频生成返回空(已重试{max_same_model_retries}次)"
            continue
            
        except Exception as e:
            error_str = str(e)[:100]
            logger.error(f"[ModelClient] ❌ {model_name} 最终失败: {error_str}")
            last_error = f"[{model_name}] 生成失败: {error_str} | 建议: 检查API额度/网络/参数"
            continue
    
    if pipeline_id:
        try:
            call_args = {"prompt": prompt, "size": size, "preferred": preferred}
            validation = validate_input("image", chain[0] if chain else "", call_args)
            logger.info(
                f"[ModelClient] 🔍 输入自查: model={chain[0] if chain else 'unknown'} "
                f"ok={validation['ok']} prompt_len={validation['prompt_len']} "
                f"issues={validation['issues']}"
            )
            
            if not validation["ok"]:
                error_detail = f"内容不合规: {'; '.join(validation['issues'])}"
                logger.error(f"[ModelClient] ❌ {error_detail} | preview: {validation['prompt_preview']}")
                return ModelResult(
                    success=False,
                    url="",
                    model=chain[0] if chain else "",
                    error=error_detail,
                    validation=validation
                )
            
            rid = _retry_enqueue(
                pipeline_id=pipeline_id,
                stage="image",
                model_name=chain[0] if chain else "",
                call_type="image",
                call_args={"prompt": prompt, "size": size, "preferred": preferred}
            )
            logger.info(
                f"[ModelClient] 🔄 内容合格，入队重试 #{rid}: "
                f"pipeline={pipeline_id} 5分钟后重试"
            )
            return ModelResult(
                success=False,
                retrying=True,
                retry_id=rid,
                next_retry_at=time.time() + 300,
                model=chain[0] if chain else "",
                error=last_error
            )
        except Exception as eq:
            logger.error(f"[ModelClient] 入队失败: {eq}")
    
    return ModelResult(
        success=False,
        url="",
        model="",
        error=last_error
    )


def generate_video(
    prompt: str,
    image_url: str = "",
    audio_url: str = "",
    preferred: str = None,
    timeout: int = None,
    resolution: str = "720P",
    pipeline_id: str = ""
) -> ModelResult:
    """视频生成。同模型重试，不降级切换，返回结构化结果"""
    chain = _get_chain("video")[:] or ALL_MODELS["video"][:]
    if preferred and preferred in chain:
        chain = [preferred] + [m for m in chain if m != preferred]
    elif preferred and preferred not in chain:
        chain = [preferred] + chain
    
    last_error = ""
    
    for model_name in chain:
        cfg = MODEL_REGISTRY.get(model_name)
        if not cfg or cfg.get("static"):
            continue
        
        final_timeout = timeout or cfg["timeout"]
        
        try:
            retry_state = RetryState(model_name=model_name)
            urls = _generate_video_with_model(
                model_name, prompt, image_url, audio_url,
                final_timeout, resolution, retry_state
            )
            
            if urls and len(urls) > 0:
                logger.info(
                    f"[ModelClient] ✅ {model_name} 视频成功 "
                    f"url={urls[0][:80]}"
                )
                return ModelResult(
                    success=True,
                    url=urls[0],
                    model=model_name,
                    error=""
                )
            
            # Retry same model instead of degrading
            retry_count = 0
            max_same_model_retries = 3
            while retry_count < max_same_model_retries and (not urls or len(urls) == 0):
                retry_count += 1
                logger.warning(f"[ModelClient] ⚠️ {model_name} 视频返回空 → 同模型重试 ({retry_count}/{max_same_model_retries})")
                import time as _time
                _time.sleep(2 * retry_count)  # progressive backoff
                urls = _generate_video_with_model(
                    model_name, prompt, image_url, audio_url,
                    final_timeout, resolution, retry_state
                )
                if urls and len(urls) > 0:
                    logger.info(f"[ModelClient] ✅ {model_name} 视频重试成功 (attempt {retry_count})")
                    return ModelResult(
                        success=True,
                        url=urls[0],
                        model=model_name,
                        error=""
                    )
            
            logger.warning(f"[ModelClient] ⚠️ {model_name} 视频 {max_same_model_retries}x 重试仍空 → 切换模型")
            last_error = f"[{model_name}] 视频生成返回空(已重试{max_same_model_retries}次)"
            continue
            
        except Exception as e:
            error_str = str(e)[:100]
            logger.error(f"[ModelClient] ❌ {model_name} 视频最终失败: {error_str}")
            last_error = f"[{model_name}] 生成失败: {error_str} | 建议: 检查API额度/网络/参数"
            continue
    
    if pipeline_id:
        try:
            call_args = {
                "prompt": prompt,
                "image_url": image_url,
                "audio_url": audio_url,
                "resolution": resolution
            }
            validation = validate_input("video", chain[0] if chain else "", call_args)
            logger.info(
                f"[ModelClient] 🔍 视频输入自查: model={chain[0] if chain else 'unknown'} "
                f"ok={validation['ok']} prompt_len={validation['prompt_len']} "
                f"issues={validation['issues']}"
            )
            
            if not validation["ok"]:
                error_detail = f"视频内容不合规: {'; '.join(validation['issues'])}"
                logger.error(f"[ModelClient] ❌ {error_detail} | preview: {validation['prompt_preview']}")
                return ModelResult(
                    success=False,
                    url="",
                    model=chain[0] if chain else "",
                    error=error_detail,
                    validation=validation
                )
            
            rid = _retry_enqueue(
                pipeline_id=pipeline_id,
                stage="video",
                model_name=chain[0] if chain else "",
                call_type="video",
                call_args=call_args
            )
            logger.info(
                f"[ModelClient] 🔄 视频内容合格，入队重试 #{rid}: "
                f"pipeline={pipeline_id} 5分钟后重试"
            )
            return ModelResult(
                success=False,
                retrying=True,
                retry_id=rid,
                next_retry_at=time.time() + 300,
                model=chain[0] if chain else "",
                error=last_error
            )
        except Exception as eq:
            logger.error(f"[ModelClient] 视频入队失败: {eq}")
    
    return ModelResult(
        success=False,
        url="",
        model="",
        error=last_error
    )


def generate_tts(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    speed: float = 1.0,
    timeout: int = None
) -> ModelResult:
    """TTS 生成。返回结构化结果"""
    chain = _get_chain("tts")[:] or ALL_MODELS["tts"][:]
    last_error = ""
    
    for model_name in chain:
        cfg = MODEL_REGISTRY.get(model_name)
        if not cfg or cfg.get("static"):
            continue
        
        final_timeout = timeout or cfg["timeout"]
        
        try:
            provider = _get_provider(model_name)
            if provider is None:
                continue
            
            if model_name == "edge-tts":
                data = provider.generate_tts(text, voice, timeout=final_timeout)
            else:
                data = provider.generate_tts(text, voice, speed)
            
            if data and data.get("audio_file"):
                logger.info(
                    f"[ModelClient] ✅ {model_name} TTS成功 "
                    f"audio_path={data['audio_file'][:80]}"
                )
                return ModelResult(
                    success=True,
                    audio_path=data["audio_file"],
                    model=model_name,
                    error=""
                )
            
            last_error = f"{model_name} 无返回"
            
        except Exception as e:
            error_str = str(e)[:80]
            logger.warning(f"[ModelClient] {model_name} TTS失败: {error_str}")
            last_error = f"[{model_name}] 生成失败: {error_str} | 建议: 检查API额度/网络/参数"
            continue
    
    return ModelResult(
        success=False,
        audio_path="",
        model="",
        error=last_error
    )


def call_llm(
    prompt: str,
    system: str = "",
    model: str = "deepseek-chat",
    timeout: int = 60,
    max_tokens: int = 4096
) -> ModelResult:
    """LLM 调用统一入口"""
    try:
        is_claude = model.startswith("claude")
        is_openrouter = "/" in model and not is_claude
        
        if is_claude or model.startswith("gpt") or model.startswith("api2d"):
            from services.ai_providers import API2DProvider
            provider = API2DProvider(model=model)
        elif is_openrouter:
            from services.ai_providers import OpenRouterProvider
            provider = OpenRouterProvider(model=model)
        else:
            from services.ai_providers import DeepSeekProvider
            provider = DeepSeekProvider(model=model)
        
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt}
        ]
        
        resp = provider.chat(msgs, max_tokens=max_tokens, timeout=timeout)
        text = resp.get("content", resp.get("text", str(resp))) if isinstance(resp, dict) else str(resp)
        
        logger.info(f"[ModelClient] ✅ LLM成功: model={model}, response_len={len(text)}")
        return ModelResult(
            success=True,
            text=text,
            model=model,
            error=""
        )
        
    except Exception as e:
        error_str = str(e)[:200]
        logger.error(f"[ModelClient] ❌ LLM失败: model={model}, error={error_str}")
        return ModelResult(
            success=False,
            text="",
            model=model,
            error=error_str
        )


def generate_scene_images(
    prompts: List[str],
    preferred: str = None,
    size: str = None
) -> Dict[str, ModelResult]:
    """批量场景图生成（并行）"""
    results: Dict[str, ModelResult] = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {}
        for i, p in enumerate(prompts):
            fut = pool.submit(generate_image, p, preferred=preferred, size=size)
            fut_map[fut] = i
        
        batch_timeout = max(120, len(prompts) * 90)
        try:
            for fut in concurrent.futures.as_completed(fut_map, timeout=batch_timeout):
                idx = fut_map[fut]
                try:
                    result = fut.result(timeout=90)
                    results[str(idx)] = result
                except Exception as e:
                    logger.error(f"[ModelClient] 场景图生成异常: idx={idx}, error={e}")
                    results[str(idx)] = ModelResult(
                        success=False,
                        url="",
                        error=str(e)
                    )
        except concurrent.futures.TimeoutError:
            logger.error(f"[ModelClient] 场景图批量超时 batch_timeout={batch_timeout}s")
            for fut, idx in fut_map.items():
                if str(idx) not in results:
                    results[str(idx)] = ModelResult(success=False, url="", error="batch timeout")
    
    return results


def generate_characters_portraits(
    chars: List[dict],
    preferred: str = None,
    genre: str = ""
) -> Dict[str, str]:
    """角色集体立绘生成（并行）"""
    def _build_prompt(char: dict) -> str:
        name = char.get("name", "角色")
        gender = char.get("gender", "女")
        age = char.get("age", "青年")
        desc_parts = []
        for k in ["personality", "appearance", "role_type", "trait", "description"]:
            v = char.get(k, "")
            if v and len(str(v).strip()) > 1:
                desc_parts.append(str(v).strip())
        person_desc = "，".join(desc_parts[:3]) if desc_parts else f"{gender}性{age}"
        style_hints = {
            "武侠": "古装武侠，佩剑，古风",
            "仙侠": "修仙古装，飘逸，仙气",
            "古装": "古装汉服，古风",
            "现代": "现代时尚",
            "科幻": "科幻未来",
        }
        style_hint = "，古装武侠"
        for gk, gv in style_hints.items():
            if gk in genre:
                style_hint = "，" + gv
                break
        return (
            f"Raw candid photograph. A real {gender} {age} Chinese person, "
            f"{person_desc}, portrait shot, half body, looking at camera,{style_hint} "
            f"studio lighting, beautiful natural lighting, sharp focus, 8K high resolution, "
            f"realistic skin pores and texture, shot on Canon EOS R5 85mm f/1.2, "
            f"photography, photo realistic, real human, not CGI not 3D not cartoon not anime not illustration"
        )
    
    results: Dict[str, str] = {}
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fut_map = {}
        for i, char in enumerate(chars[:4]):
            p = _build_prompt(char)
            fut = pool.submit(generate_image, p, preferred=preferred, size="1920x1920")
            fut_map[fut] = char.get("name", f"角色{i+1}")
        
        batch_timeout = max(120, len(chars) * 90)
        try:
            for fut in concurrent.futures.as_completed(fut_map, timeout=batch_timeout):
                name = fut_map[fut]
                try:
                    r = fut.result(timeout=90)
                    if r.get("success"):
                        results[name] = r["url"]
                    else:
                        logger.warning(f"[CharacterPortrait] {name} 失败: {r.get('error', 'unknown')}")
                except Exception as e:
                    logger.warning(f"[CharacterPortrait] {name} 异常: {e}")
        except concurrent.futures.TimeoutError:
            logger.error(f"[CharacterPortrait] 批量超时 batch_timeout={batch_timeout}s")
    
    return results


def generate_image_to_image(
    prompt: str,
    reference_image: str,
    size: str = "1920x1920",
    timeout: int = 120,
    strength: float = 0.55,
    negative: str = ""
) -> ModelResult:
    """图生图：基于参考图生成新形象。目前仅 Seedream 支持"""
    try:
        from services.ai_providers import ARKImageProvider
        provider = ARKImageProvider()
        urls = provider.generate_image_to_image(prompt, reference_image, size, strength=strength, negative=negative)
        
        if urls and len(urls) > 0:
            logger.info(
                f"[ModelClient] ✅ seedream 图生图成功 "
                f"(timeout={timeout}s) url={urls[0][:80]}"
            )
            return ModelResult(
                success=True,
                url=urls[0],
                model="seedream_i2i",
                error=""
            )
        
        return ModelResult(
            success=False,
            url="",
            model="",
            error="Seedream 图生图返回空"
        )
        
    except Exception as e:
        error_str = str(e)[:200]
        logger.error(f"[ModelClient] ❌ seedream 图生图失败: {error_str}")
        return ModelResult(
            success=False,
            url="",
            model="",
            error=error_str
        )


def download_to_storage(
    url: str,
    name_hint: str = "figure",
    user_id: int = 0
) -> str:
    """从 URL 下载图片到持久存储目录，返回可公开访问的 URL"""
    try:
        fig_dir = "/www/wwwroot/storage/figures/"
        os.makedirs(fig_dir, exist_ok=True)
        
        from services.media_registry import save as _media_save
        
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name_hint))
        h = hashlib.md5(url.encode()).hexdigest()[:8]
        fig_path = os.path.join(fig_dir, f"{safe_name}_{h}.jpg")
        
        if os.path.exists(fig_path) and os.path.getsize(fig_path) > 100:
            return "/storage/figures/" + os.path.basename(fig_path)
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = resp.read()
        
        if len(data) < 10000:
            logger.warning(f"[ModelClient] Download discarded (too small: {len(data)}B): {url}")
            return url
        with open(fig_path, "wb") as f:
            f.write(data)
        
        try:
            _media_save(
                data, os.path.basename(fig_path), "figures",
                name=name_hint, tags=[name_hint], user_id=user_id
            )
        except Exception as e:
            logger.warning(f"[MediaRegistry] figure 注册失败: {e}")
        
        logger.info(f"[ModelClient] ✅ 下载成功: {fig_path}")
        return "/storage/figures/" + os.path.basename(fig_path)
        
    except Exception as e:
        logger.warning(f"[ModelClient] 下载失败: {e}")
        return url


# ── 统一模型客户端 ──────────────────────────────────────────

class UnifiedModel:
    """统一模型客户端。所有智能体只用这个类调 AI"""
    
    @staticmethod
    def image(
        prompt: str,
        preferred: str = None,
        size: str = None,
        timeout: int = None
    ) -> ModelResult:
        return generate_image(prompt, preferred, size, timeout)
    
    @staticmethod
    def image_to_image(
        prompt: str,
        reference_image: str,
        size: str = "1920x1920",
        timeout: int = 120,
        strength: float = 0.55
    ) -> ModelResult:
        return generate_image_to_image(prompt, reference_image, size, timeout, strength,
            negative="cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic")
    
    @staticmethod
    def video(
        prompt: str,
        image_url: str = "",
        preferred: str = None,
        timeout: int = None,
        resolution: str = "720P"
    ) -> ModelResult:
        return generate_video(prompt, image_url, preferred, timeout, resolution)
    
    @staticmethod
    def tts(
        text: str,
        voice: str = "zh-CN-XiaoxiaoNeural",
        speed: float = 1.0,
        timeout: int = None
    ) -> ModelResult:
        return generate_tts(text, voice, speed, timeout)
    
    @staticmethod
    def llm(
        prompt: str,
        system: str = "",
        model: str = "deepseek-chat",
        timeout: int = 60,
        max_tokens: int = 4096
    ) -> ModelResult:
        return call_llm(prompt, system, model, timeout, max_tokens)
    
    @staticmethod
    def character_portraits(
        chars: List[dict],
        genre: str = ""
    ) -> Dict[str, str]:
        return generate_characters_portraits(chars, genre=genre)
    
    @staticmethod
    def scene_images(
        prompts: List[str],
        size: str = None
    ) -> Dict[str, ModelResult]:
        return generate_scene_images(prompts, size=size)
    
    @staticmethod
    def download_to_storage(
        url: str,
        name_hint: str = "figure",
        user_id: int = 0
    ) -> str:
        return download_to_storage(url, name_hint, user_id)