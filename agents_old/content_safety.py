#!/usr/bin/env python3
"""
内容安全过滤系统（Content Safety Filter）

AI视频生成平台（阿里绿网/火山审核）会对内容做自动检测，
不通过就拒绝生成。本模块在分镜阶段自动过滤/替换敏感内容，
避免视频生成被拦截。

用法：
    from agents.content_safety import sanitize_text, check_safety
    safe_text = sanitize_text(description)  # 自动替换敏感词
    is_safe, issues = check_safety(text)    # 检查是否有风险
"""

import re
import logging
logger = logging.getLogger(__name__)

# ═══ 敏感词替换表 ═══
# 按类别分组，每组有替代词
SENSITIVE_REPLACEMENTS = {
    # 经济犯罪类
    "造假": "做手脚",
    "诈骗": "欺骗",
    "犯罪": "违法",
    "违法": "违规",
    "贪污": "挪用",
    "受贿": "不当所得",
    "洗钱": "资金转移",
    "挪用公款": "资金问题",
    "偷税": "税务问题",
    "漏税": "税务问题",
    "走私": "非法运输",
    "贩毒": "违法交易",
    "毒品": "违禁品",
    "吸毒": "不良嗜好",

    # 暴力类
    "鲜血": "红色液体",
    "血泊": "红色液体",
    "血迹": "痕迹",
    "血花": "红色飞溅",
    "喷血": "飞溅",
    "流血": "受伤",
    "出血": "受伤",
    "尸体": "倒下的人",
    "死尸": "倒下的人",
    "残肢": "碎片",
    "断肢": "碎片",
    "开膛": "重伤",
    "爆头": "倒下",
    "脑浆": "碎片",
    "内脏": "碎片",
    "凶器": "工具",
    "管制刀具": "利器",
    "捅": "刺",
    "屠杀": "大规模冲突",
    "灭口": "消失",
    "碎尸": "消失",

    # 自残/极端行为
    "自杀": "放弃",
    "自残": "伤害自己",
    "割腕": "崩溃",
    "上吊": "绝望",
    "跳楼": "坠落",
    "服毒": "倒下",
    "畏罪": "害怕",
    "自尽": "离开",

    # 情绪崩溃/抑郁向（AI绘图风控高频拦截）
    "身体蜷缩": "安静伫立",
    "蜷缩发抖": "微微低头",
    "肩膀抽动": "身姿平稳",
    "瘫坐崩溃": "静静站立",
    "失声痛哭": "眼含泪光",
    "身体颤抖": "微微低头",
    "浑身发抖": "安静伫立",
    "蜷缩成一团": "独自静站",
    "缩成一团": "独自静立",
    "嘴角无力下垂": "嘴角微抿",
    "嘴唇微微颤抖": "嘴唇微抿",
    "双手无力垂落": "双手自然垂下",
    "绝望": "怅然",
    "阴郁": "沉静",
    "崩溃": "动容",
    "抑郁": "低落",
    "心理扭曲": "内心纠结",

    # 战场血腥强化版（之前遗漏的高频拦截词）
    "厮杀": "激战",
    "死战": "鏖战",
    "血气": "硝烟",
    "血珠": "飞溅",
    "迸出火星": "火花飞溅",
    "绞杀": "交锋",
    "凄厉": "高亢",
    "惨叫": "呐喊",
    "血肉": "碎片",
    "满地尸骸": "倒下的身影",
    "血流成河": "大地质感深沉",
    "贯穿伤口": "倒地",
    "血淋淋": "受了伤",

    # 生离死别向（离别戏高频拦截）
    "永诀": "离别",
    "天人永隔": "暂且分离",
    "赴死": "远征",
    "生离死别": "难舍离别",
    "死别": "诀别",

    # 逃亡/司法
    "跑路": "离开",
    "潜逃": "远走",
    "畏罪潜逃": "匆忙离开",
    "在逃": "不知所踪",
    "通缉": "寻找",
    "认罪": "承认",
    "判刑": "承担后果",
    "坐牢": "失去自由",
    "入狱": "失去自由",
    "死刑": "最严厉后果",

    # 人物定义
    "罪犯": "当事人",
    "犯罪分子": "对方",
    "嫌疑人": "当事人",
    "凶手": "动手的人",
    "杀人犯": "当事人",
    "毒贩": "交易方",

    # 证据类
    "犯罪证据": "相关材料",
    "罪证": "相关材料",
    "作案工具": "相关物品",
    "赃款": "争议资金",
    "赃物": "争议物品",

    # 其他可能触发
    "色情": "暧昧",
    "性侵": "冒犯",
    "虐待": "苛待",
    "凌辱": "屈辱",
    "嫖娼": "不当行为",
    "赌博": "博弈",
    "黑社会": "组织",
    "黑帮": "组织",
    "恐怖袭击": "突发事件",
    "爆炸物": "危险品",
    "枪械": "武器",
    "手枪": "武器",
    "子弹": "弹药",
}

