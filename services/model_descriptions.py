"""模型描述适配器 — 让每个模型收到它最懂的描述

不同模型对 prompt 的理解能力、风格偏好、参数规范差异很大。
本模块根据目标模型，把通用描述改写为该模型最易理解、效果最佳的形式。

核心函数:
    adapt_image_prompt(model_name, base_prompt, intent) -> str
    adapt_video_prompt(model_name, base_prompt, intent) -> str
    get_model_guide(model_name) -> dict   # 模型使用知识
"""
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# 各模型使用知识（静态知识库 — 供智能体参考）
# ═══════════════════════════════════════════════════════════════

MODEL_GUIDE = {
    # ── 图片模型 ──
    "seedream": {
        "provider": "火山方舟 ARK",
        "strengths": "写实摄影、真人质感、光影控制强、支持 negative_prompt 和 strength",
        "prompt_style": "中英双语，英文摄影术语为主。强调 photorealistic/cinematic/8K，加 skin texture 等真实感词。支持 negative_prompt 排除不想要的元素",
        "tips": "size 需≥3686400像素(如1920x1920)；strength 0.3保脸/0.5平衡/0.7大改；负向词可压卡通动漫风",
        "size_sep": "x",
        "min_pixels": 3686400,
    },
    "wan2.7-image-pro": {
        "provider": "阿里百炼",
        "strengths": "中文理解强、构图稳定、Chat格式多模态、图生图锁脸好",
        "prompt_style": "中文描述为主，自然语言详述场景。size 用 * 分隔(如1024*1024)，边长≤1440。图生图传 image URL",
        "tips": "size 用*不用x；中文prompt效果优于英文；边长超1440会被拒；图生图用 multimodal-generation 端点",
        "size_sep": "*",
        "min_pixels": 1048576,
    },
    "wanxiang": {
        "provider": "阿里百炼(老版)",
        "strengths": "稳定、中文支持、异步任务",
        "prompt_style": "中文自然语言，描述场景细节。size 用*，边长512-1440",
        "tips": "size范围512-1440；异步提交+轮询；不支持negative_prompt，靠正面词引导",
        "size_sep": "*",
        "min_pixels": 262144,
    },
    "agnes": {
        "provider": "Agnes Hub",
        "strengths": "免费备用、OpenAI兼容、速度快",
        "prompt_style": "英文 prompt 为主，OpenAI images 格式。简洁描述",
        "tips": "免费额度；英文prompt；size 用x",
        "size_sep": "x",
        "min_pixels": 0,
    },
    "hidream": {
        "provider": "智象HiDream",
        "strengths": "异步、多尺寸",
        "prompt_style": "中文描述，resolution 用*",
        "tips": "异步提交+轮询；aspect_ratio 1:1",
        "size_sep": "*",
        "min_pixels": 0,
    },
    # ── 视频模型 ──
    "happyhorse-r2v": {
        "provider": "阿里百炼",
        "strengths": "参考图锁脸、人物一致性强、口型同步",
        "prompt_style": "中文简短动作描述为主。重点写角色动作和情绪，不要长篇环境描写。参考图1-9张",
        "tips": "prompt 要简短聚焦动作(≤50字)；参考图首选角色正脸立绘；duration 5秒；resolution 720P",
        "video_params": {"duration": 5, "resolution": "720P", "ratio": "9:16"},
    },
    "happyhorse-t2v": {
        "provider": "阿里百炼",
        "strengths": "文生视频、无参考图、场景生成",
        "prompt_style": "中文描述场景+动作，可稍详细。无参考图，靠文字塑造画面",
        "tips": "prompt 写清场景+人物动作；duration 5秒",
        "video_params": {"duration": 5, "resolution": "720P", "ratio": "9:16"},
    },
    "happyhorse-i2v": {
        "provider": "阿里百炼",
        "strengths": "图生视频、首帧驱动、口型同步",
        "prompt_style": "中文简短动作描述。首帧图+可选音频驱动口型",
        "tips": "首帧图必传；有音频时加口型动作描述",
        "video_params": {"duration": 5, "resolution": "720P"},
    },
    "kling": {
        "provider": "可灵",
        "strengths": "动作流畅、物理感强、电影感",
        "prompt_style": "中英文均可，动作描写为主。重点描述角色动作和镜头运动，简洁有力",
        "tips": "duration 5秒；image_url 可选；cfg_scale 控制创造性",
        "video_params": {"duration": 5, "cfg_scale": 0.5},
    },
    "seedance": {
        "provider": "火山方舟 ARK",
        "strengths": "电影感、运镜流畅、画质高",
        "prompt_style": "中英文，content数组格式。描述画面+运镜+动作",
        "tips": "异步任务+轮询；resolution 720p；ratio 9:16",
        "video_params": {"duration": 5, "resolution": "720p", "ratio": "9:16"},
    },
    "wan2.7_t2v": {
        "provider": "阿里百炼",
        "strengths": "文生视频、长时长",
        "prompt_style": "中文描述场景与动作",
        "tips": "timeout 长(900s)；异步轮询",
        "video_params": {"duration": 5, "resolution": "720P", "ratio": "9:16"},
    },
    "wan2.7_i2v": {
        "provider": "阿里百炼",
        "strengths": "图生视频、口型同步、音频驱动",
        "prompt_style": "中文简短动作描述，有音频时强调嘴部动作",
        "tips": "image_url+audio_url；口型同步加嘴部动作描述",
        "video_params": {"duration": 5, "resolution": "720P"},
    },
}

