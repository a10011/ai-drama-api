#!/usr/bin/env python3
"""
场景智能匹配系统（Scene Intelligence）

每个镜头根据内容自动匹配：
- 场景类型 → 环境音效
- 动作类型 → 动作音效 + 物理细节
- 角色状态 → 表情 + 肢体语言
- 情绪 → 语气 + 微表情

用法：
    from agents.scene_intelligence import enhance_shot
    shot = enhance_shot(shot)  # 自动补全音效、表情、语气、动作细节
"""

# ═══ 场景类型 → 环境音效 ═══
SCENE_SOUNDSCAPE = {
    # 战争/古装
    "战场": {"env": "呼啸的风声、旗帜猎猎作响、远处闷雷般战鼓", "mood": "肃杀"},
    "冲锋": {"env": "马蹄轰鸣如雷、铁甲碰撞哗哗声、呐喊由远及近", "mood": "激昂"},
    "攻城": {"env": "巨木撞城门的沉闷轰响、石块碎裂声、弓弦嗡鸣", "mood": "惨烈"},
    "宫殿": {"env": "空旷的回声、丝绸摩擦窸窣、远处编钟低鸣", "mood": "压抑威严"},
    "军营": {"env": "篝火噼啪、兵器磨砺声、低语交谈、马匹嘶鸣", "mood": "紧张"},
    "竹林": {"env": "竹叶沙沙、风穿林梢、远处鸟鸣、溪水潺潺", "mood": "清幽"},
    "客栈": {"env": "杯碗碰撞、嘈杂交谈、木门吱呀、楼梯咯吱", "mood": "市井"},

    # 都市/现代
    "办公室": {"env": "键盘敲击声、电话铃、空调低嗡、翻纸声", "mood": "压抑忙碌"},
    "会议室": {"env": "空调低嗡、投影仪嗡嗡、偶尔咳嗽、笔尖敲桌", "mood": "紧张对峙"},
    "咖啡厅": {"env": "咖啡机嘶嘶、轻柔爵士乐、杯碟轻碰、低声交谈", "mood": "温暖暧昧"},
    "便利店": {"env": "冰柜嗡嗡、收银滴滴声、自动门叮咚、冷柜风扇", "mood": "日常孤独"},
    "雨夜街头": {"env": "雨滴打伞啪嗒、车轮碾水哗啦、远处警笛、风灌巷道", "mood": "凄凉"},
    "车内": {"env": "引擎低吼、转向灯嘀嗒、雨刮器刮水声、座椅皮革", "mood": "私密紧张"},
    "医院": {"env": "心电监护滴滴、走廊脚步回响、远处呼叫铃、器械碰撞", "mood": "冰冷恐惧"},
    "法庭": {"env": "法槌敲击、纸页翻动、椅子咯吱、低沉宣读", "mood": "庄重紧张"},

    # 仙侠/玄幻
    "仙山": {"env": "空灵风铃声、云海翻涌的虚音、鹤鸣、灵泉叮咚", "mood": "超脱空灵"},
    "洞府": {"env": "水滴回响、灵气嗡鸣、石壁回声、远处龙吟", "mood": "神秘古老"},
    "幻境": {"env": "扭曲的回声、碎片化音效、虚幻风声、心跳放大", "mood": "迷幻不安"},
}

# ═══ 动作类型 → 动作音效 + 物理细节 ═══
ACTION_FX = {
    "刀剑劈砍": {"sound": "金属破空嗖声→碰撞铿锵→火星迸溅滋滋→弹开震动", "physics": "砍击者身体前倾发力，被砍方侧身格挡，碰撞瞬间两人手臂都因反震微颤"},
    "长枪刺击": {"sound": "枪尖破空尖锐声→刺入闷响→铠甲碎裂咔嚓", "physics": "刺击者弓步前冲，枪杆弯曲蓄力，刺中后枪身颤动，被刺者身体后仰"},
    "弓箭射击": {"sound": "弓弦嗡鸣嘣→箭矢破空嗖嗖→箭中目标噗噗", "physics": "弓弦回弹震动，箭矢飞行轨迹微弯，中箭时目标身体一震"},
    "骑马冲锋": {"sound": "马蹄如鼓由远及近→马鼻喷气呼呼→铠甲哗哗碰撞", "physics": "马背颠簸起伏，骑手大腿夹紧马腹，上身前倾减小风阻，武器高举"},
    "马匹摔倒": {"sound": "马蹄打滑→马身砸地轰响→马嘶鸣→铠甲撞地闷响→尘土沙沙", "physics": "前蹄绊住→马身前倾栽倒→骑手惯性飞出→空中翻滚→肩着地→翻滚2圈"},
    "拳脚搏斗": {"sound": "拳头破风→击中闷响噗→骨头咔嚓→喘息呼哧", "physics": "出拳者转腰送肩，击中时拳头回弹，被击者头部被打偏，踉跄后退"},
    "摔跤倒地": {"sound": "衣物摩擦沙沙→身体撞地嘭→翻滚沙沙→呻吟", "physics": "失去平衡→手抓空→臀部或肩先着地→惯性翻滚→面朝下趴着"},
    "奔跑追逐": {"sound": "脚步咚咚急促→喘息声→衣物猎猎→踩碎东西咔嚓", "physics": "身体前倾，手臂大幅摆动，脚掌用力蹬地，头发/衣角向后飞"},
    "开门关门": {"sound": "门轴吱呀→门板碰框嘭→锁舌咔嗒", "physics": "手掌推门板，门缓缓/猛地打开，门框震一下"},
    "摔东西": {"sound": "物体破空→撞击/碎裂哗啦→弹跳滚动", "physics": "手臂挥出→物体脱手旋转→撞击碎裂→碎片四散弹跳"},
    "签字盖章": {"sound": "笔尖沙沙→翻纸哗啦→印章咔嗒→盖印闷响", "physics": "手指握笔运力，手腕带动笔尖游走，另一手按纸，盖章时用力下压"},
    "手机操作": {"sound": "屏幕触摸嘀嘀→消息提示叮→键盘哒哒", "physics": "拇指快速滑动屏幕，手指轻触点击，表情凝视屏幕"},
    "倒水喝茶": {"sound": "水壶倾倒哗哗→杯中水波→瓷器轻碰叮→咽下咕咚", "physics": "手腕翻转倾倒，热气升腾，双手捧杯取暖，小口啜饮"},
    "雨中行走": {"sound": "脚步踩水啪啪→雨打伞面噼啪→远处车流", "physics": "脚步小心避开水坑，伞面被雨滴砸出凹坑，肩膀微缩御寒"},
    "开车": {"sound": "引擎轰鸣→转向灯嘀嗒→刹车嘶嘶→安全带咔嗒", "physics": "手握方向盘，脚踩油门/刹车，身体因加减速前后晃动"},
}

