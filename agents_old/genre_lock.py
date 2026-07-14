#!/usr/bin/env python3
"""
类型锁定系统（Genre Lock）

导演确定类型后，所有智能体必须遵守的类型约束。
就像真实剧组：导演说是古装战争片，服化道/摄影/后期就不能搞出现代元素。

用法：
    from agents.genre_lock import get_genre_constraint, apply_genre_lock
    constraint = get_genre_constraint("古装")  # 返回该类型的约束规则
    prompt = apply_genre_lock(prompt, "古装")   # 给 prompt 加类型约束
"""

# ═══ 类型约束规则库 ═══
# 每种类型定义：允许的元素、禁止的元素、视觉风格、服装约束
GENRE_RULES = {
    # ─── 古装/历史/战争类 ───
    "古装": {
        "cn_name": "古装剧",
        "era": "中国古代（先秦至明清）",
        "costume": "中国古代服饰：汉服、铠甲、官服、盔甲、兜鍪、红缨、披风",
        "architecture": "古代建筑：城墙、宫殿、营帐、木结构建筑、青砖瓦房",
        "weapons": "古代冷兵器：刀、枪、剑、戟、弓箭、斧、锤、矛",
        "forbidden": [
            "现代服装（西装、T恤、牛仔裤、运动鞋）",
            "现代物品（手机、汽车、手表、眼镜、拉链）",
            "现代建筑（水泥楼、玻璃幕墙、公路）",
            "现代武器（枪、炮、坦克）",
            "西方人面孔、外国人",
            "西式板甲、欧式鸢盾、西洋长矛、中世纪骑士",
            "欧式城堡、十字军旗、西式栅栏、欧式堡垒",
            "西式战马马面铁甲、罗马头盔、中世纪头盔",
            "medieval knight, plate armor, european shield, crusader, castle, western warhorse",
        ],
        "warfare_hint": "中式古战场：大宋/大唐/大明重甲骑兵，玄铁札甲、明光铠、山纹甲、中式兽面护颈，环首刀、陌刀、雁翎长刀、汉式长戈，中式虎头圆盾，中原战马配皮质马铠，残破中式军旗（刺绣龙纹/玄鸟/白虎，写汉字唐宋卫），青铜战鼓，远处中式城关烽火台，黄土古漠边关",
        "visual_style": "中式写实影视，国产古战场纪录片，水墨厚重国风写实，张艺谋式古代战场镜头，色调土黄/赭石/暗红/青黑铁甲，中式战场氛围，远景加烽火台、土城关隘、连绵黄土边关",
        "face_constraint": "所有角色必须是中国古代人面孔，东方人长相",
    },
    "战争": {
        "cn_name": "古代战争剧",
        "era": "中国古代战场",
        "costume": "古代军装：铠甲、战袍、头盔、护甲、军旗",
        "architecture": "战场、城墙、军营、烽火台",
        "weapons": "冷兵器：长枪、大刀、弓箭、战马、战车",
        "forbidden": [
            "现代战争元素（枪炮、坦克、飞机）",
            "现代军装、现代装备",
            "现代建筑",
            "西方人面孔",
            "西式板甲、欧式鸢盾、中世纪骑士、十字军旗",
            "欧式城堡、西式堡垒、罗马头盔",
            "medieval knight, plate armor, european shield, crusader, castle",
        ],
        "warfare_hint": "中式古战场：大唐/大明重甲骑兵，玄铁札甲、明光铠，环首刀、陌刀、汉式长戈，中原战马，残破中式军旗（刺绣龙纹，写汉字），青铜战鼓，烽火台，黄土边关",
        "visual_style": "中式写实影视，国产古战场纪录片，张艺谋式古代战场镜头，色调土黄/赭石/暗红/青黑铁甲，参考《赤壁》《英雄》，中式战场氛围",
        "face_constraint": "所有角色必须是中国古代武将面孔",
        "scale_hint": "战争场面必须有大规模感：千军万马、旌旗蔽日、铺满画面",
    },
    "仙侠": {
        "cn_name": "仙侠剧",
        "era": "中国古代仙侠世界",
        "costume": "飘逸仙袍、道袍、法衣、佩剑、玉佩",
        "architecture": "仙山楼阁、洞府、云海、古风建筑",
        "weapons": "仙剑、法宝、符箓、灵器",
        "forbidden": ["现代所有元素", "西方奇幻元素（骑士、法师袍）"],
        "visual_style": "唯美飘逸，仙气缭绕，特效华丽，色调清雅或玄幽",
        "face_constraint": "中国古代人面孔，仙风道骨",
    },
    "武侠": {
        "cn_name": "武侠剧",
        "era": "中国古代江湖",
        "costume": "江湖侠客装：劲装、长袍、斗笠、佩剑",
        "architecture": "客栈、竹林、山崖、古镇、寺庙",
        "weapons": "刀剑、暗器、长鞭、拳脚",
        "forbidden": ["现代所有元素", "火器"],
        "visual_style": "写意江湖，水墨风格，竹林/月色/飞檐，参考《卧虎藏龙》",
        "face_constraint": "中国古代人面孔",
    },
    "宫廷": {
        "cn_name": "宫廷剧",
        "era": "中国古代宫廷",
        "costume": "华贵宫装、龙袍、凤冠、朝服、锦缎",
        "architecture": "皇宫、殿宇、御花园、朱墙黄瓦",
        "forbidden": ["现代所有元素", "西方人面孔"],
        "visual_style": "华丽精致，金碧辉煌，高饱和色调，参考《甄嬛传》",
        "face_constraint": "中国古代人面孔",
    },

    # ─── 现代类 ───
    "商战": {
        "cn_name": "商战剧",
        "era": "现代中国商业都市",
        "costume": "现代商务服装：西装、职业装、衬衫、领带、高跟鞋、公文包",
        "architecture": "现代写字楼、豪华办公室、会议室、商务酒店、城市CBD",
        "weapons": "无武器（现代商战是商业博弈，不是打斗）",
        "forbidden": [
            "古代元素（铠甲、冷兵器、古代建筑）",
            "古装服饰",
            "物理打斗/战争场面",
            "西方人面孔",
            "盔甲、战袍、兵器",
        ],
        "visual_style": "现代商务质感，干净明亮，冷色调（蓝灰）体现商业冷酷，参考《猎场》《大军师司马懿》现代线",
        "face_constraint": "现代中国人面孔，商务精英气质",
    },
    "职场": {
        "cn_name": "职场剧",
        "era": "现代中国职场",
        "costume": "现代职场服装：商务休闲、衬衫、西裤、职业裙装",
        "architecture": "办公室、会议室、写字楼、咖啡厅",
        "forbidden": ["古代元素", "物理暴力", "盔甲兵器"],
        "visual_style": "现代职场质感，明亮干净",
        "face_constraint": "现代中国人面孔",
    },
    "都市": {
        "cn_name": "都市剧",
        "era": "现代中国都市",
        "costume": "现代服装：西装、连衣裙、休闲装、职业装",
        "architecture": "现代城市：写字楼、公寓、商场、咖啡厅、街道",
        "forbidden": [
            "古代元素（铠甲、冷兵器、古代建筑）",
            "古装服饰",
            "古代场景",
        ],
        "visual_style": "现代都市质感，明亮/时尚/干净，参考现代都市剧",
        "face_constraint": "现代中国人面孔",
    },
    "甜宠": {
        "cn_name": "甜宠剧",
        "era": "现代",
        "costume": "现代时尚服装，女主可爱/清新，男主帅气/干练",
        "forbidden": ["古代元素", "暴力血腥", "恐怖元素"],
        "visual_style": "明亮温暖，粉色调/暖光，柔焦，浪漫氛围",
        "face_constraint": "现代中国人面孔，颜值高",
    },
    "悬疑": {
        "cn_name": "悬疑剧",
        "era": "现代",
        "costume": "现代服装，偏暗色调",
        "forbidden": ["古代元素", "超自然元素（除非是灵异悬疑）"],
        "visual_style": "冷色调，暗光，高对比，紧张氛围",
        "face_constraint": "现代中国人面孔",
    },
}

