"""
bootstrap.py — 框架启动器
将 core/ 层容器化，桥接旧系统，初始化全局服务
"""
import os
import json
import logging
from pathlib import Path
from typing import Optional

from core.config import (
    Config, Environment, ProviderConfig, LLMConfig,
    ImageConfig, VideoConfig, TTSConfig, DatabaseConfig,
    PipelineConfig, LoggingConfig, ServerConfig
)
from core.container import DependencyContainer, container
from core.errors import FrameworkError
from core.tracing import Tracer, tracer

logger = logging.getLogger("framework.bootstrap")

BASE_DIR = Path(__file__).resolve().parent.parent
KEYS_FILE = BASE_DIR / "config" / "api_keys.json"


def load_api_keys() -> dict:
    """从 api_keys.json 加载 key，带错误处理"""
    if KEYS_FILE.exists():
        try:
            with open(KEYS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"Failed to load api_keys.json: {e}")
    return {}


def build_provider_config(keys: dict) -> ProviderConfig:
    """从 api_keys.json 构建 ProviderConfig"""
    ds_key = keys.get("deepseek", {}).get("key", "") or os.environ.get("DEEPSEEK_API_KEY", "")
    ark_key = keys.get("ark_volc", {}).get("key", "") or os.environ.get("ARK_API_KEY", "")
    ali_key = keys.get("aliyun_bailian", {}).get("key", "") or os.environ.get("ALIYUN_API_KEY", "")
    kling_ak = keys.get("kling_ak", {}).get("key", "") or os.environ.get("KLING_AK", "")
    kling_sk = keys.get("kling_sk", {}).get("key", "") or os.environ.get("KLING_SK", "")
    
    return ProviderConfig(
        deepseek=LLMConfig(api_key=ds_key, base_url="https://api.deepseek.com/v1", model="deepseek-chat"),
        seedance=ImageConfig(api_key=ark_key, base_url="https://ark.cn-beijing.volces.com/api/v3", provider="seedance", timeout=120),
        kling=VideoConfig(api_key=f"{kling_ak}|{kling_sk}", base_url="https://api.kling.kuaishou.com", provider="kling", timeout=300),
        cosyvoice=TTSConfig(api_key=ali_key, base_url="https://dashscope.aliyuncs.com", provider="cosyvoice", timeout=60),
    )


def build_config(keys: Optional[dict] = None) -> Config:
    """构建框架 Config"""
    if keys is None:
        keys = load_api_keys()
    
    env_str = os.environ.get("ENVIRONMENT", "production")
    try:
        env = Environment(env_str)
    except ValueError:
        logger.warning(f"Unknown environment '{env_str}', defaulting to PRODUCTION")
        env = Environment.PRODUCTION
    
    config = Config(
        environment=env,
        debug=os.environ.get("DEBUG", "").lower() in ("1", "true", "yes"),
        server=ServerConfig(host="0.0.0.0", port=8000, workers=1),
        database=DatabaseConfig(url=f"sqlite:///{BASE_DIR}/data/short_drama.db"),
        providers=build_provider_config(keys),
        pipeline=PipelineConfig(max_retries=3, node_timeout=300, max_concurrent_nodes=5),
        logging=LoggingConfig(level=os.environ.get("LOG_LEVEL", "INFO"), enable_tracing=True),
    )
    return config


def bootstrap() -> Config:
    """初始化全局容器和框架服务"""
    config = build_config()
    
    container.register("config", config)
    container.register("Config", config)
    container.register("provider_config", config.providers)
    
    container.register("tracer", tracer)
    container.register("Tracer", tracer)
    
    logger.info(f"Framework bootstrapped: env={config.environment.value}, debug={config.debug}")
    return config


_config: Optional[Config] = None
_container: Optional[DependencyContainer] = None


def get_config() -> Config:
    """线程安全获取配置"""
    global _config
    if _config is None:
        _config = bootstrap()
    return _config


def get_container() -> DependencyContainer:
    """获取依赖容器"""
    global _container
    if _container is None:
        _container = container
    return _container


def get_tracer() -> Tracer:
    """获取追踪器"""
    return tracer