# ═══ 情绪 → 表情 + 肢体语言 + 语气 ═══
EMOTION_EXPRESSION = {
    "愤怒": {
        "face": "眉骨压低紧锁，双目圆睁眼白充血，咬紧牙关腮帮鼓起，鼻翼扩张，面部肌肉抽搐",
        "body": "双拳紧握指节发白，胸口剧烈起伏，脖子青筋暴起，身体前倾像要扑出去",
        "voice": "音量陡然升高，吐字一字一顿像砸钉子，气息粗重，尾音可能破裂",
    },
    "愤怒压抑": {
        "face": "嘴角微微下压似笑非笑，眼神冰冷不眨，喉结缓缓滚动，太阳穴跳",
        "body": "手指慢慢攥紧又松开，肩膀端起不放下，呼吸刻意放缓但胸口仍在抖",
        "voice": "刻意压低声线，用气声说话，每个字都像从牙缝挤出，反而比吼叫更瘆人",
    },
    "悲伤": {
        "face": "眼眶泛红，泪水在眼眶打转却不落下，嘴唇微微颤抖，嘴角无力下垂",
        "body": "双手无力垂落，肩膀轻微抽动，身体蜷缩，像想把自己缩成一团",
        "voice": "声线发颤带哭腔，语速变慢，气息断续，说到最后可能哽咽失声",
    },
    "悲伤隐忍": {
        "face": "猛地别过头不看人，死命咬住下唇，眼眶通红但硬忍着不眨眼",
        "body": "手指掐进掌心，肩膀僵硬不动，整个身体像被冻住",
        "voice": "声音发抖但拼命控制，鼻腔共鸣加重，每句话末尾吞咽一次",
    },
    "紧张": {
        "face": "瞳孔微缩，额头渗出细密汗珠，喉结上下滚动，眼神快速左右扫视",
        "body": "手指不自觉敲桌面或攥衣角，脚尖点地，坐立不安，频繁吞咽",
        "voice": "语速加快，声音发紧偏高，气息短促，句子之间频繁停顿换气",
    },
    "恐惧": {
        "face": "瞳孔骤然放大，面色煞白血色全无，嘴唇哆嗦说不出话，冷汗顺着鬓角滑下",
        "body": "本能后退，双手护在胸前或挡在面前，膝盖发软，腿不听使唤",
        "voice": "声音发不出来或变成尖叫，气息紊乱，可能发出呜咽",
    },
    "震惊": {
        "face": "瞳孔猛缩后放大，嘴巴微张忘记合上，眉头先扬后锁，整个人像被定住",
        "body": "手中东西可能掉落，身体僵在原地不动，呼吸骤停一秒",
        "voice": "先是一瞬间的沉默，然后可能是气声'什么？'或一句话说不完整",
    },
    "温柔": {
        "face": "眉眼舒展微微弯起，嘴角带若有若无的笑意，目光柔软专注",
        "body": "身体微微前倾靠近，动作放缓变轻，手部动作温柔",
        "voice": "声线放低变柔，语速适中，尾音微微上扬带暖意",
    },
    "冷酷": {
        "face": "面无表情，眼神直视不闪躲，嘴唇抿成一条线，下颌线绷紧",
        "body": "动作干脆利落没有多余，站姿挺直，手交叉抱胸或背手",
        "voice": "声线平稳不带起伏，语速不快不慢，每个字都冷",
    },
    "得意": {
        "face": "嘴角勾起一边冷笑，眼神居高临下，下巴微微扬起",
        "body": "身体后靠，翘腿或双手交叠，动作从容不迫",
        "voice": "语速放慢带笑意，重音落在关键词，尾音上扬像在品味",
    },
    "绝望": {
        "face": "眼神空洞涣散，泪水无声滑落，嘴唇微动但发不出声，面如死灰",
        "body": "整个人像被抽空了力气，缓缓跪下或靠墙滑坐，手无力垂落",
        "voice": "声音沙哑像耳语，气息几乎听不见，可能自言自语",
    },
    "释然": {
        "face": "紧锁的眉头缓缓舒展，嘴角微微上扬，眼角有一滴泪但带着笑意",
        "body": "肩膀缓缓放下，长出一口气，整个身体从紧绷变松弛",
        "voice": "声音恢复平稳，语速放缓，尾音带着如释重负的轻叹",
    },
}


