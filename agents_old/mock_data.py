"""
Mock数据池 — AI不可用时返回合理demo数据
从agent_base.py中提取，避免基类膨胀
"""
import json


def get_mock_response(system: str, user: str) -> str:
    """根据请求内容生成对应的mock JSON响应"""
    combined = system + " " + user

    if "hok" in system or "hok" in combined:
        return json.dumps({"hok": "下一个转角，命运已经等候多时", "type": "悬念"})

    if "梗概扩写" in system or "扩写" in user or "premise" in user:
        return _mock_expand()

    if "剧本优化" in system or "优化" in combined:
        return _mock_optimize()

    if "角色" in system or "人设" in system:
        if "提取" in combined or "提取角色" in combined or "深度理解" in system:
            return _mock_characters_array()
        return _mock_character()

    if "分镜" in system:
        return _mock_storyboard()

    if "场景" in system:
        return _mock_scene()

    if "视频" in system or "prompt" in user:
        return _mock_video()

    if "配音" in system or "voice" in user:
        return _mock_tts()

    if "字幕" in system:
        return _mock_subtitle()

    if "BGM" in system or "配乐" in system:
        return _mock_bgm()

    if "合成" in system:
        return _mock_composite()

    return json.dumps({"mock": True, "data": "demo", "message": "模拟数据"})


def _mock_expand() -> str:
    return json.dumps({
        "title": "AI短剧创作示例",
        "outline": "外卖小哥李明在一次送餐途中意外觉醒预知未来的能力，发现自己每次使用能力都会缩短寿命。在能力与生命的抉择中，他遇到了同有特殊能力的女主小美，两人携手揭开城市背后的神秘组织真相。",
        "episode_count": 6,
        "episodes": [
            {"num": 1, "title": "意外的觉醒", "synopsis": "李明在一次意外中获得预知能力", "hook": "那个黑衣人到底是谁？"},
            {"num": 2, "title": "能力的代价", "synopsis": "李明发现使用能力会消耗寿命", "hook": "小美也有超能力？"},
            {"num": 3, "title": "命运的相遇", "synopsis": "李明遇到同有超能力的小美", "hook": "组织已经开始行动"},
            {"num": 4, "title": "组织的阴影", "synopsis": "神秘组织盯上了两人", "hook": "最好的朋友竟是卧底"},
            {"num": 5, "title": "生死抉择", "synopsis": "李明必须在能力与生命之间选择", "hook": "小美牺牲了自己"},
            {"num": 6, "title": "最后的战斗", "synopsis": "最终决战，命运的交织", "hook": "新世界刚刚开始"},
        ],
        "key_conflicts": ["能力与寿命的博弈", "信任与背叛", "正邪对决"],
        "target_audience": "18-35岁都市奇幻爱好者",
        "tone": "热血悬疑",
    })


def _mock_optimize() -> str:
    return json.dumps({
        "issues": [
            {"type": "节奏", "location": "第二幕", "suggestion": "增加转折点", "priority": "high"},
            {"type": "冲突", "location": "第三幕", "suggestion": "加强反派动机", "priority": "medium"},
        ],
        "optimized_script": "优化后的剧本内容...",
        "hook_suggestion": "这个秘密她永远不会知道",
        "rating": 8,
        "summary": "整体节奏紧凑，建议加强第二幕冲突",
    })


def _mock_characters_array(script: str = "") -> str:
    """返回符合 extract_characters 格式的数组，动态数量"""
    # 1. 先尝试从剧本里提取角色描述行（如"角色名："、"XX："格式）
    import re
    names = set()
    # 匹配 "角色名：" 格式
    for m in re.finditer(r'([\u4e00-\u9fff]{2,4})[：:]', script):
        n = m.group(1)
        if n not in ('时间','地点','场景','镜头','旁白','字幕','音乐','音效','淡入','淡出','切','转场','开场','结尾','主题','片头','片尾'):
            names.add(n)
    count = max(len(names), 2)  # 至少2个角色
    
    # 2. 角色模板池
    templates = [
        {"name": "林悦", "type": "主角", "gender": "女", "desc": "勇敢善良", "personality": "温柔坚韧", "look": "长发清秀", "notes": "成长型"},
        {"name": "陈总", "type": "反派", "gender": "男", "desc": "权势滔天的总裁", "personality": "冷酷无情", "look": "西装革履", "notes": "商界大亨"},
        {"name": "小李", "type": "配角", "gender": "男", "desc": "忠心的助手", "personality": "幽默正直", "look": "普通青年", "notes": "男主助手"},
        {"name": "苏婉", "type": "女主", "gender": "女", "desc": "聪慧独立的女性", "personality": "独立坚强", "look": "职业装干练", "notes": "女主"},
        {"name": "王叔", "type": "长辈", "gender": "男", "desc": "慈祥的老者", "personality": "和蔼可亲", "look": "白发苍苍", "notes": "长辈"},
        {"name": "阿强", "type": "反派跟班", "gender": "男", "desc": "粗鲁的打手", "personality": "莽撞凶悍", "look": "肌肉发达", "notes": "反派手下"},
        {"name": "小芳", "type": "闺蜜", "gender": "女", "desc": "活泼的闺蜜", "personality": "开朗活泼", "look": "时尚靓丽", "notes": "女主闺蜜"},
    ]
    
    chars = []
    for i in range(min(count, len(templates))):
        t = templates[i]
        chars.append({"name": t["name"], "type": t["type"], "gender": t["gender"],
                      "description": t["desc"], "personality": t["personality"],
                      "appearance": t["look"], "role_notes": t["notes"]})
    return json.dumps({"characters": chars})