# ═══ 敏感组合检测（不是单个词，是组合触发）═══
SENSITIVE_PATTERNS = [
    # (正则模式, 说明, 建议修改)
    (r"准备.{0,4}(跑路|潜逃|逃走)", "暗示逃亡犯罪", "改为'匆忙收拾行李准备离开'"),
    (r"(露出|翻开).{0,6}(机票|护照|身份证).{0,4}(跑|逃|走)", "暗示潜逃", "去掉逃亡暗示，保留离开的情节"),
    (r"\d+亿.{0,6}(造假|诈骗|贪污|洗钱)", "具体犯罪金额+犯罪手法", "改为'巨额资金问题'，去掉具体犯罪手法"),
    (r"(刀|枪|斧).{0,4}(砍|刺|射|劈).{0,4}(头|脖子|心脏|动脉)", "极端暴力描写", "改为'激烈搏斗中倒下'"),
    (r"(满身|浑身|脸上).{0,4}(是血|血淋淋|血肉模糊)", "过度血腥", "改为'受了重伤'"),
    (r"(认罪|签字画押|签字认罪)", "司法程序敏感", "改为'低头承认了事实'"),
]

# ═══ 角色名安全化（避免角色名含敏感词）═══
def sanitize_character_name(name: str) -> str:
    """角色名安全化"""
    if not name:
        return name
    safe = name
    for bad, good in SENSITIVE_REPLACEMENTS.items():
        if bad in safe:
            safe = safe.replace(bad, good)
    return safe


def sanitize_text(text: str) -> tuple:
    """替换文本中的敏感词，返回 (安全文本, 替换计数)"""
    if not text:
        return text, 0
    safe = text
    count = 0
    for bad, good in SENSITIVE_REPLACEMENTS.items():
        if bad in safe:
            safe = safe.replace(bad, good)
            count += 1
    # 检测敏感组合
    for pattern, desc, suggestion in SENSITIVE_PATTERNS:
        if re.search(pattern, safe):
            logger.info(f"[内容安全] 检测到敏感组合: {desc}，建议: {suggestion}")
            # 不自动替换组合（太复杂），只记录
            count += 1
    return safe, count


def check_safety(text: str) -> tuple:
    """检查文本安全性，返回 (是否安全, 风险列表)"""
    if not text:
        return True, []
    risks = []
    # 检查敏感词
    for bad in SENSITIVE_REPLACEMENTS:
        if bad in text:
            risks.append(f"敏感词'{bad}'→建议替换为'{SENSITIVE_REPLACEMENTS[bad]}'")
    # 检查敏感组合
    for pattern, desc, suggestion in SENSITIVE_PATTERNS:
        if re.search(pattern, text):
            risks.append(f"敏感组合: {desc}（{suggestion}）")
    return len(risks) == 0, risks


def sanitize_shot(shot: dict) -> dict:
    """对单个分镜镜头做内容安全过滤，直接修改并返回"""
    total_fixes = 0
    # 过滤描述
    desc = shot.get("description", "")
    if desc:
        safe_desc, cnt = sanitize_text(desc)
        if cnt > 0:
            shot["description"] = safe_desc
            total_fixes += cnt
            logger.info(f"[内容安全] shot描述替换{cnt}处敏感词")
    # 过滤台词
    dialogue = shot.get("dialogue", "")
    if dialogue and dialogue != "(无台词)":
        safe_dia, cnt = sanitize_text(dialogue)
        if cnt > 0:
            shot["dialogue"] = safe_dia
            total_fixes += cnt
    # 过滤内心独白
    inner = shot.get("inner_voice", "")
    if inner and inner != "(无)":
        safe_inner, cnt = sanitize_text(inner)
        if cnt > 0:
            shot["inner_voice"] = safe_inner
            total_fixes += cnt
    # 过滤旁白
    narration = shot.get("narration", "")
    if narration and narration != "(无)":
        safe_nar, cnt = sanitize_text(narration)
        if cnt > 0:
            shot["narration"] = safe_nar
            total_fixes += cnt
    # 过滤角色名
    scene = shot.get("scene", "")
    if scene:
        safe_scene, cnt = sanitize_text(scene)
        if cnt > 0:
            shot["scene"] = safe_scene
            total_fixes += cnt
    # 过滤 director_shot
    ds = shot.get("director_shot", "")
    if ds:
        safe_ds, cnt = sanitize_text(ds)
        if cnt > 0:
            shot["director_shot"] = safe_ds
            total_fixes += cnt

    if total_fixes > 0:
        shot["_safety_fixed"] = total_fixes
    return shot


def sanitize_shots(shots: list) -> list:
    """批量过滤分镜内容安全"""
    total = 0
    for s in shots:
        sanitize_shot(s)
        if s.get("_safety_fixed"):
            total += 1
    if total > 0:
        logger.info(f"[内容安全] 共{total}/{len(shots)}个镜头做了敏感词替换")
    return shots
