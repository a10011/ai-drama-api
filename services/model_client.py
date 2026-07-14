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
from utils.path_util import local_path_to_url as _local_path_to_url_legacy
from utils.storage_path import figure_path, local_to_url
from app_config import BASE_URL
import concurrent.futures
from typing import Optional, Dict, Any, List, TypedDict, Union
from dataclasses import dataclass, field
from enum import Enum

from services.retry_manager import enqueue as _retry_enqueue, get_next_interval
from services.validate_input import validate_input
from services.model_spec import (
    SPEC as MODEL_REGISTRY,
    get_chain,
    get_rate,
    framing_prompt,
    NEGATIVE_DEFAULT,
    CURRENT_ECOSYSTEM,
)

# 模型管家 — 调用记录
try:
    from agents.agent_model_manager import get_model_manager as _get_mm
    _model_manager = _get_mm()
except Exception:
    _model_manager = None
from services.usage_tracker import log_usage

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
    last_frame_url: str   # seedance 尾帧，供下一镜首帧衔接
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

# 模块级：存 seedance 最新一次返回的尾帧URL，供下一镜首帧衔接
_last_seedance_frame: str = ""

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

# MODEL_REGISTRY → imported from services.model_spec (above)
# ── 模型限流（防 429） ──────────────────────────────────
import threading as _rl_thr
import time as _rl_time
import random

_model_rate_limits = {}    # {model_name: {"sem": Semaphore, "last_call": float, "rpm": int}}
_rl_lock = _rl_thr.Lock()

def _acquire_model_slot(model_name: str, timeout: float = 300) -> bool:
    """获取模型调用槽位，控制并发和 RPM"""
    from services.model_spec import RATE
    rate = RATE.get(model_name)
    if not rate:
        return True  # 无限流配置，放行
    
    with _rl_lock:
        if model_name not in _model_rate_limits:
            _model_rate_limits[model_name] = {
                "sem": _rl_thr.Semaphore(getattr(rate, "concurrency", 1) or 1),
                "last_call": 0,
                "rpm": getattr(rate, "rpm", 60) or 60,
            }
        entry = _model_rate_limits[model_name]
    
    # 按 RPM 限速
    rpm = entry["rpm"]
    if rpm > 0:
        min_interval = 60.0 / rpm
        now = _rl_time.time()
        wait = min_interval - (now - entry["last_call"])
        if wait > 0:
            _rl_time.sleep(wait)
    
    # 获取并发槽位
    acquired = entry["sem"].acquire(timeout=timeout)
    if acquired:
        entry["last_call"] = _rl_time.time()
    return acquired

def _release_model_slot(model_name: str):
    """释放模型调用槽位"""
    entry = _model_rate_limits.get(model_name)
    if entry:
        entry["sem"].release()


# ── 路由链 ──────────────────────────────────────────

# ── 生态链（阿里系/火山系 各自完整的模型搭配，不混用） ──
# ECOSYSTEM_CHAINS → imported from services.model_spec

# 当前生效生态链（可在 pipeline 请求中指定 ecosystem 参数切换）
# CURRENT_ECOSYSTEM → imported from services.model_spec

# _get_chain → imported from services.model_spec as get_chain

# 兼容旧代码
# ROUTING_CHAINS → removed, use get_chain() from model_spec

# ── 智能路由（模型管家过滤不健康模型）──
def _smart_chain(category: str) -> list:
    """合并静态链 + 模型管家健康过滤"""
    chain = get_chain(category)
    try:
        from agents.agent_model_manager import get_model_manager
        mgr = get_model_manager()
        if mgr and chain:
            smart = mgr.get_best_route(category)
            if smart:
                return [m for m in smart if m in chain] + [m for m in chain if m not in smart]
    except Exception:
        pass
    return chain