def _mock_character() -> str:
    return json.dumps({
        "name": "李明",
        "basic": {
            "gender": "男", "age": 25, "height_cm": 178,
            "body_type": "匀称", "face_shape": "瓜子脸", "hair_style": "短发",
        },
        "personality": {
            "type": "INFP", "traits": ["善良", "坚韧", "内向"],
            "motivation": "保护身边的人",
        },
        "style": {
            "clothing_style": "休闲运动",
            "color_palette": ["#4A90D9", "#2C3E50", "#ECF0F1"],
        },
        "voice": {"tone": "清亮", "speed": "normal", "emotion_range": ["喜悦", "悲伤", "愤怒"]},
        "arc": "从普通人成长为英雄",
    })


def _mock_storyboard() -> str:
    return json.dumps({
        "total_shots": 12,
        "total_duration_sec": 60,
        "shots": [
            {
                "shot_num": i + 1,
                "scene": f"场景{(i // 3) + 1}",
                "shot_type": ["远景", "中景", "近景", "特写"][i % 4],
                "camera_movement": ["固定", "推", "摇", "移"][i % 4],
                "duration_sec": 5,
                "description": f"第{i + 1}个镜头画面描述",
            }
            for i in range(12)
        ],
    })


def _mock_scene() -> str:
    return json.dumps({
        "scene_name": "都市夜景",
        "weather": "小雨",
        "lighting": "霓虹灯",
        "mood": "都市感",
    })


def _mock_video() -> str:
    return json.dumps({
        "optimized_prompt": "Cinematic shot of a city night, 4K, professional lighting",
        "negative_prompt": "blurry, low quality",
        "style": "写实",
    })


def _mock_tts() -> str:
    return json.dumps({
        "casting": [
            {"character": "李明", "voice_type": "清亮", "emotion_range": ["喜悦", "悲伤"]},
            {"character": "小美", "voice_type": "甜美", "emotion_range": ["喜悦", "悲伤"]},
        ]
    })


def _mock_subtitle() -> str:
    return json.dumps({
        "subtitles": [
            {"start_sec": 0, "end_sec": 3, "text": "你来了。"},
            {"start_sec": 3, "end_sec": 6, "text": "我一直都在等你。"},
        ]
    })


def _mock_bgm() -> str:
    return json.dumps({
        "bgm_tracks": [
            {"mood": "紧张", "bgm_style": "悬疑电子", "bgm_volume": 0.7},
            {"mood": "温情", "bgm_style": "钢琴", "bgm_volume": 0.5},
        ]
    })


def _mock_composite() -> str:
    return json.dumps({
        "ffmpeg_cmd": "ffmpeg -i input.mp4 output.mp4",
        "video_count": 3,
    })


# 前端.mock数据（对应当前CreateDrama.vue中的fallbackAgent）
FRONTEND_FALLBACKS = {
    "character_extract": {
        "characters": [
            {"name": "男主", "gender": "男", "age": "28", "personality": "待定", "outfit": "待定", "voice_type": "男主"},
            {"name": "女主", "gender": "女", "age": "22", "personality": "待定", "outfit": "待定", "voice_type": "女主"},
        ]
    },
    "storyboard_generate": {
        "total_shots": 4,
        "total_duration_sec": 20,
        "shots": [
            {"shot_num": 1, "scene": "开场", "shot_type": "远景", "camera_movement": "固定", "duration_sec": 5, "description": "开场场景", "dialogue": "", "mood": "平静", "action": ""},
            {"shot_num": 2, "scene": "发展", "shot_type": "中景", "camera_movement": "推", "duration_sec": 5, "description": "剧情发展", "dialogue": "", "mood": "紧张", "action": ""},
            {"shot_num": 3, "scene": "高潮", "shot_type": "近景", "camera_movement": "摇", "duration_sec": 5, "description": "冲突爆发", "dialogue": "", "mood": "激烈", "action": ""},
            {"shot_num": 4, "scene": "结局", "shot_type": "特写", "camera_movement": "移", "duration_sec": 5, "description": "尘埃落定", "dialogue": "", "mood": "温情", "action": ""},
        ],
    },
    "dub_assign": {
        "casting": [
            {"character": "男主", "voice_type": "男主", "emotion": "平静", "speed": 1.0},
            {"character": "女主", "voice_type": "女主", "emotion": "平静", "speed": 1.0},
        ],
    },
    "bgm_match": {
        "bgm_tracks": [
            {"mood": "平静", "bgm_style": "轻音乐", "bgm_volume": 0.5, "bgm_description": "轻柔钢琴曲"},
            {"mood": "紧张", "bgm_style": "悬疑", "bgm_volume": 0.7, "bgm_description": "悬疑电子音"},
            {"mood": "热血", "bgm_style": "摇滚", "bgm_volume": 0.8, "bgm_description": "激昂摇滚"},
        ],
    },
    "subtitle_generate": {
        "subtitles": [],
        "total_duration_sec": 60.0,
        "format": "SRT",
    },
    "composite": {
        "duration": 60,
        "file_size": "120MB",
        "url": "/result.mp4",
    },
}
