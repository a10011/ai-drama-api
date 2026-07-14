"""
模型知识库（v2.0）— 每个模型的完整细节、规格、标准、价格、适用场景

由模型管家智能体自动加载，支持运行时查询。
从 model_spec.py + PRICING + RATE 自动同步基础字段。
"""

# ═══════════════════════════════════════════════════════════════
# 完整模型知识库
# ═══════════════════════════════════════════════════════════════

MODEL_KNOWLEDGE = {
    # ──────────────────── 图片模型 ────────────────────
    "wan2.7-image-pro": {
        "name": "万相 2.7 Pro（最新旗舰）",
        "full_name": "wan2.7-image-pro",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "image",
        "description": "万相最新旗舰生图模型，替代wanx2.1。Chat格式API，支持更复杂Prompt理解",
        "capabilities": {
            "文生图": "✅ 主功能，Chat messages格式",
            "图生图": "⚠️ 待验证（multi-image messages）",
            "组合生图": "✅ 支持多条件组合生成",
            "prompt理解": "✅ 深度语义理解，优于wanx2.1",
        },
        "specs": {
            "model_id": "wan2.7-image-pro",
            "api_endpoint": "/api/v1/services/aigc/image-generation/generation",
            "api_format": "Chat-compatible (messages数组)",
            "input_example": '{"messages":[{"role":"user","content":[{"text":"prompt"}]}]}',
            "output_path": "output.choices[0].message.content[0].image",
            "max_resolution": "1024×1024（可扩展）",
            "mode": "异步提交+轮询（~25s）",
            "prompt_lang": "中文/英文",
        },
        "pricing": {"unit": "元/张", "price": 0.20, "note": "旗舰定价，比wanx2.1高约30%"},
        "rate_limit": {"concurrency": 1, "rpm": 2},
        "best_for": ["最高质量写真","复杂场景理解","替代wanx2.1的下一代"],
        "status": "✅ 已验证（2026-06-24）",
    },
    "wanxiang": {
        "name": "通义万相 2.1 Plus",
        "full_name": "wanx2.1-t2i-plus",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "image",
        "description": "阿里云顶级写实生图模型，亚洲人脸最优，原生中文Prompt支持",
        "capabilities": {
            "文生图": "✅ 主功能，支持中文/英文Prompt，最优亚洲写实人像",
            "图生图": "✅ 通过ref_img+ref_strength参数，保持面部ID换装",
            "背景替换": "⚠️ 需用wanx-background-generation-v2专有API",
            "风格重绘": "⚠️ 需用wanx-style-repaint专有API（ID锁定）",
            "局部重绘": "⚠️ 需用百炼Inpainting API",
            "多图生成": "✅ 支持n参数一次多张",
        },
        "specs": {
            "model_id": "wanx2.1-t2i-plus",
            "max_resolution": "1440×1440",
            "supported_sizes": ["1024*1024","1024*768","768*1024","720*1280","1280*720","1440*1440"],
            "mode": "异步提交+轮询（~15-60s）",
            "prompt_lang": "中文（原生）/ 英文",
            "摄影级Prompt公式": "主体+面部皮肤细节+服装+环境背景+摄影设备光影+画质词",
            "关键摄影词": "RAW photo, DSLR, 85mm, f/1.8, skin pores, peach fuzz, unretouched, cinematic lighting",
        },
        "pricing": {"unit": "元/张", "price": 0.15, "note": "百炼官方定价，按调用次数计费"},
        "rate_limit": {"concurrency": 1, "rpm": 2, "note": "异步提交限制，每分钟可提交2个任务，每个任务60s内完成"},
        "best_for": ["商业人像摄影","电影级角色设定","高质量模特图","写实风海报","亚洲面孔写真"],
        "anti_cartoon": "✅ prompt级反卡通(3层)+摄影关键词+皮肤毛孔/绒毛强调",
        "i2i_strength_range": "0.1-0.95，<0.3=大改相似低，>0.7=高度还原原图",
    },
    "seedream": {
        "name": "豆包 Seedream 5.0",
        "full_name": "doubao-seedream-4-0-250828",
        "vendor": "火山方舟 ARK",
        "type": "image",
        "description": "字节跳动顶级生图模型，支持negative prompt和photography风格",
        "capabilities": {
            "文生图": "✅ 主功能，英文Prompt，photography风格",
            "图生图": "✅ 原生支持，strength参数(0.05-0.8)，注意strength反比(越低越像原图)",
            "负向提示词": "✅ 独立negative_prompt参数",
            "风格指定": "✅ style=photography强制写实风格",
        },
        "specs": {
            "model_id": "doubao-seedream-4-0-250828",
            "max_resolution": "2048×2048",
            "mode": "同步调用（~10-30s）",
            "prompt_lang": "英文（推荐）/ 中文",
            "negative_support": "✅ 22词反卡通/反塑料",
            "style_support": "✅ photography / illustration / anime等",
        },
        "pricing": {"unit": "元/张", "price": 0.12, "note": "ARK计费，按调用次数"},
        "rate_limit": {"concurrency": 1, "rpm": 2},
        "best_for": ["英文Prompt场景","需要negative参数精细控制","同步快速出图"],
        "i2i_note": "strength值反比：0.08=最大保留原图面部(已验证)，0.55=大幅改动",
    },
    "hidream": {
        "name": "HiDream Z1",
        "full_name": "z1-image",
        "vendor": "Agnes Hub",
        "type": "image",
        "description": "第三方聚合生图模型，画质中等，性价比高",
        "capabilities": {"文生图": "✅", "图生图": "❌", "负向提示词": "❌"},
        "specs": {"model_id": "z1-image", "max_resolution": "1024×1024", "mode": "同步（~5-15s）", "prompt_lang": "英文"},
        "pricing": {"unit": "元/张", "price": 0.02, "note": "Agnes Hub聚合平台，按token计"},
        "rate_limit": {"concurrency": 2, "rpm": 10},
        "best_for": ["大量生成/快速验证","预算敏感场景","非关键质量需求"],
    },
    "agnes": {
        "name": "Agnes Flash 2.1",
        "full_name": "agnes-image-2.1-flash",
        "vendor": "Agnes Hub",
        "type": "image",
        "description": "Agnes Hub快速生图模型",
        "capabilities": {"文生图": "✅", "图生图": "❌"},
        "specs": {"model_id": "agnes-image-2.1-flash", "max_resolution": "1024×1024", "mode": "同步（~3-10s）", "prompt_lang": "英文"},
        "pricing": {"unit": "元/张", "price": 0.01},
        "rate_limit": {"concurrency": 3, "rpm": 20},
        "best_for": ["极速出图","低质量预览","大批量草稿"],
    },

    # ──────────────────── 视频模型 ────────────────────
    "happyhorse-r2v": {
        "name": "快乐马 R2V",
        "full_name": "happyhorse-1.1-r2v",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "video",
        "description": "阿里百炼图转视频模型，当前主力视频生成",
        "capabilities": {
            "图生视频": "✅ 主功能(reference_images)，640×多图",
            "文生视频": "⚠️ 不推荐（质量差），建议先文生图再图生视频",
            "运镜控制": "✅ 支持camera_movement参数（推拉摇移等）",
            "时长": "5秒(max)",
            "分辨率": "720P",
        },
        "specs": {
            "model_id": "happyhorse-1.1-r2v",
            "duration": "5s",
            "resolution": "720P (1280×720)",
            "mode": "异步提交+轮询（~60-300s）",
            "fps": 24,
            "video_ratio": "9:16 (竖屏)",
            "参考图数量": "1-6张",
            "参考图要求": "必须同尺寸+竖屏构图",
        },
        "pricing": {"unit": "元/秒", "price": 0.28, "note": "百炼按生成视频时长计费"},
        "rate_limit": {"concurrency": 1, "rpm": 1, "note": "生成慢，轮询占用，限流严格"},
        "best_for": ["短剧视频分镜","多图连续性转视频","运镜可控的影视片段"],
    },
    "seedance": {
        "name": "豆包 Seedance 2.0",
        "full_name": "doubao-seedance-2-0-260128",
        "vendor": "火山方舟 ARK",
        "type": "video",
        "description": "字节图/文生视频模型",
        "capabilities": {
            "图生视频": "✅ 支持首帧/尾帧图",
            "文生视频": "✅ 支持纯文本生成",
            "时长": "5s",
            "分辨率": "720P",
        },
        "specs": {"model_id": "doubao-seedance-2-0-260128", "duration": "5s", "resolution": "720P", "mode": "异步提交+轮询（~60-300s）"},
        "pricing": {"unit": "元/秒", "price": 0.30, "note": "ARK计费"},
        "rate_limit": {"concurrency": 1, "rpm": 1},
        "best_for": ["纯文本生成视频","首尾帧图生视频"],
    },
    "wan2.7_t2v": {
        "name": "万相 2.7 文生视频",
        "full_name": "wan2.7-t2v",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "video",
        "description": "万相最新文生视频模型，速度慢但质量上限高",
        "capabilities": {"文生视频": "✅", "图生视频": "❌", "时长": "5s", "分辨率": "720P"},
        "specs": {"model_id": "wan2.7-t2v", "duration": "5s", "resolution": "720P", "mode": "异步提交+轮询（~120-600s）"},
        "pricing": {"unit": "元/秒", "price": 0.50},
        "rate_limit": {"concurrency": 1, "rpm": 1},
        "best_for": ["纯文本高质量视频","不需要参考图的场景"],
    },
    "wan2.7_i2v": {
        "name": "万相 2.7 图生视频",
        "full_name": "wan2.7-i2v-2026-04-25",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "video",
        "description": "万相图生视频模型",
        "capabilities": {"图生视频": "✅", "文生视频": "❌", "时长": "5s", "分辨率": "720P"},
        "specs": {"model_id": "wan2.7-i2v-2026-04-25", "duration": "5s", "resolution": "720P", "mode": "异步提交+轮询（~60-300s）"},
        "pricing": {"unit": "元/秒", "price": 0.50},
        "rate_limit": {"concurrency": 1, "rpm": 1},
        "best_for": ["单图高质量视频","不依赖多图连续性"],
    },
    "kling": {
        "name": "可灵 2.6",
        "full_name": "kling-v2-6",
        "vendor": "快手可灵",
        "type": "video",
        "description": "快手顶级视频模型，服务器DNS不通(无法访问kling.kuaishou.com)",
        "status": "❌ DNS不通，暂不可用",
        "capabilities": {"图生视频": "✅", "文生视频": "✅", "时长": "5s/10s", "分辨率": "720P/1080P"},
        "specs": {"model_id": "kling-v2-6", "duration": "5s", "resolution": "720P", "mode": "异步提交+轮询"},
        "pricing": {"unit": "元/次", "price": 0.68, "note": "无实时计费API，按套餐包计"},
        "rate_limit": {"concurrency": 1, "rpm": 2},
        "best_for": ["高质量影视视频","长视频(10s)","1080P高清"],
        "known_issue": "服务器无法解析 kling.kuaishou.com DNS",
    },
    "kling-bailian": {
        "name": "可灵 (百炼代理)",
        "full_name": "kling-v1.6",
        "vendor": "阿里云百炼代理 可灵",
        "type": "video",
        "description": "通过百炼代理调用可灵1.6，避DNS问题",
        "capabilities": {"图生视频": "✅", "文生视频": "✅"},
        "specs": {"model_id": "kling-v1.6", "duration": "5s", "resolution": "720P", "mode": "异步提交+轮询（~120-300s）", "poll_interval": 10, "max_polls": 60},
        "pricing": {"unit": "元/秒", "price": 0.35},
        "rate_limit": {"concurrency": 1, "rpm": 1},
        "best_for": ["避可灵DNS问题","通过百炼统一API调用"],
    },

    # ──────────────────── LLM 模型 ────────────────────
    "deepseek-chat": {
        "name": "DeepSeek V4 Pro",
        "full_name": "deepseek-chat",
        "vendor": "DeepSeek 官方",
        "type": "llm",
        "description": "主力推理模型，128K上下文，速度快成本低",
        "capabilities": {
            "文本理解": "✅ 剧情分析/角色提取/风格判断",
            "代码生成": "✅",
            "JSON输出": "✅ 需max_tokens=8192防截断",
            "多语言": "✅ 中英",
            "上下文": "128K tokens",
            "API模式": "OpenAI兼容",
        },
        "specs": {"model_id": "deepseek-chat", "context": "128K", "mode": "同步", "api_base": "https://api.deepseek.com/v1"},
        "pricing": {"unit": "元/百万token", "input": 1.0, "output": 2.0, "note": "DeepSeek官方定价"},
        "rate_limit": {"concurrency": 5, "rpm": 30},
        "best_for": ["剧本创作(128K长文本)","角色提取","导演分析","分镜生成"],
        "known_issue": "JSON输出可能被截断需max_tokens=8192",
        "余额": "¥11.29 (2026-06-24查询)",
    },
    "qwen-max": {
        "name": "通义千问 Max",
        "full_name": "qwen-max",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "llm",
        "description": "阿里最强LLM，中文理解最优，适合复杂剧情",
        "capabilities": {"文本理解": "✅ 最优中文","JSON输出": "✅","多语言": "✅ 中英日韩","上下文": "32K","API模式": "DashScope"},
        "specs": {"model_id": "qwen-max", "context": "32K", "mode": "同步"},
        "pricing": {"unit": "元/百万token", "input": 2.0, "output": 6.0, "note": "百炼官方定价"},
        "rate_limit": {"concurrency": 3, "rpm": 20},
        "best_for": ["复杂中文推理","精细角色塑造","中文文案"],
    },
    "doubao": {
        "name": "豆包 LLM",
        "full_name": "doubao-lite-pro",
        "vendor": "火山方舟 ARK",
        "type": "llm",
        "description": "字节LLM，性价比高",
        "capabilities": {"文本理解": "✅","JSON输出": "✅","上下文": "32K","API模式": "OpenAI兼容"},
        "specs": {"model_id": "doubao-lite-pro", "context": "32K", "mode": "同步"},
        "pricing": {"unit": "元/百万token", "input": 0.3, "output": 0.6},
        "rate_limit": {"concurrency": 5, "rpm": 30},
        "best_for": ["大批量文本处理","成本敏感场景"],
    },

    # ──────────────────── TTS 模型 ────────────────────
    "cosyvoice": {
        "name": "CosyVoice 2.0",
        "full_name": "cosyvoice-v2",
        "vendor": "阿里云百炼 (DashScope)",
        "type": "tts",
        "description": "阿里语音合成，多音色可选",
        "capabilities": {"语音合成": "✅","多音色": "✅ 男/女/童多角色","语速调整": "✅","情绪": "⚠️ 有限支持","SSML": "✅"},
        "specs": {"model_id": "cosyvoice-v2", "format": "mp3/wav/pcm", "sample_rate": "48000"},
        "pricing": {"unit": "元/千字符", "price": 0.01},
        "rate_limit": {"concurrency": 2, "rpm": 10},
        "best_for": ["短剧角色配音","多角色对白","中英文合成"],
    },
    "edge-tts": {
        "name": "Edge TTS",
        "full_name": "edge-tts",
        "vendor": "Microsoft Edge",
        "type": "tts",
        "description": "免费微软语音合成，质量中等",
        "capabilities": {"语音合成": "✅","多音色": "✅ 微软标准音色","语速调整": "✅","SSML": "✅"},
        "specs": {"model_id": "edge-tts", "format": "mp3", "mode": "本地调用"},
        "pricing": {"unit": "免费", "price": 0},
        "rate_limit": {"concurrency": 3, "rpm": 30},
        "best_for": ["低成本配音","非关键对白","大量文本转语音"],
    },

    # ──────────────────── 音乐/BGM 模型 ────────────────────
    "music_api": {
        "name": "Music API (BGM)",
        "full_name": "背景音乐API",
        "vendor": "第三方音乐服务",
        "type": "bgm",
        "description": "背景音乐生成/匹配服务",
        "capabilities": {"背景音乐生成": "✅","情绪匹配": "✅","风格": "古风/现代/悬疑/温馨等"},
        "pricing": {"unit": "元/首", "price": 0.05},
        "best_for": ["短剧BGM配乐","场景情绪音乐","片头片尾"],
    },
}