# 默认知识（未知模型用）
_DEFAULT_GUIDE = {
    "prompt_style": "中英双语，自然语言详述",
    "tips": "",
    "size_sep": "x",
}


def get_model_guide(model_name: str) -> dict:
    """获取模型使用知识"""
    return MODEL_GUIDE.get(model_name, _DEFAULT_GUIDE)


# ═══════════════════════════════════════════════════════════════
# 图片 prompt 适配
# ═══════════════════════════════════════════════════════════════

# 写实真人增强词（seedream/万相生图通用）
_REALISTIC_BOOST_EN = (
    "photorealistic, real photograph, cinematic lighting, 8K, "
    "realistic skin texture, natural lighting, live action film still, "
    "shot on ARRI Alexa, professional cinematography"
)
_REALISTIC_NEGATIVE = (
    "cartoon, anime, illustration, painting, drawing, 3D render, CGI, "
    "stylized, unrealistic, plastic skin, doll, game character, comic"
)

# 竖屏构图（竖屏短剧通用）
_VERTICAL_CN = "竖屏9:16构图，主体居中，全身完整入画，写实电影质感"
_VERTICAL_EN = "vertical 9:16 composition, subject centered, full body visible, cinematic"


def adapt_image_prompt(model_name: str, base_prompt: str, intent: str = "scene") -> str:
    """按目标模型规范改写图片描述。

    intent: scene(场景图) / portrait(角色立绘) / i2i(图生图)
    返回该模型最易理解、效果最佳的 prompt。
    """
    guide = get_model_guide(model_name)
    p = (base_prompt or "").strip()

    if model_name == "seedream":
        # 火山 seedream：英文摄影词 + 竖屏 + 写实增强
        # seedream 已自带 negative 控制，prompt 加写实词
        if intent == "portrait":
            return f"{p}, {_REALISTIC_BOOST_EN}, portrait photography, sharp focus"
        return f"{p}\n{_VERTICAL_EN}\n{_REALISTIC_BOOST_EN}"

    elif model_name in ("wan2.7-image-pro", "wanxiang"):
        # 阿里万相：中文为主，加竖屏构图，不加英文堆砌
        if intent == "portrait":
            return f"{p}，写实真人摄影，8K高清，自然光影，皮肤真实质感"
        return f"{p}，{_VERTICAL_CN}，写实真人摄影，8K高清，自然光影"

    elif model_name == "agnes":
        # Agnes：英文简洁
        return f"{p}, {_VERTICAL_EN}, photorealistic, 8K"

    elif model_name == "hidream":
        # 智象：中文
        return f"{p}，{_VERTICAL_CN}，写实高清"

    # 默认：中英双语
    return f"{p}\n{_VERTICAL_CN}\n{_VERTICAL_EN}"


def get_image_negative(model_name: str) -> str:
    """返回该模型适用的负向提示词（不支持负向的模型返回空）"""
    if model_name == "seedream":
        return _REALISTIC_NEGATIVE
    return ""  # 万相/agnes 等不支持 negative_prompt


# ═══════════════════════════════════════════════════════════════
# 视频 prompt 适配
# ═══════════════════════════════════════════════════════════════