def _normalize_size(size: str, model_name: str) -> str:
    """按模型规格标准化尺寸：分隔符、最小像素、最大边长"""
    if not size:
        return "1024x1024"
    cfg = MODEL_REGISTRY.get(model_name, {})
    sep = cfg.get("size_separator", "x")        # 模型需要的分隔符
    min_px = cfg.get("min_pixels", 0)           # 最低像素
    max_dim = cfg.get("max_dimension", 99999)   # 最大边长
    
    # 统一解析成宽高
    raw = size.replace("*", "x")
    if "x" not in raw:
        return raw
    parts = raw.split("x", 1)
    try:
        w, h = int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return "1024x1024"  # safe fallback
    
    # 限制最大边长
    if w > max_dim:
        ratio = max_dim / w
        w, h = max_dim, int(h * ratio)
    if h > max_dim:
        ratio = max_dim / h
        w, h = int(w * ratio), max_dim
    
    # 保证最低像素
    if min_px > 0 and w > 0 and h > 0 and w * h < min_px:
        # 计算要放大多少
        import math
        need = min_px / (w * h)
        scale = math.sqrt(need)
        w = int(w * scale)
        h = int(h * scale)
        # 取整对齐
        w = w + (w % 2)  # 确保偶数
        h = h + (h % 2)
    
    return ("{}x{}" if sep == "x" else "{}*{}").format(w, h)

# 全类别兜底（供降级使用）
# ALL_MODELS → imported from services.model_spec

# ── Provider 调度表（模块级常量，避免重复创建） ──────────

_PROVIDER_DISPATCH: Dict[str, ProviderEntry] = {

    "QwenProvider":           {"module": "services.ai_providers", "symbol": "QwenProvider", "is_callable": True},
    "DoubaoProvider":         {"module": "services.ai_providers", "symbol": "DoubaoProvider", "is_callable": True},
    "ARKImageProvider":       {"module": "services.ai_providers", "symbol": "ARKImageProvider", "is_callable": True},
    "TongyiWanxiangProvider": {"module": "services.ai_providers", "symbol": "TongyiWanxiangProvider", "is_callable": True},
    "AgnesAIProvider":        {"module": "services.ai_providers", "symbol": "AgnesAIProvider", "is_callable": True},
    "HiDreamImageProvider":   {"module": "services.ai_providers", "symbol": "HiDreamImageProvider", "is_callable": True},
    "KlingProvider":          {"module": "services.ai_providers", "symbol": "KlingProvider", "is_callable": True},
    "EdgeTTSProvider":        {"module": "services.ai_providers", "symbol": "EdgeTTSProvider", "is_callable": True},
    "SeedanceProvider":       {"module": "services.ai_providers", "symbol": "SeedanceProvider", "is_callable": True},
    "cosyvoice":              {"module": "services.ai_providers", "symbol": "CosyVoiceV2Provider", "is_callable": True},
    "zhipu":                  {"module": "services.ai_providers", "symbol": "ZhipuProvider", "is_callable": True},
    "music_api":              {"module": "services.ai_providers", "symbol": "music_api_provider", "is_callable": False},
    "happyhorse":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "wan2.7_t2v":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "wan2.7_i2v":             {"module": "services.ai_providers", "symbol": "happyhorse", "is_callable": False},
    "kling-bailian":          {"module": "services.ai_providers", "symbol": "bailian_kling", "is_callable": False},
    "BailianWanxiangChatProvider": {"module": "services.ai_providers", "symbol": "bailian_wanxiang_chat", "is_callable": False},
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
    """判断是否应该重试。
    按需求：不自动重试。一次请求失败就抛异常，由上层/人工定位原因。
    仅在明确的瞬时网络抖动时可考虑放宽，但目前保持不重试。"""
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
    retry_state: Optional[RetryState] = None,
    order_id: str = "",
    user_id: int = 0,
    drama_id: str = "",
    reference_image: str = "",
    strength: float = 0.6
) -> Optional[List[str]]:
    """调用单个图片模型，返回 URL 列表"""
    try:
        provider = _get_provider(model_name)
        if provider is None:
            logger.warning(f"[ModelClient] {model_name} provider 为 None")
            return None
        
        urls: List[str] = []
        # [模型自适应] 按目标模型规范改写描述，让每个模型收到它最懂的 prompt
        from services.model_descriptions import adapt_image_prompt
        framed_prompt = adapt_image_prompt(model_name, prompt, intent="scene")


        logger.debug(f"[ModelClient] 🎯 dispatching order={order_id} to {model_name}")
        if model_name == "seedream":
            if reference_image:
                urls = provider.generate_image_to_image(framed_prompt, reference_image, size, strength=strength)
            else:
                urls = provider.generate_image(framed_prompt, size)
        elif model_name == "wanxiang":
            result = provider.generate_image(framed_prompt, size)
            if isinstance(result, dict):
                urls = result.get("images", []) or (["使用中"] if result.get("url") else [])
            else:
                urls = result if isinstance(result, list) else []
            if not urls:
                logger.warning(f"[ModelClient] 万相原始返回: {json.dumps(result, ensure_ascii=False)[:300]}")
        elif model_name == "agnes":
            urls = provider.generate_image(framed_prompt, size, timeout=timeout, reference_image=reference_image, strength=strength)
            if not urls:
                logger.warning(f"[ModelClient] Agnes 原始返回: urls={urls!r}")
        elif model_name == "hidream":
            urls = provider.generate_image(framed_prompt, size)
            if not urls:
                logger.warning(f"[ModelClient] HiDream 原始返回: urls={urls!r}")
        else:
            urls = provider.generate_image(framed_prompt, size)
        
        log_usage(model_name=model_name, model_type="image", image_count=len(urls),
                  user_id=user_id, drama_id=drama_id)
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
            return _generate_image_with_model(model_name, prompt, size, timeout, retry_state, order_id=order_id, user_id=user_id, drama_id=drama_id, reference_image=reference_image, strength=strength)
        
        return None