# ═══════════════════════════════════════════════════════════════
# 生态链说明
# ═══════════════════════════════════════════════════════════════

ECOSYSTEM_INFO = {
    "deepseek": {
        "name": "DeepSeek 生态（主力）",
        "description": "DeepSeek LLM + 万相2.1图 + 快乐马视频 + Edge TTS",
        "chain": {
            "llm": "deepseek-chat — 128K上下文，主力推理",
            "image": "wanxiang (wanx2.1-t2i-plus) — 真人写实最优",
            "video": "happyhorse-r2v — 图转视频主力",
            "tts": "edge-tts — 免费语音合成",
            "bgm": "music_api — 背景音乐",
        },
        "monthly_budget_est": "~¥50-150（取决于使用量）",
    },
    "aliyun": {
        "name": "阿里百炼 生态（备用）",
        "description": "通义千问 LLM + 万相图 + 快乐马视频 + CosyVoice TTS",
        "chain": {
            "llm": "qwen-max — 最优中文推理",
            "image": "wanxiang (wanx2.1-t2i-plus)",
            "video": "happyhorse-r2v",
            "tts": "cosyvoice — 专业多角色配音",
            "bgm": "music_api",
        },
    },
}

# ═══════════════════════════════════════════════════════════════
# API余额
# ═══════════════════════════════════════════════════════════════

