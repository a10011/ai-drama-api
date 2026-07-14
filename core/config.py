"""
config.py — 生产级配置管理
支持 JSON 文件 + 环境变量覆盖，线程安全，类型校验
"""
from __future__ import annotations
import os
import json
import threading
from pathlib import Path
from typing import Dict, Any, Optional, List
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("framework.config")


class Environment(str, Enum):
    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"


@dataclass
class LLMConfig:
    api_key: str = ""
    base_url: str = "https://api.deepseek.com/v1"
    model: str = "deepseek-chat"
    max_tokens: int = 4096
    temperature: float = 0.7
    timeout: int = 60
    max_retries: int = 3


@dataclass
class ImageConfig:
    provider: str = "seedance"
    api_key: str = ""
    base_url: str = ""
    timeout: int = 120


@dataclass
class VideoConfig:
    provider: str = "kling"
    api_key: str = ""
    base_url: str = ""
    timeout: int = 300


@dataclass
class TTSConfig:
    provider: str = "cosyvoice"
    api_key: str = ""
    base_url: str = ""
    timeout: int = 60


@dataclass
class DatabaseConfig:
    url: str = "sqlite:///./data/short_drama.db"
    pool_size: int = 10
    timeout: int = 30
    echo: bool = False


@dataclass
class PipelineConfig:
    max_retries: int = 3
    node_timeout: int = 300
    max_concurrent_nodes: int = 5
    cache_enabled: bool = True
    cache_ttl: int = 3600


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    file_path: Optional[str] = None
    enable_tracing: bool = True


@dataclass
class ServerConfig:
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    cors_origins: List[str] = field(default_factory=lambda: ["*"])


@dataclass
class ProviderConfig:
    deepseek: LLMConfig = field(default_factory=LLMConfig)
    seedance: ImageConfig = field(default_factory=ImageConfig)
    kling: VideoConfig = field(default_factory=VideoConfig)
    cosyvoice: TTSConfig = field(default_factory=TTSConfig)


@dataclass
class Config:
    """全局配置 — 线程安全单例"""
    environment: Environment = Environment.DEVELOPMENT
    debug: bool = True
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    providers: ProviderConfig = field(default_factory=ProviderConfig)
    pipeline: PipelineConfig = field(default_factory=PipelineConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    
    _instance: Optional[Config] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    
    def __post_init__(self):
        """初始化后加载环境变量覆盖 — 安全版本，避免递归"""
        try:
            self._load_from_env()
            self._validate()
        except Exception as e:
            logger.error(f"Config post_init failed: {e}")
    
    @classmethod
    def get_instance(cls) -> Config:
        """线程安全单例获取"""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    config_path = os.environ.get("CONFIG_PATH", "config.json")
                    cls._instance = cls._load_from_file(config_path)
        return cls._instance
    
    @classmethod
    def _load_from_file(cls, path: str) -> Config:
        """从 JSON 文件加载配置"""
        config_path = Path(path)
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return cls._from_dict(data)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load config from {path}: {e}")
        return cls()
    
    @classmethod
    def _from_dict(cls, data: Dict[str, Any]) -> Config:
        """从字典构建配置，避免 __post_init__ 循环"""
        config = object.__new__(cls)
        
        # Initialize all fields manually to avoid __post_init__ recursion
        config.environment = Environment.DEVELOPMENT
        config.debug = True
        config.server = ServerConfig()
        config.database = DatabaseConfig()
        config.providers = ProviderConfig()
        config.pipeline = PipelineConfig()
        config.logging = LoggingConfig()
        config._instance = None
        config._lock = threading.Lock()
        
        if "environment" in data:
            try:
                config.environment = Environment(data["environment"])
            except ValueError:
                logger.warning(f"Unknown environment '{data['environment']}', using default")
        if "debug" in data:
            config.debug = bool(data["debug"])
        
        if "server" in data:
            s = data["server"]
            if isinstance(s, dict):
                config.server.host = s.get("host", config.server.host)
                config.server.port = int(s.get("port", config.server.port))
                config.server.workers = int(s.get("workers", config.server.workers))
                if "cors_origins" in s:
                    config.server.cors_origins = list(s["cors_origins"])
        
        if "database" in data:
            db = data["database"]
            if isinstance(db, dict):
                config.database.url = db.get("url", config.database.url)
                config.database.pool_size = int(db.get("pool_size", config.database.pool_size))
                config.database.timeout = int(db.get("timeout", config.database.timeout))
        
        if "providers" in data:
            providers = data["providers"]
            if isinstance(providers, dict):
                for key in ("deepseek", "seedance", "kling", "cosyvoice"):
                    if key in providers and isinstance(providers[key], dict):
                        p = providers[key]
                        target = getattr(config.providers, key)
                        for field_name in ("api_key", "base_url", "model", "timeout", "max_tokens", "temperature"):
                            if field_name in p:
                                setattr(target, field_name, p[field_name])
        
        if "pipeline" in data:
            pl = data["pipeline"]
            if isinstance(pl, dict):
                for key in ("max_retries", "node_timeout", "max_concurrent_nodes", "cache_enabled", "cache_ttl"):
                    if key in pl:
                        setattr(config.pipeline, key, pl[key])
        
        if "logging" in data:
            lg = data["logging"]
            if isinstance(lg, dict):
                config.logging.level = lg.get("level", config.logging.level)
                config.logging.file_path = lg.get("file_path", config.logging.file_path)
                if "enable_tracing" in lg:
                    config.logging.enable_tracing = bool(lg["enable_tracing"])
        
        try:
            config._load_from_env()
            config._validate()
        except Exception as e:
            logger.error(f"Config._from_dict validation failed: {e}")
        
        return config
    
    def _load_from_env(self):
        """从环境变量加载覆盖"""
        env_map = {
            "DEEPSEEK_API_KEY": ("providers", "deepseek", "api_key"),
            "SEEDANCE_API_KEY": ("providers", "seedance", "api_key"),
            "KLING_API_KEY": ("providers", "kling", "api_key"),
            "COSYVOICE_API_KEY": ("providers", "cosyvoice", "api_key"),
            "DATABASE_URL": ("database", "url"),
            "LOG_LEVEL": ("logging", "level"),
            "DEBUG": ("debug",),
        }
        for env_var, path in env_map.items():
            value = os.environ.get(env_var, "")
            if not value:
                continue
            try:
                if len(path) == 1:
                    setattr(self, path[0], value.lower() in ("1", "true", "yes") if path[0] == "debug" else value)
                elif len(path) == 2:
                    parent = getattr(self, path[0])
                    setattr(parent, path[1], value)
                elif len(path) == 3:
                    parent = getattr(self, path[0])
                    child = getattr(parent, path[1])
                    setattr(child, path[2], value)
            except Exception as e:
                logger.warning(f"Failed to apply env var {env_var}: {e}")
    
    def _validate(self):
        """验证配置"""
        if self.environment == Environment.PRODUCTION:
            if not self.providers.deepseek.api_key:
                logger.warning("DEEPSEEK_API_KEY not set in production")
            if self.debug:
                logger.warning("Debug mode enabled in production")
            if self.server.workers < 2:
                logger.warning(f"Only {self.server.workers} worker(s) configured for production")


class ConfigError(Exception):
    """Configuration error"""
    pass