# ── 视频生成核心逻辑 ──────────────────────────────────

def _generate_video_with_model(
    model_name: str,
    prompt: str,
    image_url: str,
    audio_url: str,
    timeout: int,
    resolution: str,
    retry_state: Optional[RetryState] = None,
    user_id: int = 0,
    drama_id: str = "",
    **kwargs
) -> Optional[List[str]]:
    """调用单个视频模型，返回 URL 列表"""
    try:
        provider = _get_provider(model_name)
        if provider is None:
            logger.warning(f"[ModelClient] {model_name} provider 为 None")
            return None

        urls: List[str] = []

        # [模型自适应] 按目标视频模型规范改写描述
        from services.model_descriptions import adapt_video_prompt
        prompt = adapt_video_prompt(model_name, prompt or "角色自然表演", has_audio=bool(audio_url))

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
            # [模型自适应] prompt 已由 adapt_video_prompt 按音频情况加口型描述
            url = bp.generate_video(
                prompt,
                image_url, audio_url=audio_url,
                model="wan2.7-i2v-2026-04-25",
                max_wait=timeout or 300,
                resolution=resolution,
                motion_intensity=kwargs.get("motion_intensity", 0.5),
            )
            urls = [url] if url else []
        elif model_name == "agnes-video":
            from services.ai_providers import AgnesAIProvider
            ag = AgnesAIProvider()
            result = ag.generate_video(prompt, image_url=image_url, duration=kwargs.get("duration", 5), max_wait=600)
            if not result or not result.get("success"):
                raise Exception("Agnes video failed: " + str(result.get("error", "unknown")))
            urls = [result["video_url"]]
        elif model_name == "kling":
            urls = provider.generate_video(prompt, image_url, duration=kwargs.get("duration", 5), resolution=resolution, motion_intensity=kwargs.get("motion_intensity", 0.5))
        elif model_name == "kling-bailian":
            from services.bailian_provider import BailianVideoProvider
            bp = BailianVideoProvider()
            url = bp.generate_video(prompt, image_url=image_url,
                                    duration=5, model="kling-v1.6",
                                    max_wait=timeout or 300, resolution=resolution)
            urls = [url] if url else []
        elif model_name == "seedance":
            result = provider.generate_video(
                prompt, duration=kwargs.get("duration", 5), max_wait=timeout or 600,
                image_url=image_url,
                first_frame_url=kwargs.get("first_frame_url", ""),
                reference_images=kwargs.get("reference_images", []),
                dialogue_text=kwargs.get("dialogue_text", ""),
            )
            video_url = ""
            global _last_seedance_frame
            _last_seedance_frame = ""  # 存尾帧供下一镜衔接
            if isinstance(result, dict):
                data = result.get("data", [])
                if isinstance(data, list) and len(data) > 0:
                    video_url = data[0].get("video_url", data[0].get("url", ""))
                elif isinstance(data, dict):
                    video_url = data.get("video_url", data.get("url", ""))
                # 提取尾帧
                if result.get("last_frame_url"):
                    _last_seedance_frame = result["last_frame_url"]
            urls = [video_url] if video_url else []
            if not video_url:
                logger.warning(f"[ModelClient] seedance 返回无视频URL: {json.dumps(result, ensure_ascii=False)[:300]}")
        elif model_name.startswith("happyhorse"):
            hh_mode = MODEL_REGISTRY.get(model_name, {}).get("mode", "t2v")
            if hh_mode == "r2v":
                # 参考生视频: 支持1-9张角色图+场景图做参考
                from urllib.parse import urlparse
                ref_images = kwargs.get("reference_images", [])
                if not ref_images and image_url:
                    ref_images = [image_url]
                # 也支持 character_image + scene_image 双参考
                char_img = kwargs.get("character_image", "")
                scene_img = kwargs.get("scene_image", "")
                multi_chars = kwargs.get("character_images", [])
                if multi_chars:
                    for _ci in multi_chars:
                        if _ci and _ci not in ref_images:
                            ref_images.insert(0, _ci)
                elif char_img and char_img not in ref_images:
                    ref_images.insert(0, char_img)
                if scene_img and scene_img not in ref_images:
                    ref_images.append(scene_img)
                ref_images = ref_images[:9]
                urls = provider.generate_r2v(
                    prompt or "角色在场景中自然表演",
                    reference_images=ref_images,
                    duration=kwargs.get("duration", 5),
                    resolution=resolution,
                    ratio=kwargs.get("ratio", "9:16"),
                    motion_intensity=kwargs.get("motion_intensity", 0.5),
                )
            elif hh_mode == "video-edit":
                urls = provider.generate_video_edit(
                    image_url, prompt or "增强画质，优化色彩",
                    resolution=resolution
                )
            else:
                # t2v / i2v
                urls = provider.generate_video(
                    prompt or "角色自然表演",
                    image_url, resolution=resolution,
                    motion_intensity=kwargs.get("motion_intensity", 0.5),
                )
        else:
            logger.warning(f"[ModelClient] 未知视频模型: {model_name}")
            return []
        
        log_usage(model_name=model_name, model_type="video", video_duration=5,
                  user_id=user_id, drama_id=drama_id)
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
            return _generate_video_with_model(model_name, prompt, image_url, audio_url, timeout, resolution, retry_state, user_id=user_id, drama_id=drama_id)
        
        return None

