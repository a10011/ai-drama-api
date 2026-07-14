"""
模型规格统一配置文件（v1.0）

单点配置所有模型参数、限流、尺寸、路由链。
其他模块从此文件导入，不再散落各处。

使用方式:
    from services.model_spec import SPEC, CHAIN, SIZE, RATE, RETRY
    model = SPEC["wanxiang"]           # ModelSpec
    chain = CHAIN.deepseek.image       # ["wanxiang"]
    size = SIZE.scene_background       # "1440x2560"
    limit = RATE["wanxiang"]           # RateSpec(concurrency=1, rpm=2)
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

# ═══════════════════════════════════════════════════════════════
# 基础数据类
# ═══════════════════════════════════════════════════════════════

@dataclass
class ModelSpec:
    """单个模型完整规格（支持 dict 风格兼容访问）"""
    provider: str           # Provider 类名
    service: str            # 服务商标识
    model_id: str           # API model ID
    type: str               # image | video | llm | tts | bgm
    timeout: int = 60       # API 超时(秒)
    # 图片专属
    size: str = ""          # 默认尺寸
    size_separator: str = "x"  # 宽高分隔符
    min_pixels: int = 0     # 最小像素要求
    max_dimension: int = 99999  # 最大边长
    # 视频专属
    mode: str = ""          # t2v | i2v | r2v | edit
    resolution: str = "720P"
    duration: int = 5
    poll_interval: int = 10
    max_polls: int = 30
    # 通用
    static: bool = False    # 静态资源(不调API)
    negative: str = ""      # 默认负向提示词
    style: str = ""         # 风格参数

    # ── dict 兼容层（向后兼容旧 MODEL_REGISTRY 代码）──
    def __getitem__(self, key: str):
        if key == "model":
            return self.model_id
        return getattr(self, key)

    def get(self, key: str, default=None):
        if key == "model":
            return self.model_id
        return getattr(self, key, default)

@dataclass
class RateSpec:
    """模型限流规格"""
    concurrency: int = 1    # 最大并发数
    rpm: int = 60           # 每分钟最大请求数
    acquire_timeout: int = 30  # 获取许可超时(秒)

@dataclass
class RetrySpec:
    """重试策略规格"""
    max_retries: int = 2    # 同模型最大重试次数
    backoff_seconds: List[int] = field(default_factory=lambda: [3, 6, 15, 30])
    wait_429: int = 3        # 收到429时等待秒数
    video_wait_429: int = 30
    queue_intervals: List[int] = field(default_factory=lambda: [5, 10, 30, 60, 60, 120])  # 分钟

@dataclass
class SizeSpec:
    """竖屏尺寸规格"""
    character_portrait: str = "1920x1920"
    scene_background: str = "1440x2560"
    cover_thumbnail: str = "720x960"
    fallback_square: str = "1024x1024"
    fallback_wanxiang: str = "768*1024"

@dataclass
class EcosystemChain:
    """生态链（一个生态内的模型搭配）"""
    llm: List[str] = field(default_factory=list)
    image: List[str] = field(default_factory=list)
    video: List[str] = field(default_factory=list)
    tts: List[str] = field(default_factory=list)
    bgm: List[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════
# 尺寸规格
# ═══════════════════════════════════════════════════════════════

SIZE = SizeSpec()

# ═══════════════════════════════════════════════════════════════
# 模型规格库
# ═══════════════════════════════════════════════════════════════

SPEC: Dict[str, ModelSpec] = {
    # ── 图片模型 ──
    "seedream": ModelSpec(
        provider="ARKImageProvider", service="ark_volc",
        model_id="doubao-seedream-5-0-260128",
        type="image", timeout=60,
        size="1920x2560", size_separator="x", min_pixels=3686400,
        negative="cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic, deformed face, missing face, distorted face, blurry face, disfigured, bad anatomy, extra limbs",
        max_dimension=2048,
    ),
    "hidream": ModelSpec(
        provider="HiDreamImageProvider", service="hidream",
        model_id="z1-image",
        type="image", timeout=60,
        size=SIZE.fallback_square, size_separator="x",
    ),
    "agnes": ModelSpec(
        provider="AgnesAIProvider", service="agnes",
        model_id="agnes-image-2.1-flash",
        type="image", timeout=120,
        size=SIZE.fallback_square, size_separator="x",
    ),
    # ── 万相 2.7 旗舰（Chat格式API）──
    "wan2.7-image-pro": ModelSpec(
        provider="BailianWanxiangChatProvider",
        service="bailian",
        model_id="wan2.7-image-pro",
        type="image",
        timeout=90,
        size="1024x1024",
        size_separator="*",
        min_pixels=1024*1024,
        max_dimension=1440,
    ),

    "wanxiang": ModelSpec(
        provider="TongyiWanxiangProvider", service="aliyun_bailian",
        model_id="wanx2.1-t2i-plus",
        type="image", timeout=60,
        size=SIZE.fallback_wanxiang, size_separator="*", max_dimension=1440,
    ),
    # ── 视频模型 ──
    "kling": ModelSpec(
        provider="KlingProvider", service="kling",
        model_id="kling-v2-6",
        type="video", timeout=60,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=60,
    ),
    "seedance": ModelSpec(
        provider="SeedanceProvider", service="ark_volc",
        model_id="doubao-seedance-2-0-260128",
        type="video", timeout=120,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=12,
    ),
    "wan2.7_t2v": ModelSpec(
        provider="wan2.7_t2v", service="aliyun_bailian",
        model_id="wan2.7-t2v",
        type="video", timeout=900,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=90,
    ),
    "wan2.7_i2v": ModelSpec(
        provider="wan2.7_i2v", service="aliyun_bailian",
        model_id="wan2.7-i2v-2026-04-25",
        type="video", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),
    "agnes-video": ModelSpec(
        provider="AgnesAIProvider", service="agnes_hub",
        model_id="agnes-video-v2.0",
        type="video", timeout=120,
        resolution="720P", duration=15,
        poll_interval=5, max_polls=60,
    ),
    "happyhorse-t2v": ModelSpec(
        provider="happyhorse", service="happyhorse",
        model_id="happyhorse-1.1-t2v",
        type="video", mode="t2v", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),
    "happyhorse-i2v": ModelSpec(
        provider="happyhorse", service="happyhorse",
        model_id="happyhorse-1.1-i2v",
        type="video", mode="i2v", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),
    "happyhorse-r2v": ModelSpec(
        provider="happyhorse", service="happyhorse",
        model_id="happyhorse-1.1-r2v",
        type="video", mode="r2v", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),
    "happyhorse-video-edit": ModelSpec(
        provider="happyhorse", service="happyhorse",
        model_id="happyhorse-1.0-video-edit",
        type="video", mode="edit", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),

    "kling-bailian": ModelSpec(
        provider="kling-bailian", service="kling-bailian",
        model_id="kling-v1.6",
        type="video", timeout=300,
        resolution="720P", duration=5,
        poll_interval=10, max_polls=30,
    ),
    # ── TTS 模型 ──
    "edge-tts": ModelSpec(
        provider="EdgeTTSProvider", service="edge",
        model_id="edge-tts",
        type="tts", timeout=15,
    ),
    "cosyvoice": ModelSpec(
        provider="cosyvoice", service="aliyun_bailian",
        model_id="cosyvoice-v2",
        type="tts", timeout=20,
    ),
    "seed-audio-1.0": ModelSpec(
        provider="SeedAudioProvider", service="ark_volc",
        model_id="seed-audio-1.0",
        type="tts", timeout=120,
    ),
    # ── LLM 模型 ──
    "doubao": ModelSpec(
        provider="DoubaoProvider", service="ark_volc",
        model_id="doubao-seed-2-1-pro-260628",
        type="llm", timeout=120,
    ),
    "qwen-max": ModelSpec(
        provider="QwenProvider", service="aliyun_bailian",
        model_id="qwen3.7-max-2026-06-08",
        type="llm", timeout=120,
    ),
    "deepseek-v4-flash": ModelSpec(
        provider="DeepSeekProvider", service="deepseek",
        model_id="deepseek-v4-flash",
        type="llm", timeout=180,
    ),
    "agnes-2.0-flash": ModelSpec(
        provider="AgnesAIProvider", service="agnes",
        model_id="agnes-2.0-flash",
        type="llm", timeout=300,
    ),
    "deepseek-reasoner": ModelSpec(
        provider="DeepSeekProvider", service="deepseek",
        model_id="deepseek-reasoner",
        type="llm", timeout=300,
    ),
    "glm-4-plus": ModelSpec(
        provider="ZhipuProvider", service="zhipu",
        model_id="glm-4-plus",
        type="llm", timeout=120,
    ),
    "glm-5.2": ModelSpec(
        provider="ZhipuProvider", service="zhipu",
        model_id="glm-5.2",
        type="llm", timeout=180,
    ),
    # ── 其他 ──
    # P0-2: local_bgm removed from SPEC
    "music_api": ModelSpec(
        provider="music_api", service="music",
        model_id="bgm-generator",
        type="bgm", timeout=30,
    ),
}


# ═══════════════════════════════════════════════════════════════
# 限流规格（按模型）
# ═══════════════════════════════════════════════════════════════

RATE: Dict[str, RateSpec] = {
    # 图片：单并发（API 容量有限）
    "wanxiang":   RateSpec(concurrency=1, rpm=2),
    "seedream":   RateSpec(concurrency=1, rpm=2),
    "hidream":    RateSpec(concurrency=1, rpm=1),
    "agnes":      RateSpec(concurrency=1, rpm=1),
    # 视频：单并发（生成耗时 1-3 分钟）
    "happyhorse-r2v": RateSpec(concurrency=1, rpm=1),
    "happyhorse-t2v": RateSpec(concurrency=1, rpm=1),
    "happyhorse-i2v": RateSpec(concurrency=1, rpm=1),
    "seedance":   RateSpec(concurrency=1, rpm=1),
    "kling":      RateSpec(concurrency=1, rpm=1),
    "wan2.7_t2v": RateSpec(concurrency=1, rpm=1),
    "wan2.7_i2v": RateSpec(concurrency=1, rpm=1),
    "kling-bailian": RateSpec(concurrency=1, rpm=1),
    # LLM：适度并发
    "deepseek-v4-flash": RateSpec(concurrency=3, rpm=60),
    "agnes-2.0-flash": RateSpec(concurrency=1, rpm=2),
    "deepseek-reasoner": RateSpec(concurrency=2, rpm=30),
    "glm-4-plus":   RateSpec(concurrency=2, rpm=30),
    "qwen-max":   RateSpec(concurrency=3, rpm=60),
    "doubao":     RateSpec(concurrency=3, rpm=60),
    # TTS
    "edge-tts":   RateSpec(concurrency=5, rpm=100),
    "cosyvoice":  RateSpec(concurrency=2, rpm=50),
    "seed-audio-1.0": RateSpec(concurrency=3, rpm=20),
    # 默认
    "_default":   RateSpec(concurrency=1, rpm=10),
}


# ═══════════════════════════════════════════════════════════════
# 重试策略
# ═══════════════════════════════════════════════════════════════

RETRY = RetrySpec()

# 重试队列扫描间隔(秒)
RETRY_SCAN_INTERVAL = 30
# 重试队列线程池大小
RETRY_POOL_SIZE = 4
# 重试队列单模型并发
RETRY_PER_MODEL = 2
# 连续失败上限(标记dead)
RETRY_MAX_CONSECUTIVE_FAILS = 3


# ═══════════════════════════════════════════════════════════════
# 提示词常量
# ═══════════════════════════════════════════════════════════════

FRAMING_PROMPT_CN = (
    "竖屏9:16构图，主体居中，全身完整入画，"
    "画面充满全屏不留白，"
    "人脸和关键动作在画面中央，"
    "不要裁切身体任何部分，"
    "写实电影质感，真实摄影风格，实拍感，"
    "真人摄影，实景拍摄，cinematic live action photography"
)

FRAMING_PROMPT_EN = (
    "vertical portrait composition 9:16, "
    "subject centered, full body visible, "
    "fill entire frame no empty space, "
    "face and key action in center of frame, "
    "do not crop any part of the body, "
    "photorealistic, cinematic, real photography, shot on ARRI Alexa, "
    "live action film photography, realistic human photography"
)

NEGATIVE_DEFAULT = (
    "cartoon, anime, illustration, painting, drawing, "
    "3D render, CGI, stylized, unrealistic, plastic skin, "
    "doll, game character, portrait painting, digital art, comic, deformed face, missing face, distorted face, blurry face, disfigured, bad anatomy, extra limbs"
)

NEGATIVE_CHARACTER = NEGATIVE_DEFAULT  # 角色图专用（与场景同）

def framing_prompt(bilingual: bool = True) -> str:
    """返回构图追加语"""
    if bilingual:
        return f"{FRAMING_PROMPT_CN}\n{FRAMING_PROMPT_EN}"
    return FRAMING_PROMPT_CN


# ═══════════════════════════════════════════════════════════════
# 岗位 → LLM 模型分配
# 创意/中文创作用 glm-5.2(中文强)；结构化/解析用 deepseek-v4-flash(快省)；
# 质量要求高的剧本/QA 用 deepseek-v4-pro(质量优先)
# ═══════════════════════════════════════════════════════════════

ROLE_MODEL: Dict[str, str] = {
    "script":          "agnes-2.0-flash",    # 剧本创作：质量优先，戏剧结构需深度
    "director":        "agnes-2.0-flash",     # 导演分析:题材判断要准用最强模型
    "director_script": "deepseek-v4-pro",     # 导演剧本分析(定genre):必须聪明判错全剧崩
    "character":     "agnes-2.0-flash",            # 角色设计：人设创意
    "storyboard":    "agnes-2.0-flash",            # 分镜：电影语言
    "cinematographer": "glm-5.2",            # 摄影指导：运镜构图
    "wardrobe":        "deepseek-v4-flash",  # 服化道：结构化输出
    "sfx":             "deepseek-v4-flash",  # 特效设计：结构化
    "scene":           "agnes-2.0-flash",  # 场景设计：结构化
    "subtitle":        "deepseek-v4-flash",  # 字幕：快速、轻量
    "bgm":             "deepseek-v4-flash",  # BGM 元数据：结构化
    "tts":             "deepseek-v4-flash",  # TTS 选角/标注
    "costume":         "glm-5.2",            # 造型设计：创意
    "qa":              "deepseek-v4-pro",    # QA 质检：质量优先
    "default":         "deepseek-v4-flash",  # 兜底
}

def get_role_model(agent_id: str) -> str:
    """根据智能体岗位返回推荐的 LLM 模型名"""
    return ROLE_MODEL.get(agent_id, ROLE_MODEL["default"])


# ═══════════════════════════════════════════════════════════════
# 生态链（模型路由）
# ═══════════════════════════════════════════════════════════════

# 火山引擎：豆包纯血
CHAIN_VOLC = EcosystemChain(
    llm=["agnes-2.0-flash"],
    image=["seedream", "agnes"],  # seedream=豆包(质量好), agnes=备选
    video=["agnes-video"],
    tts=["seed-audio-1.0"],
    bgm=[],
)

# 阿里百炼：快乐马纯血
CHAIN_ALIYUN = EcosystemChain(
    llm=["agnes-2.0-flash"],
    image=["wanxiang"],
    video=["happyhorse-r2v"],
    tts=["cosyvoice"],
    bgm=["music_api"],
)

# DeepSeek LLM + 万相图（真人写实）+ 快乐马视频（主力）
CHAIN_DEEPSEEK = EcosystemChain(
    llm=["glm-5.2","deepseek-v4-flash"],
    image=["seedream"], # seedream→2.7→seedream兜底
    video=["happyhorse-r2v"],
    tts=["cosyvoice"],
    bgm=["music_api"],
)

ECOSYSTEM_CHAINS: Dict[str, EcosystemChain] = {
    "volc": CHAIN_VOLC,
    "aliyun": CHAIN_ALIYUN,
    "deepseek": CHAIN_DEEPSEEK,
}

# 当前生效生态
CURRENT_ECOSYSTEM = "volc"

# 全局兜底（生态链为空时使用）
ALL_CHAINS = EcosystemChain(
    image=["seedream"],
    video=["happyhorse-r2v"],
    tts=["cosyvoice"],
    bgm=["music_api"],
)


# ═══════════════════════════════════════════════════════════════
# 便捷访问函数
# ═══════════════════════════════════════════════════════════════

def get_model(name: str) -> Optional[ModelSpec]:
    """获取模型规格"""
    return SPEC.get(name)

def get_chain(category: str) -> List[str]:
    """获取当前工作流某类别的模型列表"""
    eco = ECOSYSTEM_CHAINS.get(CURRENT_ECOSYSTEM, CHAIN_VOLC)
    result = getattr(eco, category, [])
    if not result:
        result = getattr(ALL_CHAINS, category, [])
    return result

def get_rate(model_name: str) -> RateSpec:
    """获取模型限流规格"""
    return RATE.get(model_name, RATE["_default"])

PRICING = {
    "seedream":    {"unit": "image",     "price": 0.20, "label": "Seedream 5.0"},
    "hidream":     {"unit": "image",     "price": 0.10, "label": "HiDream"},
    "agnes":       {"unit": "image",     "price": 0.05, "label": "Agnes 2.1"},
    "wanxiang":    {"unit": "image",     "price": 0.15, "label": "万相 2.1"},
    "seedance":    {"unit": "video_sec", "price": 0.50, "label": "Seedance 2.0"},
    "agnes-video": {"unit": "video_sec", "price": 0.30, "label": "Agnes Video 2.0"},
    "wan2.7_t2v":  {"unit": "video_sec", "price": 0.20, "label": "wan2.7 文生"},
    "wan2.7_i2v":  {"unit": "video_sec", "price": 0.25, "label": "wan2.7 图生"},
    "happyhorse-t2v": {"unit": "video_sec", "price": 0.30, "label": "HappyHorse 文生"},
    "happyhorse-i2v": {"unit": "video_sec", "price": 0.35, "label": "HappyHorse 图生"},
    "happyhorse-r2v": {"unit": "video_sec", "price": 0.30, "label": "HappyHorse 参考生"},
    "happyhorse-video-edit": {"unit": "video_sec", "price": 0.25, "label": "HappyHorse 编辑"},
    "kling-bailian":  {"unit": "video_sec", "price": 0.35, "label": "可灵 v1.6 (百炼)"},
    "deepseek-chat": {"unit": "token_1k", "price": 0.001, "label": "DeepSeek Chat"},
    "qwen-max":    {"unit": "token_1k",  "price": 0.005, "label": "通义 Qwen3-Max"},
    "doubao":      {"unit": "token_1k",  "price": 0.004, "label": "豆包 Pro"},
    "edge-tts":    {"unit": "char",      "price": 0.0,   "label": "Edge TTS"},
    "cosyvoice":   {"unit": "char",      "price": 0.002, "label": "CosyVoice"},
    "seed-audio-1.0": {"unit": "min",       "price": 0.06,  "label": "豆包音频 1.0"},
    "local_bgm":   {"unit": "request",   "price": 0.0,   "label": "本地 BGM"},
}

_DEFAULT_PRICE = {"unit": "request", "price": 0.0, "label": "未知"}

def get_price(model_name: str) -> dict:
    return PRICING.get(model_name, _DEFAULT_PRICE)

def calc_cost(model_name: str, quantity: float = 1.0) -> float:
    p = PRICING.get(model_name, _DEFAULT_PRICE)
    return round(p["price"] * quantity, 4)