# 类型别名（用户可能输入的不同写法）
GENRE_ALIASES = {
    "历史": "古装", "古代": "古装", "古风": "古装",
    "玄幻": "仙侠", "修真": "仙侠",
    "江湖": "武侠", "功夫": "武侠",
    "宫斗": "宫廷",
    "商战": "商战", "商业": "商战", "职场": "职场", "打工": "职场", "创业": "商战", "总裁": "商战", "霸总": "甜宠",
    "现代": "都市", "都市言情": "甜宠", "霸总": "甜宠",
    "惊悚": "悬疑", "推理": "悬疑",
    "赘婿": "都市", "战神": "都市", "重生": "都市",
}


def normalize_genre(genre: str) -> str:
    """归一化类型名称"""
    if not genre:
        return ""
    g = genre.strip()
    # 直接匹配
    if g in GENRE_RULES:
        return g
    # 别名匹配
    if g in GENRE_ALIASES:
        return GENRE_ALIASES[g]
    # 模糊匹配（类型包含关键词）
    g_lower = g.lower()
    for key in GENRE_RULES:
        if key in g:
            return key
    for alias, target in GENRE_ALIASES.items():
        if alias in g:
            return target
    return ""


def get_genre_constraint(genre: str) -> dict:
    """获取某类型的约束规则"""
    normalized = normalize_genre(genre)
    if not normalized:
        return {}
    return GENRE_RULES.get(normalized, {})


def build_genre_lock_prompt(genre: str) -> str:
    """构建类型锁定 prompt 片段，加到所有生图/生视频的 prompt 里"""
    constraint = get_genre_constraint(genre)
    if not constraint:
        return ""

    parts = []
    cn = constraint.get("cn_name", genre)
    era = constraint.get("era", "")
    parts.append(f"【类型锁定：{cn}，背景设定为{era}】")

    if constraint.get("face_constraint"):
        parts.append(constraint["face_constraint"])

    if constraint.get("costume"):
        parts.append(f"服装：{constraint['costume']}")

    if constraint.get("architecture"):
        parts.append(f"建筑/场景：{constraint['architecture']}")

    if constraint.get("weapons"):
        parts.append(f"武器/道具：{constraint['weapons']}")

    if constraint.get("visual_style"):
        parts.append(f"视觉风格：{constraint['visual_style']}")

    if constraint.get("scale_hint"):
        parts.append(f"规模要求：{constraint['scale_hint']}")

    forbidden = constraint.get("forbidden", [])
    if forbidden:
        parts.append("严禁出现：" + "、".join(forbidden[:5]))

    return "，".join(parts)


def apply_genre_lock(prompt: str, genre: str) -> str:
    """给现有 prompt 追加类型锁定约束"""
    lock = build_genre_lock_prompt(genre)
    if not lock:
        return prompt
    return f"{prompt}。{lock}" if prompt else lock