# ── 公共 API 函数 ──────────────────────────────────────────

def _next_image_seq(project_id: int = 0) -> str:
    """原子递增: 返回 'project_id.seq' 如 10011111.001"""
    import sqlite3 as _sq
    db_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "short_drama.db")
    db = _sq.connect(db_path)
    try:
        # 原子操作：先读再写，用事务保证
        db.execute("BEGIN IMMEDIATE")
        cur = db.execute("SELECT next_seq FROM image_seq WHERE project_id=?", (project_id,))
        row = cur.fetchone()
        if row:
            seq = row[0]
        else:
            seq = 1
            db.execute("INSERT INTO image_seq(project_id, next_seq) VALUES(?,?)", (project_id, seq+1))
            db.commit()
            # 重读确认
            cur = db.execute("SELECT next_seq FROM image_seq WHERE project_id=?", (project_id,))
            seq = cur.fetchone()[0] - 1
        oid = f"{project_id}.{seq:03d}"
        db.execute("UPDATE image_seq SET next_seq=? WHERE project_id=?", (seq+1, project_id))
        db.commit()
        return oid
    finally:
        db.close()

def _gen_order_id(scene_tag: str = "", project_id: int = 0) -> str:
    """生成订单号: project_id.seq 如 10011111.001"""
    if project_id:
        try:
            project_id = int(project_id)
        except Exception:
            project_id = 0
    if project_id and project_id > 0:
        return _next_image_seq(project_id)
    # Fallback: timestamp-based
    t = str(int(time.time() * 1000))
    h = hashlib.md5((t + scene_tag).encode()).hexdigest()[:6]
    return f"ORD_{t}_{h}"