BALANCES = {
    "volcano_ark": {"balance": 270.83, "unit": "元", "updated": "2026-06-24", "note": "火山方舟（Seedream/Seedance/豆包）"},
    "aliyun_bailian": {"balance": 137.96, "unit": "元", "updated": "2026-06-24", "note": "阿里云百炼（万相/快乐马/千问/CosyVoice）"},
    "deepseek": {"balance": 11.29, "unit": "元", "updated": "2026-06-24", "note": "DeepSeek官方"},
}

# ═══════════════════════════════════════════════════════════════
# 当前管道使用的百炼图生图API详情
# ═══════════════════════════════════════════════════════════════

BAILIAN_IMAGE_APIS = {
    "wanx2.1-t2i-plus": {
        "endpoint": "/api/v1/services/aigc/text2image/image-synthesis",
        "mode": "异步提交+轮询",
        "params": {
            "prompt": "中英文Prompt",
            "size": "宽*高（*分隔，如1024*1024）",
            "n": "一次生成数量（1-4）",
            "steps": "推理步数（默认20）",
            "ref_img": "参考图URL/Base64（i2i时使用）",
            "ref_strength": "参考强度 0.1-0.95（i2i时使用）",
        },
        "timeout": "提交15s + 轮询60s（最大）",
    },
    "wanx-background-generation-v2": {
        "endpoint": "阿里云百炼 // 需单独接入",
        "description": "真人背景生成替换，光影自动融合",
        "status": "❌ 未接入（需时再加）",
        "params": "ref_img + background_prompt",
    },
    "wanx-style-repaint": {
        "endpoint": "阿里云百炼 // 需单独接入",
        "description": "人像风格重绘，锁住人脸ID",
        "status": "❌ 未接入（需时再加）",
    },
}

