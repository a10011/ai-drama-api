"""
Hermes Safety Filter — 文本安全清洗模块
所有 Agent 共享：高危词替换、IP 角色清洗、暴力弱化
"""
import re
import logging
from .safety_audit import log_event

logger = logging.getLogger(__name__)

# 高危词 → 安全替换映射
RISKY_MAP = {
    # 暴力
    "杀死": "击败",
    "杀掉": "击退",
    "杀人": "伤及",
    "死亡": "陨落",
    "尸体": "遗蜕",
    "尸体": "身骸",
    "尸横遍野": "遍地长眠",
    "血": "赤",
    "流血": "受伤",
    "鲜血": "赤流",
    "吐血": "咳血",
    "伤口": "伤势",
    "刀伤": "刃痕",
    "砍": "劈",
    "斩首": "击败",
    "掐死": "制住",
    "勒死": "困住",
    "捅": "刺",
    "鞭打": "责罚",
    "酷刑": "审问",
    # 惨烈
    "惨叫": "惊呼",
    "哀嚎": "低吟",
    "悲鸣": "叹息",
    "痛苦": "煎熬",
    "折磨": "考验",
    "破碎": "碎裂",
    "支离破碎": "分崩离析",
    # 恐怖
    "鬼": "灵",
    "怨灵": "残念",
    "厉鬼": "凶灵",
    "冤魂": "执念",
    "地狱": "深渊",
    "恶鬼": "凶煞",
}

# IP 角色名黑名单（需完全替换）
IP_NAMES = [
    "唐三", "萧炎", "林动", "秦羽", "纪宁", "石昊", "楚风", "叶凡",
    "张小凡", "韩立", "王林", "白小纯", "秦问天", "牧尘", "周元",
    "孙悟空", "唐僧", "猪八戒", "白素贞", "许仙", "范闲", "言冰云",
    "哈利", "赫敏", "伏地魔", "钢铁侠", "美国队长", "蜘蛛侠",
    "漩涡鸣人", "宇智波佐助", "路飞", "索隆",
]


def clean_text(text: str, replace_ips: bool = True) -> str:
    """清洗剧本/台词中的风险内容"""
    if not text:
        return text
    
    # 1. 高危词替换
    for risky, safe in RISKY_MAP.items():
        text = text.replace(risky, safe)
    
    # 2. IP 角色替换
    if replace_ips:
        for ip_name in IP_NAMES:
            text = re.sub(ip_name, f"影{hash(ip_name) % 100:02d}", text)
    
    # 3. 弱化极端描述
    text = re.sub(r"[！!]{3,}", "！", text)
    text = re.sub(r"[。.]{4,}", "……", text)
    
    return text


def clean_prompt(prompt: str, scene_type: str = "") -> str:
    """清洗画面提示词（让 AI 生图不违规）"""
    # 先通用清洗
    prompt = clean_text(prompt, replace_ips=False)
    
    # 画面特殊清洗
    prompt = prompt.replace("血腥", "柔和")
    prompt = prompt.replace("恐怖", "神秘")
    prompt = prompt.replace("阴森", "幽暗")
    prompt = prompt.replace("狰狞", "严肃")
    
    # 加入风格安全暗示
    safe_suffix = "柔和光影，电影级画面，无暴力元素，无恐怖元素"
    if scene_type in ("打斗", "战斗", "战争"):
        safe_suffix = "动作场面，但无血迹无暴力特写"
    elif scene_type in ("死亡", "悲伤"):
        safe_suffix = "悲伤氛围，画面克制唯美"
    
    prompt = f"{prompt}。{safe_suffix}"
    return prompt


def clean_character_name(name: str) -> str:
    """替换 IP 角色名为通用名"""
    for ip_name in IP_NAMES:
        if ip_name in name:
            return f"影{hash(name) % 100:02d}"
    return name


NEGATIVE_PROMPT = (
    "NOT cartoon, NOT anime, NOT 3D render, NOT CG, "
    "NOT illustration, NOT painting style, "
    "无二次元, 无动漫风格, 无卡通渲染, 无过度美化, 无夸张建模, "
    "no blood, no gore, no violence close-up"
)