def generate_image(
    prompt: str,
    preferred: str = None,
    size: str = None,
    model: str = None,
    timeout: int = None,
    pipeline_id: str = "",
    order_id: str = "",
    project_id: int = 0,
    user_id: int = 0,
    drama_id: str = "",
    reference_image: str = "",
    strength: float = 0.6
) -> ModelResult:
    """图片生成。限流则等（不换模型），鉴权失败才降级。同模型走到底。"""
    if not order_id:
        order_id = _gen_order_id(prompt[:30], project_id=project_id)
    
    chain = _smart_chain("image")
    
    logger.info(f"[ModelClient] 图片订单 {order_id} | 模型链: {chain}")

    import time as _time
    # 按需求：不自动重试，一次调用失败就抛异常让人工定位。
    MAX_RETRIES = 1           # 只调一次，不重试

    model_name = chain[0] if chain else "seedream"
    cfg = MODEL_REGISTRY.get(model_name)
    if not cfg or cfg.get("static"):
        return ModelResult(success=False, url="", model=model_name, error="模型未注册或为静态资源", order_id=order_id)

    final_size = _normalize_size(size or cfg.get("size", "1024x1024"), model_name)
    final_timeout = timeout or cfg["timeout"]

    try:
        # 获取模型槽位 — 限流时等久点
        slot_timeout = max(final_timeout + 30, 300)  # 至少300秒等槽位
        if not _acquire_model_slot(model_name, timeout=slot_timeout):
            raise RuntimeError(f"{model_name} 槽位获取失败(并发已满)")

        try:
            retry_state = RetryState(model_name=model_name)
            urls = _generate_image_with_model(model_name, prompt, final_size, final_timeout, retry_state, order_id=order_id, user_id=user_id, drama_id=drama_id, reference_image=reference_image, strength=strength)
        finally:
            _release_model_slot(model_name)

        if urls and len(urls) > 0:
            logger.info(f"[ModelClient] ✅ {model_name} 成功 (order={order_id})")
            return ModelResult(success=True, url=urls[0], model=model_name, error="", order_id=order_id)

        # 返回空 → 检查是否有累积错误信息
        last_err = retry_state.last_error if retry_state else ""
        err_msg = last_err if last_err else f"{model_name} 图片生成返回空"
        logger.error(f"[ModelClient] {model_name} 返回空 → 不重试，立即失败 (order={order_id}), last_error={last_err[:100]}")
        return ModelResult(success=False, error=err_msg, url="", model=model_name, order_id=order_id)

    except Exception as e:
        _release_model_slot(model_name)
        error_str = str(e)
        logger.error(f"[ModelClient] ❌ {model_name} 图片失败 → 不重试: {error_str[:120]}")
        # 返回包含原始错误信息的结果
        return ModelResult(success=False, error=error_str[:200], url="", model=model_name, order_id=order_id)
        return ModelResult(success=False, error=f"{model_name} 图片生成失败: {error_str[:150]}", url="", model=model_name, order_id=order_id)