# ═══════════════════════════════════════════════════════════════
# 查询函数
# ═══════════════════════════════════════════════════════════════

def get_model_info(model_name: str) -> dict:
    """获取单个模型的完整信息"""
    info = MODEL_KNOWLEDGE.get(model_name, {}).copy()
    if not info:
        return {"error": f"未知模型: {model_name}", "known_models": list(MODEL_KNOWLEDGE.keys())}
    
    # 自动从 model_spec 同步运行时参数
    try:
        from services.model_spec import SPEC, RATE, PRICING
        spec = SPEC.get(model_name)
        rate = RATE.get(model_name)
        price = PRICING.get(model_name, {})
        if spec and not spec.static:
            info["_runtime"] = {
                "provider": spec.provider,
                "service": spec.service,
                "model_id": spec.model_id,
                "timeout": spec.timeout,
                "size": spec.size,
            }
        if rate:
            info["_runtime"]["rate"] = {"concurrency": rate.concurrency, "rpm": rate.rpm}
        if price and "price" in price:
            info["_runtime"]["price"] = price
    except ImportError:
        pass
    
    return info

def get_all_models() -> dict:
    """获取全部模型摘要"""
    summary = {}
    for name, info in MODEL_KNOWLEDGE.items():
        summary[name] = {
            "name": info.get("name", name),
            "vendor": info.get("vendor", ""),
            "type": info.get("type", ""),
            "description": info.get("description", ""),
            "pricing": info.get("pricing", {}),
        }
    return summary

def get_ecosystem_info(eco: str = None) -> dict:
    """获取生态链信息"""
    if eco:
        return ECOSYSTEM_INFO.get(eco, {"error": f"未知生态: {eco}"})
    return ECOSYSTEM_INFO

def get_balances() -> dict:
    return BALANCES

def get_bailian_apis() -> dict:
    return BAILIAN_IMAGE_APIS
