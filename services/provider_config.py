import json, os

# ═══════════════════════════════════════════
# 三条模型通道 — 新模型直接加配置就行
# ═══════════════════════════════════════════
PROVIDER_CONFIG = {
    'ark_volc': {   # 通道1: SD2.0 — 火山方舟 Seedance 2.0 (最强)
        'channel': 'SD 2.0',
        'name': '火山方舟 Seedance 2.0',
        'llm': 'deepseek-chat',
        'llm_long': 'deepseek-r1-256k',
        'image': 'doubao-seedream-5-0',
        'video': 'doubao-seedance-2-0-260128',
        'video_multi_ref': True,   # 支持多参考图锁脸
        'tts': 'builtin',          # 视频自带音频
        'lipsync': 'builtin',
    ,
    'agnes': {  # 通道3: AgnesAI
        'channel': 'AgnesAI',
        'name': 'AgnesAI agnes-image-2.1 + agnes-video-v2.0',
        'llm': 'agnes-2.0-flash',
        'image': 'agnes-image-2.1-flash',
        'video': 'agnes-video-v2.0',
        'video_multi_ref': True,
        'tts': 'builtin',
        'lipsync': 'builtin',
    },
},
    'ark_volc_1.5': {  # 通道1.5: SD1.5 — 火山方舟 Seedance 1.5 Pro
        'channel': 'SD 1.5',
        'name': '火山方舟 Seedance 1.5 Pro',
        'llm': 'deepseek-chat',
        'llm_long': 'deepseek-r1-256k',
        'image': 'doubao-seedream-5-0',
        'video': 'doubao-seedance-1-5-pro',
        'video_multi_ref': False,
        'tts': 'builtin',
        'lipsync': 'builtin',
    },
    'ali_bailian': {  # 通道2: 阿里百炼 — HappyHorse R2V
        'channel': 'Ali Bailian',
        'name': '阿里百炼 HappyHorse + CosyVoice',
        'llm': 'qwen-max',
        'llm_long': 'qwen-max',
        'image': 'wan2.7-image-pro',
        'video': 'happyhorse',
        'video_multi_ref': False,
        'tts': 'cosyvoice-v2',
        'lipsync': 'per-shot',
    },
}

def get_active_provider(user_id=None):
    from services.ai_providers import _get_user_key
    ukeys = _get_user_key(user_id) if callable(_get_user_key) else {}
    if isinstance(ukeys, dict) and ukeys.get('ali_bailian') and not ukeys.get('ark_volc'):
        return 'ali_bailian'
    return 'ark_volc'