def generate_video(
    prompt: str,
    image_url: str = "",
    audio_url: str = "",
    preferred: str = None,
    timeout: int = None,
    resolution: str = "720P",
    pipeline_id: str = "",
    order_id: str = "",
    user_id: int = 0,
    drama_id: str = "",
    **kwargs
) -> ModelResult:
    """视频生成。同模型重试，不降级切换，返回结构化结果"""
    chain = _smart_chain("video")

    last_error = ""

    model_name = chain[0] if chain else "seedream"
    # [bugfix] 根据有无参考图选择正确的 happyhorse 模型
    # T2V（无参考图）用 happyhorse-t2v，I2V（有参考图）用 happyhorse-r2v
    if model_name == "happyhorse-r2v" and not image_url:
        model_name = "happyhorse-t2v"
        logger.info(f"[ModelClient] 无参考图，切换 happyhorse-r2v → happyhorse-t2v")
    cfg = MODEL_REGISTRY.get(model_name)
    if not cfg or cfg.get("static"):
        return ModelResult(success=False, url="", model=model_name, error="模型未注册或为静态资源", order_id=order_id)

    # [bugfix] 此前主逻辑被错误缩进进上面的 if 块内，导致正常模型时视频生成被完全跳过
    final_timeout = timeout or cfg["timeout"]

    try:
        retry_state = RetryState(model_name=model_name)
        urls = _generate_video_with_model(
            model_name, prompt, image_url, audio_url,
            final_timeout, resolution, retry_state, user_id=user_id, drama_id=drama_id, **kwargs
        )

        if urls and len(urls) > 0:
            logger.info(
                f"[ModelClient] ✅ {model_name} 视频成功 "
                f"url={urls[0][:80]}"
            )
            _mr = ModelResult(
                success=True,
                url=urls[0],
                model=model_name,
                error=""
            )
            # 带上尾帧（seedance 返回的）
            global _last_seedance_frame
            if _last_seedance_frame:
                _mr["last_frame_url"] = _last_seedance_frame
            return _mr

        # 返回空 → 不重试，直接抛异常让人工定位
        logger.error(f"[ModelClient] ❌ {model_name} 视频返回空 → 不重试")
        raise RuntimeError(f"[{model_name}] 视频生成返回空 | 建议: 检查API额度/网络/参数/prompt")

    except Exception as e:
        error_str = str(e)[:150]
        logger.error(f"[ModelClient] ❌ {model_name} 视频失败 → 不重试: {error_str}")
        raise RuntimeError(f"[{model_name}] 视频生成失败: {error_str} | 建议: 检查API额度/网络/参数")
    
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
    timeout: int = None,
    order_id: str = ""
) -> ModelResult:
    """TTS 生成。返回结构化结果"""
    chain = _smart_chain("tts")
    last_error = ""

    model_name = chain[0] if chain else "seedream"
    cfg = MODEL_REGISTRY.get(model_name)
    if not cfg or cfg.get("static"):
        return ModelResult(success=False, url="", model=model_name, error="模型未注册或为静态资源", order_id=order_id)

    # [bugfix] 此前主逻辑被错误缩进进上面的 if 块内，导致正常模型时 TTS 被完全跳过
    final_timeout = timeout or cfg["timeout"]

    try:
        provider = _get_provider(model_name)
        if provider is None:
            raise RuntimeError("TTS provider 不可用")

        # edge-tts 用 generate_tts，cosyvoice 等用 synthesize
        if model_name == "edge-tts":
            result = provider.generate_tts(text, voice, speed)
            # edge-tts 返回 {audio_url: data:...}，落盘后返回 audio_file
            if result and isinstance(result, dict) and result.get("audio_url", "").startswith("data:"):
                import base64 as _b64, os as _os, hashlib as _hl
                try:
                    _, b64 = result["audio_url"].split(",", 1)
                    adata = _b64.b64decode(b64)
                    out_path = f"/tmp/tts_{_hl.md5(text.encode()).hexdigest()[:10]}.mp3"
                    with open(out_path, "wb") as f:
                        f.write(adata)
                    data = {"audio_file": out_path}
                except Exception:
                    data = None
            else:
                data = None
        else:
            data = provider.synthesize(text, voice, speed)

        if data and isinstance(data, dict) and data.get("audio_file"):
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

    return ModelResult(
        success=False,
        audio_path="",
        model="",
        error=last_error
    )