def enhance_shot(shot: dict, genre: str = "") -> dict:
    """根据镜头内容，自动匹配和补充音效、表情、动作、语气。
    直接修改 shot 字典并返回。"""
    desc = shot.get("description", "")
    emotion = shot.get("emotion", "")
    sound = shot.get("sound_design", "")
    dialogue = shot.get("dialogue", "")
    location = shot.get("location", "")
    scene_name = shot.get("scene", "")
    all_text = f"{desc} {emotion} {location} {scene_name}"
    enhanced = []

    # 1. 匹配场景音效
    if not sound or len(sound) < 10:
        for scene_key, sdata in SCENE_SOUNDSCAPE.items():
            if scene_key in all_text or any(w in all_text for w in scene_key):
                shot["sound_design"] = f"环境：{sdata['env']}。氛围：{sdata['mood']}。"
                enhanced.append(f"场景音效({scene_key})")
                break

    # 2. 匹配动作音效 + 物理细节
    desc_lower = desc.lower() if desc else ""
    for action_key, adata in ACTION_FX.items():
        # 动作关键词匹配
        keywords = {
            "刀剑劈砍": ["劈", "砍", "挥刀", "刀光", "剑劈"],
            "长枪刺击": ["刺", "枪尖", "长枪", "矛"],
            "弓箭射击": ["箭", "弓", "射", "箭雨"],
            "骑马冲锋": ["冲锋", "骑兵", "策马", "铁骑"],
            "马匹摔倒": ["马摔", "马倒", "马绊", "坠马"],
            "拳脚搏斗": ["拳", "踢", "打", "搏斗"],
            "摔跤倒地": ["摔倒", "倒地", "跌倒", "摔下"],
            "奔跑追逐": ["奔跑", "追", "跑", "冲过去"],
            "开门关门": ["开门", "关门", "推门", "门"],
            "摔东西": ["摔了", "砸", "扔", "摔杯"],
            "签字盖章": ["签字", "盖章", "合同", "签"],
            "手机操作": ["手机", "消息", "短信", "屏幕"],
            "倒水喝茶": ["喝茶", "倒水", "咖啡", "杯"],
            "雨中行走": ["雨中", "雨夜", "撑伞", "雨"],
            "开车": ["开车", "车上", "方向盘", "驾车"],
        }
        kws = keywords.get(action_key, [action_key])
        if any(w in desc for w in kws):
            # 补充动作音效
            existing_sound = shot.get("sound_design", "")
            if adata["sound"] not in existing_sound:
                shot["sound_design"] = (existing_sound + " " if existing_sound else "") + f"动作音：{adata['sound']}。"
            # 补充物理细节到描述
            if adata["physics"] not in desc and len(desc) < 200:
                shot["description"] = desc.rstrip("。") + "。" + adata["physics"]
                desc = shot["description"]
            enhanced.append(f"动作({action_key})")
            break  # 一个镜头匹配一个主要动作

    # 3. 匹配表情 + 肢体 + 语气
    if emotion:
        # 精确匹配
        expr_data = EMOTION_EXPRESSION.get(emotion)
        if not expr_data:
            # 模糊匹配
            for ekey, edata in EMOTION_EXPRESSION.items():
                if ekey in emotion or emotion in ekey:
                    expr_data = edata
                    break
        if expr_data:
            # 补充表情到描述
            if expr_data["face"] not in desc:
                shot["description"] = desc.rstrip("。") + f"。表情：{expr_data['face']}"
                desc = shot["description"]
            # 补充肢体到描述
            if expr_data["body"] not in desc:
                shot["description"] = desc.rstrip("。") + f"。肢体：{expr_data['body']}"
                desc = shot["description"]
            # 补充语气到台词字段（如果有台词）
            if dialogue and dialogue != "(无台词)":
                shot["delivery_hint"] = expr_data["voice"]
            enhanced.append(f"表情语气({emotion})")

    if enhanced:
        shot["_enhanced"] = "，".join(enhanced)

    return shot


def enhance_shots(shots: list, genre: str = "") -> list:
    """批量增强镜头"""
    for s in shots:
        enhance_shot(s, genre)
    return shots