def adapt_video_prompt(model_name: str, base_prompt: str, has_audio: bool = False) -> str:
    """按目标视频模型规范改写描述。

    has_audio: 是否有配音（影响口型动作描述）
    返回该视频模型最易理解的 prompt。
    """
    guide = get_model_guide(model_name)
    p = (base_prompt or "").strip()

    if model_name in ("happyhorse-r2v", "happyhorse-i2v"):
        # 百炼参考图/图生视频：简短聚焦动作，中文
        # 参考图已锁脸，prompt 不需要外貌，只写动作
        action_focus = _extract_action(p) or "角色自然表演"
        if has_audio:
            action_focus += "，角色正在说话，嘴巴张开，嘴型动作明显，嘴唇开合清晰"
        # 控制在 50 字内聚焦动作
        if len(action_focus) > 80:
            action_focus = action_focus[:80]
        return action_focus

    elif model_name == "happyhorse-t2v":
        # 文生视频：场景+动作，可稍详细
        return f"{p}，竖屏9:16，电影质感" if len(p) < 100 else p

    elif model_name == "kling":
        # 可灵：动作描写为主，简洁有力，中英均可
        action = _extract_action(p) or p
        return f"{action}, cinematic, smooth motion"

    elif model_name == "seedance":
        # 火山 seedance：画面+运镜+动作
        return f"{p}, cinematic shot, smooth camera movement, 9:16 vertical"

    elif model_name in ("wan2.7_t2v", "wan2.7_i2v"):
        # 万相视频：中文描述
        if has_audio and model_name == "wan2.7_i2v":
            return f"{p}，角色正在说话，嘴型动作夸张明显"
        return p

    return p


def _extract_action(prompt: str) -> str:
    """从完整 prompt 中提取动作描述部分（去掉环境/氛围等冗余）。
    视频 prompt 只需动作，环境由参考图提供。"""
    if not prompt:
        return ""
    # 按 | 或 。 分隔，取含动词的部分
    parts = prompt.replace("|", "。").split("。")
    # 优先选含动作词的部分
    action_words = ("走", "跑", "看", "笑", "哭", "说", "转", "抬", "握", "推", "拉", "坐", "站", "点头", "摇头", "挥手", "伸手", "皱眉", "微笑", "瞪", "咬", "攥", "颤抖")
    for part in parts:
        part = part.strip()
        if any(w in part for w in action_words) and len(part) > 3:
            return part
    # 兜底取最长部分
    parts = [p.strip() for p in parts if p.strip()]
    return max(parts, key=len) if parts else ""


# ═══════════════════════════════════════════════════════════════
# 国内外短剧差异知识库（供剧本/导演/分镜智能体参考）
# 智能体据此适配不同目标市场的短剧风格
# ═══════════════════════════════════════════════════════════════

DRAMA_KNOWLEDGE = """
【国内外短剧市场差异 — 创作时按目标市场适配】

一、国内竖屏短剧（抖音/快手/微信小程序）
1. 时长：单集1-3分钟，总集数80-100集常见
2. 节奏：极快，前3秒必须钩子，每15秒一反转，卡点留扣逼追更
3. 题材爆款：赘婿逆袭/战神回归/霸总甜宠/宫斗/重生复仇/萌宝
4. 情绪曲线：憋屈→爆发打脸→爽，情绪跨度大且密集
5. 价值观：爽感驱动、阶级跨越、善恶有报、家庭团圆
6. 合规红线：禁暴力血腥/低俗/政治敏感/封建糟粕正面化/侵权IP

二、海外短剧（TikTok/Reels/Shorts/海外竖屏App）
1. 时长：单集30秒-2分钟，更碎片化
2. 节奏：更快，前1.5秒抓眼球，视觉冲击优先于剧情复杂度
3. 题材偏好：狼人/吸血鬼等超自然、霸总(Alpha)、豪门恩怨、罪案悬疑、浪漫喜剧
4. 情绪曲线：强冲突开场，少铺垫，直接进入戏剧高潮
5. 价值观：个人主义、爱情至上、反抗权威、自我实现
6. 合规：注意种族/性别刻板印象回避，暴力尺度可略宽但需分级
7. 语言：台词英文为主，简洁口语化，避免文化梗/谐音梗

三、通用创作要点
- 无论国内外，竖屏构图9:16、主体居中、面部表情清晰
- 钩子法则通用：冲突/悬念/反常/视觉冲击开场
- 人物辨识度：标志性口头禅/动作/外貌记忆点
- 卡点留扣：每集结尾悬而未决

【目标市场适配】
若用户指定"国内"：用国内题材+快节奏+爽感曲线，中文台词
若用户指定"海外/英文"：用海外题材+碎片化+强视觉，英文台词，回避文化梗
未指定时默认国内风格
"""