def call_llm(
    prompt: str,
    system: str = "",
    model: str = None,
    timeout: int = 60,
    max_tokens: int = 4096,
    user_id: int = 0,
    drama_id: str = ""
) -> ModelResult:
    """LLM 调用统一入口 - 走生态链路由"""
    if model is None:
        chain = _smart_chain("llm")
        model = chain[0] if chain else "deepseek-chat"
    try:
        # Try ecosystem chain first: map model name to MODEL_REGISTRY entry
        registry_model = None
        for mname, mcfg in MODEL_REGISTRY.items():
            if mcfg.get("type") == "llm" and (mname == model or mcfg.get("model") == model):
                registry_model = mname
                break
        
        if registry_model:
            cfg = MODEL_REGISTRY[registry_model]
            provider_name = cfg.get("provider", "DoubaoProvider")
            provider_model = cfg["model"]
            # Dynamic provider dispatch per MODEL_REGISTRY
            _PROV_CLASSES = {}
            try:
                from services.ai_providers import DoubaoProvider as _DP, QwenProvider as _QP, DeepSeekProvider as _DSP, ZhipuProvider as _ZP, AgnesAIProvider as _AP
                _PROV_CLASSES = {"DoubaoProvider": _DP, "QwenProvider": _QP, "DeepSeekProvider": _DSP, "ZhipuProvider": _ZP, "AgnesAIProvider": _AP}
            except ImportError:
                from services.ai_providers import DoubaoProvider as _DP
                _PROV_CLASSES = {"DoubaoProvider": _DP}
            ProviderCls = _PROV_CLASSES.get(provider_name, _PROV_CLASSES["DoubaoProvider"])
            provider = ProviderCls(model=provider_model)
        else:
            # Legacy routing for non-registry models
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
        text = resp if isinstance(resp, str) else resp.get("content", resp.get("text", str(resp)))
        
        logger.info(f"[ModelClient] ✅ LLM成功: model={model}, response_len={len(text)}")
        log_usage(model_name=model, model_type="llm", total_tokens=len(text)//4,
                  user_id=user_id, drama_id=drama_id)
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
        style_hint = ""
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
    timeout: int = 1200,
    strength: float = 0.25,
    negative: str = "",
    reference_images: list = None,
) -> ModelResult:
    """图生图：多模型兜底 (万相2.7 → Seedream)"""
    # ── 本地路径 → 公网URL ──
    _original = reference_image
    if reference_image and reference_image.startswith('/www/wwwroot/'):
        reference_image = local_path_to_url(reference_image)
        logger.warning(f"[ModelClient] URL 自动修正: {_original[:80]} → {reference_image[:80]}")

    # [OSS跨云修复] 阿里云 dashscope 生图返回的 OSS URL 是临时的(会过期403)，
    # 且火山 ARK 无法下载阿里 OSS(跨云)。这里把任何非本站的远程图先下载到本地，
    # 转成 ai.mzsh.top 公网 URL(火山/万相都能访问)。下载失败说明参考图已失效，
    # 直接返回失败让上游 fallback 到 t2i(文生图)，避免无意义的 ARK/万相调用与等待。
    if reference_image and reference_image.startswith('http') and BASE_URL not in reference_image and 'ai.mzsh.top' not in reference_image:
        try:
            import urllib.request as _ur, ssl as _ssl, hashlib as _hl, os as _os
            ctx = _ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = _ssl.CERT_NONE
            req = _ur.Request(reference_image, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=20, context=ctx) as resp:
                img_data = resp.read()
            if len(img_data) < 1000:
                raise Exception(f"参考图太小({len(img_data)}B),可能已失效")
            _os.makedirs("/www/wwwroot/storage/figures", exist_ok=True)
            h = _hl.md5(img_data).hexdigest()[:12]
            local_path = f"/www/wwwroot/storage/figures/i2i_ref_{h}.jpg"
            with open(local_path, "wb") as f:
                f.write(img_data)
            reference_image = local_path_to_url(local_path)
            logger.info(f"[ModelClient] 参考图本地化: {_original[:60]} → {reference_image[:60]}")
        except Exception as e:
            logger.warning(f"[ModelClient] 参考图本地化失败(可能OSS过期): {str(e)[:100]} → 跳过i2i,上游将fallback t2i")
            return ModelResult(success=False, url="", model="", error=f"参考图失效无法i2i: {str(e)[:80]}")

    strength_val = max(0.1, min(0.95, strength))
    framed_i2i_prompt = prompt  # 肖像prompt自带构图

    # ── 链1: Seedream (ARK/火山) — 优先使用 ──
    # 按官方文档：size 用 "2K" 规格字符串（非像素格式），不传 strength（文档未定义）。
    try:
        from services.ai_providers import ARKImageProvider
        ark = ARKImageProvider()
        urls = ark.generate_image_to_image(framed_i2i_prompt, reference_image, "2K")
        if urls and len(urls) > 0:
            logger.info(f"[ModelClient] ✅ seedream I2I url={urls[0][:80]}")
            return ModelResult(success=True, url=urls[0], model="seedream_i2i", error="")
    except Exception as e:
        logger.warning(f"[ModelClient] ⚠️ seedream I2I failed, trying wanxiang: {str(e)[:120]}")
    
    # ── 链2: 万相2.7 (Bailian) 兜底 ──
    try:
        from services.ai_providers import bailian_wanxiang_chat
        norm_size = _normalize_size(size, "wan2.7-image-pro")
        urls = bailian_wanxiang_chat.generate_image_to_image(framed_i2i_prompt, reference_image, norm_size, strength=strength_val)
        if urls and len(urls) > 0:
            logger.info(f"[ModelClient] ✅ wanxiang I2I (strength={strength_val:.2f}) url={urls[0][:80]}")
            return ModelResult(success=True, url=urls[0], model="wanxiang_i2i", error="")
    except Exception as e:
        logger.error(f"[ModelClient] ❌ wanxiang I2I failed: {str(e)[:120]}")
    
    return ModelResult(success=False, url="", model="", error="所有I2I模型失败")

def _classify_error_str(error_str: str) -> str:
    """简单错误分类"""
    if "401" in error_str or "Unauthorized" in error_str or "InvalidApiKey" in error_str:
        return "401_auth"
    if "429" in error_str or "rate" in error_str.lower():
        return "429_rate"
    if "503" in error_str or "timeout" in error_str.lower():
        return "503_server"
    return "unknown"


def download_to_storage(
    url: str,
    name_hint: str = "figure",
    user_id: int = 0,
    order_id: str = "",
    pipeline_id: str = "",
    project_id: str = ""
) -> str:
    """从 URL 下载图片到持久存储目录，返回可公开访问的 URL"""
    try:
        _pid = project_id or pipeline_id or ""
        safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(name_hint))
        h = hashlib.md5(url.encode()).hexdigest()[:8]
        prefix = f"{order_id}_" if order_id else ""
        fname = f"{prefix}{safe_name}_{h}.jpg"
        if _pid and not _pid.startswith("pipe_"):
            fig_path, fig_url = figure_path(_pid, fname)
            local_path_to_use = fig_path
            result_url = fig_url
        else:
            base_dir = f"/www/wwwroot/storage/{_pid}" if _pid else "/www/wwwroot/storage"
            fig_dir = os.path.join(base_dir, "figures")
            os.makedirs(fig_dir, exist_ok=True)
            fig_path = os.path.join(fig_dir, fname)
            local_path_to_use = fig_path
            result_url = _local_path_to_url_legacy(fig_path)
        
        
        
        from services.media_registry import save as _media_save
        
        if os.path.exists(local_path_to_use) and os.path.getsize(local_path_to_use) > 100:
            return result_url
        
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = resp.read()
        
        if len(data) < 10000:
            logger.warning(f"[ModelClient] Download discarded (too small: {len(data)}B): {url}")
            return url
        with open(local_path_to_use, "wb") as f:
            f.write(data)
        
        try:
            _media_save(
                data, os.path.basename(local_path_to_use), "figures",
                name=name_hint, tags=[name_hint], user_id=user_id,
                metadata={"order_id": order_id, "pipeline_id": pipeline_id} if order_id or pipeline_id else None
            )
        except Exception as e:
            logger.warning(f"[MediaRegistry] figure 注册失败: {e}")
        
        logger.info(f"[ModelClient] DOWNLOAD_OK: {local_path_to_use}")
        return local_to_url(local_path_to_use)
        
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
        timeout: int = None,
        project_id: int = 0,
        reference_image: str = "",
        strength: float = 0.6
    ) -> ModelResult:
        return generate_image(prompt, preferred, size, timeout, project_id=project_id, user_id=0, drama_id="", reference_image=reference_image, strength=strength)
    
    @staticmethod
    def image_to_image(
        prompt: str,
        reference_image: str,
        size: str = "1920x1920",
        timeout: int = 1200,
        strength: float = 0.25,
        reference_images: list = None,
    ) -> ModelResult:
        return generate_image_to_image(prompt, reference_image, size, timeout, strength,
            reference_images=reference_images,
            negative="cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic, manga, pixar, disney, cartoon face, big eyes, exaggerated features, 2D, cel shading, toon, chibi")
    
    @staticmethod
    def video(
        prompt: str,
        image_url: str = "",
        preferred: str = None,
        timeout: int = None,
        resolution: str = "720P",
        **kwargs
    ) -> ModelResult:
        return generate_video(prompt, image_url, preferred=preferred, timeout=timeout, resolution=resolution, user_id=0, **kwargs)
    
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
        model: str = None,
        timeout: int = 60,
        max_tokens: int = 4096
    ) -> ModelResult:
        return call_llm(prompt, system, model, timeout, max_tokens, user_id=0, drama_id="")
    
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
        user_id: int = 0,
        order_id: str = "",
        pipeline_id: str = "",
        project_id: str = ""
    ) -> str:
        return download_to_storage(url, name_hint, user_id, order_id, pipeline_id=pipeline_id, project_id=project_id)