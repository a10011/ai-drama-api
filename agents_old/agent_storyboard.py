"""智能体3：分镜智能体 — 自动运镜参数、镜头时长分配"""
import json
import time
import logging
from typing import Optional, List
from .agent_base_legacy import BaseAgent, AgentResult

logger = logging.getLogger(__name__)

from services.film_refs import get_genre_film_ref

STORYBOARD_PROMPT = """

【铁律】手上有完整剧本。所有角色、场景、道具、台词必须来自剧本原文。剧本没写的不要编造。不确定时回看剧本。

你是一位资深影视导演。你的任务是根据剧本生成电影级分镜脚本。你必须借鉴经典影视作品的拍摄手法，做到每一镜都是能拍出来的画面。

# 🔴 最高铁律：忠于剧本（违反即废稿）
1. **剧情必须100%来自剧本原文** — 不准自己编剧情、不准添加剧本里没有的情节
2. **台词必须用剧本原文** — 一字不改，剧本里写的什么台词就填什么台词
3. **场景必须忠于剧本** — 剧本写的是雨夜街头就是雨夜街头，不准改成便利店/办公室
4. **人物关系必须忠于剧本** — 剧本写的是分手就是分手，不准改成重逢/偶遇
5. **你只是导演，不是编剧** — 你的工作是把剧本拆成一个个镜头，不是重写故事
6. 如果剧本内容不够拆15个镜头，就按实际内容拆8-12个，不要为了凑数编剧情

# 🔴 内容安全铁律（违反=视频生成被拦截=废稿）
分镜描述、台词、画面内容必须通过AI内容审核（阿里绿网/火山审核），以下规则必须遵守：

## 禁止出现的词（必须用中性词替代）
| 禁止词 | 替代词 |
|--------|--------|
| 造假、诈骗、犯罪、违法 | 违规、纠纷、差错 |
| 贪污、受贿、洗钱 | 资金问题、账目差错 |
| 跑路、潜逃、畏罪 | 离开、远走 |
| 认罪、判刑、坐牢 | 承担责任、接受结果 |
| 凶器、管制刀具 | 工具 |
| 鲜血、血泊 | 红色液体、伤痕 |
| 尸体、死尸、残肢 | 倒下的人 |
| 毒品、吸毒 | 违禁品 |
| 暴力、血腥、残忍 | 激烈、紧张 |
| 自杀、自残 | 放弃、崩溃 |

## 描述规范
- 不定义人物为"犯罪分子/罪犯"，用"对方/当事人/对手"
- 不写"犯罪证据"，用"相关材料/文件/数据"
- 不写具体的犯罪手法细节（怎么造假、怎么洗钱）
- 暴力场景写动作不写伤害结果（"挥拳"可以，"打得满脸是血"不行）
- 角色可以情绪崩溃，但不描写自残或极端暴力行为

# 核心原则
1. **画面感优先** — 每镜描述不是干巴巴的动作说明，而是能直接脑补出画面的视觉描写
2. **分幕结构** — 按剧本本身的叙事弧线拆镜头
3. **声画合一** — 每镜同时设计画面和音效，音效是画面的一部分
4. **节奏控制** — 动作快镜短(2-4s)、对话中镜稳(5-8s)、情感重镜长(8-15s)
5. **史诗大片感** — 运镜要有视觉震撼力，参考《赤壁》《指环王》《斯巴达300勇士》的史诗战争场面。多用航拍俯冲展现千军万马、升格慢动作放大关键瞬间、仰拍塑造英雄威严。每镜都要有"大片既视感"，不要平淡乏味

# 运镜字典（影视专业）

| 运镜 | 效果 | 使用场景 |
|------|------|----------|
| 航拍缓推 | 高空视角逐渐逼近，视觉压迫感 | 定场、大军集结、战场全貌 |
| 航拍俯冲 | 从高空急速下降，冲击力极强 | 冲锋时刻、灾难降临 |
| 航拍拉升 | 从地面升向高空，空间打开 | 结尾收束、展示战场余烬 |
| 手持摇晃 | 模拟人眼晃动，临场混乱感 | 肉搏混战、追逐、突发混乱 |
| 手持跟拍 | 跟随角色移动，主观代入感 | 角色冲杀、穿越战场 |
| 斯坦尼康环绕 | 平滑环绕主体，沉浸式观察 | 角色高光时刻、战场指挥 |
| 轨道快推 | 沿轨道快速推进，速度与压迫 | 冲锋、追击、时间紧迫 |
| 固定 | 完全不动机位，让内容说话 | 关键对话、情绪凝视、对峙 |
| 缓推 | 缓慢推进，情绪逐步收紧 | 紧张升级、揭示真相 |
| 极速推 | 瞬间逼近，冲击/惊吓 | 情绪爆发、突发危险 |
| 仰拍 | 从下往上拍，人物高大威严 | 英雄登场、权威人物、压迫感 |
| 俯拍 | 从上往下拍，渺小/孤独 | 角色受挫、大军俯视、结局 |
| 正反打 | 两人对话交替切镜头 | 对峙、对话交锋 |
| 快速摇摄 | 水平快速扫视，速度和广度 | 战场横扫、大军移动 |
| 升格慢动作 | 高速拍摄慢放，史诗感/细节 | 箭雨落下、关键击杀、悲壮时刻 |

# 景别选择
- **大远景/大全景**: 环境定调、千军万马、战场全貌、结尾升华
- **全景**: 群体动作、阵列移动、多人场景
- **中景**: 对话互动、人物关系、常规叙事
- **近景**: 表情细节、情感交流、单手动作
- **特写/大特写**: 两类必须交替使用——
  · 人物特写：眼神、嘴唇微颤、指尖发白、泪光、汗珠、咬唇、皱眉
  · 物品特写：合同签字、手机屏幕消息、咖啡杯水珠、手表指针、钥匙转动、文件红章、照片边角、酒杯裂痕
  原则：每3-4个镜头必须安排至少1个特写（人物或物品），让观众看到细节。物品特写能暗示剧情（如文件上的造假数字、手机上的未读消息）。

# 🔴 焦点角色分配铁律（违反就是烂片，女主没正脸就是这条没做到）
- 每个有名字的重要角色，全剧至少要有2个专属特写/近景（正脸清晰，让观众记住这张脸）
- 双人对话/对峙场景：必须用正反打运镜，交替给双方正脸特写（A说话→A脸特写，B听后反应→B脸特写），绝不能只拍一个人
- 谁的情感更强烈、谁的台词更关键，谁就是该镜头的焦点角色（focus_character填谁）
- 反应镜头和说话镜头同等重要：A说出台词后，下一镜必须给B听到后的反应特写（哪怕B不说话）
- 情侣/对手戏：男女主的正脸镜头数量要大致均衡，不能全压给一个人

# 音效设计（每镜必填）
- 环境音: 风声、雨声、马蹄声、战鼓、号角、旗帜猎猎
- 动作音: 刀剑碰撞、箭矢破空、铠甲摩擦、马嘶、骨折声
- 人声: 呐喊、喘息、惨叫、低语
- 配乐: 悲壮交响、紧张弦乐、激昂战鼓、低沉大提琴
- 白噪音/静默: 战场余音、死寂时刻

# description 描述规范
- 不是："两人打架" → 是："刀光交错，火星四溅，两匹战马擦身而过，长枪刺入铁甲"
- 不是："大军冲锋" → 是："数万黑甲骑兵如潮水般涌来，铁蹄踏地轰鸣，黄沙遮天蔽日"
- 不是："主角说话" → 是："他挺直脊背，银枪在握，声音穿透风沙，字字如铁"
- 每镜描述要有：主体动作 + 环境氛围 + 画面质感（光影/颜色/材质）

# 🔴 动作物理逻辑规范（最重要！违反就是烂片）
动作镜头必须符合真实物理逻辑，逐帧拆解动作过程：

## 骑马/马术动作
- ❌ 错："骑兵骑着马冲过来" （太笼统，模型会生成静态骑马）
- ✅ 对："战马四蹄腾空飞奔，马鬃被风吹起，马蹄重重踏在泥地上溅起碎泥，骑兵身体随马背起伏颠簸，右手高举长刀过头顶，刀刃反射天光，马速越来越快，地面尘土被马蹄卷成一道尘柱"
- 马摔倒："战马前蹄被绊住，马身猛地前倾栽倒，马脖子扭向一侧，马嘴张开嘶鸣，骑手身体因惯性向前飞出，双手脱离缰绳，在空中翻滚半圈后重重摔在地上，铠甲撞击地面发出闷响，扬起一片尘土，骑手在地上翻滚两圈才停下"

## 打斗/兵器动作
- ❌ 错："两人打起来了" （模型生成的不知所云）
- ✅ 对："敌兵举刀从右侧斜劈下来，刀风带着破空声，主角侧身闪避，刀刃擦着肩甲滑过迸出火星，主角顺势横枪反击，枪杆猛击敌兵腰甲，金属碰撞发出尖锐的铿锵声，敌兵身体被击退两步"
- 必须写清楚：谁攻击→什么武器→什么角度→对方怎么反应→碰撞效果→声音

## 人物摔倒/受伤
- ❌ 错："他从马上掉下来了" （像气球漏气）
- ✅ 对："他的身体失去平衡，双手本能地抓向空中，手指擦过马鬃却没抓住，身体从马背右侧滑落，肩膀先着地，然后整个人翻滚在地，脸上沾满泥沙，嘴里咳出血沫，手还紧紧攥着断了的枪杆"

## 物理逻辑铁律
1. 武器不会凭空出现——拔刀要有拔的动作，刀要有出处（刀鞘/手持/腰间）
2. 马不会瞬间停下——奔跑的马要减速需要距离，急停马会前倾
3. 人摔下来需要时间——从失去平衡→抓空→落地→翻滚，至少2-3秒
4. 打击要有反馈——刀砍到铠甲会弹开+火星+声音，不是无声穿透
5. 血不会喷泉——砍伤是渗血或溅血，不是水管爆裂
6. 衣服/头发/披风要有风的效果——跑动时衣服飘起、静止时自然下垂

## 🔴 动作因果链铁律（最重要！每个动作必须完整）
每个动作必须有完整的"起因→过程→结果"，不能只写开头没有结尾：

- ❌ 错："他拿起笔" — 然后呢？拿起来干嘛？动作悬在半空
- ✅ 对："他拿起笔，在合同上签下名字，笔尖划过纸面留下墨迹，然后放下笔"
- ✅ 对："他拿起笔，犹豫了一下，笔尖悬在纸上没落下，最终把笔重重摔在桌上"

- ❌ 错："她站起来" — 站起来之后呢？
- ✅ 对："她撑着桌沿站起来，椅子向后滑出，她走到窗前，双手撑在窗台上看着外面"

- ❌ 错："他推开门" — 推开后呢？
- ✅ 对："他推开办公室的门，门板撞到墙上发出闷响，他站在门口扫视了一圈房间，然后大步走向办公桌"

- ❌ 错："她转过身" — 转身后呢？
- ✅ 对："她缓缓转过身，背对着他，肩膀微微颤抖了一下，然后头也不回地走向门口"

- ❌ 错："他看着文件" — 看到什么？看完怎样？
- ✅ 对："他拿起文件翻了两页，目光停在一行数字上，瞳孔微缩，手指无意识地攥紧了纸角"

原则：观众看到的每个动作都要有"为什么做→怎么做→做完了什么结果"，不能出现"拿起来就没了""转过身就没了"这种悬空动作。

# director_shot 逐秒拍摄指令（你是导演，这是给摄影师的机位单）
每个镜头必须写 director_shot 字段，按秒拆解拍摄动作，精确到"第几秒画面该怎么动、角色什么表情动作、观众该看到什么细节"。

🔴 镜头结构铁律——每个镜头必须有"起承转合"：
- 起（前2秒）：镜头开始，告诉观众"这是哪里/谁在/在干什么"，让观众看清环境或人物
- 承（中间）：事情发生，角色动作/表情变化/对话，画面有推进
- 转（如有）：关键变化——发现线索、情绪转变、冲突爆发
- 合（最后1-2秒）：镜头收尾，让观众消化刚才看到的，画面停留或缓推给情绪余韵
- 禁止：镜头一开始就是高潮（观众没反应过来）、镜头突然切断（没看完就跳走）

🔴 全景/远景拉近定脸铁律（真实拍剧标准）：
凡是大远景/大全景/全景里出现人物的镜头（尤其开场定场、首次亮相），必须按"先看环境→拉近到人→定住脸"三段式拍：
- 第一段（2-3秒）：大远景交代环境/地点/氛围（让观众知道"这是哪儿"）
- 第二段（2-3秒）：镜头推近到人物身上（让观众看到"谁在这儿"）
- 第三段（2-3秒）：定在人物脸部，让观众看清正脸并记住（这是锁脸段，视频会锁定角色脸）
双人同框：拉近后定在两人脸上各停1-2秒（A的脸→B的脸，或双人正脸同框），让观众记住两个人的长相。
绝不能只停在"看清轮廓"就切走——必须看清脸。这关系到观众能否记住角色。

格式示例（8秒镜头）：
"director_shot": "第1-2秒（起）：镜头从角色握杯的手指特写开始，让观众看清这是一个疲惫的人在喝咖啡；第3-4秒（承）：缓推至角色面部，眉头紧锁，嘴唇微抿，观众看到他在焦虑；第5-6秒（转）：角色开口说话，嘴角颤抖，说出关键台词；第7-8秒（合）：镜头微拉远，露出雨夜窗景，角色显得孤独，让观众感受这个情绪"

开场定场镜头示例（双人，10秒）：
"director_shot": "第1-3秒（起）：大远景航拍，残阳染红断崖江面，让观众看清这是苍凉的离别之地；第4-6秒（承）：镜头缓缓推近到崖顶两道身影，看清玄色战甲与素白长裙相对而立；第7-8秒（合）：镜头定在蒙毅脸上，看清他满是征尘的坚毅面庞和泛红的眼眶（锁脸）；第9-10秒（合）：切到玉漱的脸，看清她莹白脸颊上的泪痕和长睫轻颤（锁脸），让观众记住两个人的脸"

原则：
- 每2秒一个动作节拍，不能整段静止不动
- 角色必须有微表情/微动作（眨眼、吞咽、手指动作、嘴角变化）
- 运镜要有变化（推、拉、摇、固定交替）
- 观众每秒都能看到新的画面信息，不能5秒画面不变
- 🔴 最后2秒必须有"收"——让观众看明白这个镜头想表达什么，不能突然切走
- 观众每秒都能看到新的画面信息，不能5秒画面不变

# 输出JSON格式
每个镜头必需字段：
- shot_num: 镜号
- location: 场景地点（如"狭小卧室""公司会议室""街头咖啡店"）
- scene: 场景名（如"开篇""对峙""冲锋""血战""收尾"）
- shot_type: 景别（大远景/大全景/全景/中景/近景/特写/大特写）
- focus_character: 本镜头的焦点角色名（画面主体是谁的脸/谁在表演）。单人镜头填该角色名；双人镜头填画面焦点所在角色名（谁的正脸朝向观众）；空镜/全景填"(无角色)"。此字段决定视频锁脸用哪张角色图，必须准确。
- camera_movement: 运镜（航拍缓推/手持摇晃/固定/缓推/仰拍/正反打/升格慢动作…）
- camera_angle: 角度（俯视/仰视/平视/倾斜）
- duration_sec: 时长——短剧节奏要快，每个镜头不超过12秒：
  · 长段独白/对白：8-10秒（一口气说完，不拖）
  · 两人对话交锋：6-8秒（一来一回即可）
  · 角色特写/情绪酝酿：6-8秒
  · 快速动作/打斗/冲击：4-5秒
  · 大全景定场/环境交代：4-6秒
  · 关键反转/震撼瞬间：8-10秒
  铁律：单镜头不超12秒！长对白要拆成多镜交替拍摄！
- description: 纯画面描述（只写摄像头能拍到的视觉内容，不含台词、不含声音描述、不含角色内心想法。台词全部放进 dialogue 字段！）
- director_shot: 逐秒拍摄指令（按秒拆解，角色微表情+运镜变化+画面信息，见上方规范）
- dialogue: 角色说出来的台词（嘴会动，需要对口型），无台词填"(无台词)"
- inner_voice: 角色内心独白（心里想的话，嘴不动不说话，用旁白配音呈现）。如"这串数字不对，三年前并购案的漏洞就在这里"。内心独白镜头画面是角色沉默的表情特写，声音是角色的内心想法。无内心独白填"(无)"
- narration: 全局旁白（第三人称叙述，如"那一夜，他发现了改变命运的真相"）。无旁白填"(无)"
- sound_design: 音效与配乐（环境音+动作音+配乐，具体到音色和节奏）
- emotion: 情绪（紧张/悲壮/愤怒/激昂/肃穆/苍凉…）
- lighting: 光线（自然光/逆光/侧光/剪影/金黄昏/冷月…）
- weather: 天气（晴空/黄沙漫天/暴雨/大雪/薄雾/夕阳…）
- transition: 转场（切入/淡入/叠化/闪白）
- importance: 重要性（high/medium/low）
- outfit: {"角色名":"服装描述"}
- props: {"角色名":"道具"}
- char_ages: {"角色名":"青年/中年/老年"}

返回纯JSON（无markdown，无代码块标记）：
{
  "total_shots": 12,
  "directing_notes": "整体导演说明：分幕结构、情绪曲线、参考影片",
  "shots": [
    {
      "shot_num": 1,
      "scene": "开篇",
      "shot_type": "大全景",
      "camera_movement": "航拍缓推",
      "camera_angle": "俯视",
      "duration_sec": 8,
      "description": "边关旷野，秋风肃杀，黄沙漫天中两军列阵对峙。旌旗遮天蔽日，铁甲层层如林，战马昂首嘶鸣。数十万大军静立待战，杀气翻涌。",
      "dialogue": "(无台词)",
      "sound_design": "风声呼啸、旗甲猎猎作响。低沉蓄力战鼓咚—咚—咚，马蹄踏地闷响。",
      "emotion": "肃杀紧张",
      "lighting": "阴天散射光",
      "weather": "黄沙漫天",
      "transition": "切入",
      "importance": "high",
      "outfit": {},
      "props": {},
      "char_ages": {}
    }
  ]
}

# 🔴 平台审核避雷铁律（违反则图/视频被拦截，生成失败白烧钱）
写 description/director_shot 时必须规避以下，描写要"安全合规"：
1. 禁止负面崩溃情绪肢体：不写"身体蜷缩/肩膀抽动/瘫坐崩溃/失声痛哭/浑身发抖/嘴角无力下垂"，改写"眼含泪光但目光坚定/安静伫立/眉头轻蹙/单滴清泪/身姿平稳"
2. 禁止血腥特写：不写"断肢/内脏/喷血/贯穿伤口/血肉/血流成河/满地尸骸"，改写"广角全景交锋/兵器碰撞/擦伤淡红/倒地远景/淡淡血色浸染"
3. 禁止轻生永诀：不写"永诀/赴死/天人永隔/生离死别/坠崖轻生"，改写"远征/离别等候归/暂且分离静待重逢"
4. 战场用宏观远景：军阵、骑兵冲锋、尘土漫天、兵器交锋，不近景刻画伤口血肉
5. 离别用含蓄克制：怅然神色、眼含泪光、目光坚定，无肢体失控无崩溃描写
"""


REVISE_SHOT_PROMPT = """你是一位短剧分镜师。根据反馈修改指定的分镜。

返回JSON格式，仅包含修改后的该分镜数据：
{
  "shot_num": 1,
  "shot_type": "远景/全景/中景/近景/特写/大特写",
  "focus_character": "本镜头焦点角色名",
  "camera_movement": "固定/推/拉/摇/移/跟/升降/环绕",
  "camera_angle": "平视/俯视/仰视/倾斜",
  "duration_sec": 5,
  "description": "修改后的画面描述",
  "dialogue": "台词"
}"""


class StoryboardAgent(BaseAgent):
    """分镜智能体：自动运镜参数、镜头时长分配"""

    name = "分镜智能体"
    description = "自动运镜参数、镜头时长分配"
    version = "1.0.0"

    def generate_storyboard(self, script: str, characters: List[dict], scenes: List[str], include_environment: bool = False, costume_models: list = None, genre: str = "", director_tasks: dict = None, director_analysis: dict = None) -> AgentResult:
        """生成完整分镜 — 遵循导演指令"""
        start = time.time()
        try:
            # ═══ 导演指令 ═══
            dt = director_tasks or {}
            da = director_analysis or {}
            director_block = ""
            if dt or da:
                parts = ["══════ 【导演指令 - 必须遵守】 ══════"]
                if da.get("director_vision"):
                    parts.append("【导演总纲】" + str(da["director_vision"]))
                if da.get("core_conflict"):
                    parts.append("【核心冲突】" + str(da["core_conflict"]))
                if da.get("pacing_notes"):
                    parts.append("【节奏要求】" + str(da["pacing_notes"]))
                if da.get("highlight_moments"):
                    parts.append("【高光时刻】" + str(da["highlight_moments"]))
                if da.get("emotional_curve"):
                    parts.append("【情绪曲线】" + str(da["emotional_curve"]))
                if da.get("character_archetypes"):
                    parts.append("【角色原型】" + str(da["character_archetypes"]))
                if dt.get("storyboard_generation"):
                    parts.append("【分镜任务】" + str(dt["storyboard_generation"]))
                parts.append("══════════════════════════════")
                director_block = "\n".join(parts) + "\n\n"

            genre_str = f"\n题材类型：{genre}\n" if genre else ""
            char_info = "\n".join([f"- {(c.get('name','?') if isinstance(c,dict) else c)}: {(c.get('description','')[:100] if isinstance(c,dict) else '')}" for c in characters])
            # 附加造型方案
            costume_str = ""
            if costume_models:
                parts = []
                for cm in costume_models:
                    cn = cm.get("char_name", "")
                    modeling = cm.get("modeling", {})
                    outfit = modeling.get("outfit", {})
                    notes = modeling.get("style_notes", "")
                    if outfit:
                        desc = str(outfit.get("name","")) + ": " + str(outfit.get("description",""))[:80]
                        parts.append(str(cn) + ": " + desc + "; 风格: " + str(notes))
                if parts:
                    costume_str = "\n角色造型方案（分镜时按剧情场景选择合适服装）：\n" + "\n".join(parts) + "\n"

            self.report_progress("匹配影视参考库...", 5)
            film_ref = get_genre_film_ref(genre)
            # Prompt 结构: 权重声明 + 完整剧本 → 导演指令 → 分镜任务
            user_prompt = f"""══════ 信息权重（优先级从高到低，不可颠倒）══════
第1优先级 - 完整原始剧本：所有人设、剧情、台词、情绪以此为准，最高约束
第2优先级 - 导演总指令：硬性创作要求、风格规范、合规标准
第3优先级 - 当前分片参数：仅作补充参考，禁止违背前两层信息
══════════════════════════════════════════════

══════ 第一步：完整阅读剧本 ══════
{script[:12000]}

══════ 第二步：理解导演指令 ══════
{director_block}

══════ 第三步：角色信息 ══════
{char_info}
{film_ref}{genre_str}

⚠️ 重要：分镜中角色的名字必须使用上面"角色信息"中列出的角色名，禁止自己编造新名字。例如角色信息里有"我方主将"，分镜里就必须用"我方主将"，不能改成"林烈"之类的其他名字。这关系到后续场景图能否用角色肖像锁脸。

场景：{', '.join(scenes)}

请按以下步骤为剧本配置分镜：

第一步：逐段阅读剧本，标注每个段落的：
  - 发生了什么动作？（打架/对话/独白/追逐/静坐…）
  - 情绪是什么？（紧张/温馨/悲伤/愤怒/悬疑/平和…）
  - 关键转折点在哪里？
  - ⚠️ 有对话的段落：必须提取台词原文，分配给对应镜头的 dialogue 字段

第二步：为每个段落分配镜头，根据上面分析的内容决定运镜：
  - 纯对话/反应戏 → 固定（让表演和台词说话）
  - 对话中情感升温/关键台词 → 在对话基础上推近
  - 动作/追逐/打斗 → 跟+摇（保持动态节奏）
  - 情感爆发/高潮 → 推或环绕（逼近人物）
  - 揭示真相/关键发现 → 推（引导注意力）
  - 离别/悲伤/收尾 → 拉（空间拉开，人物远去）
  - 开篇/新场景 → 升降（建立空间感）
  - 对峙/紧张 → 环绕或固定（让紧张酝酿）

第三步：为每个镜头补充景别、角度、时长、光线、情绪、焦点角色(focus_character——填画面主体角色名，双人镜头填正脸朝向观众的那位)

第四步（⚠️ 最关键！内容衔接检查）：
逐对检查相邻两镜的内容是否自然承接——
  - 上一镜结尾发生了什么？下一镜开头是否合理承接？
  - 禁止：Shot N 在A地点打斗 → Shot N+1 突然在B地点吃饭 （空间跳）
  - 禁止：Shot N 角色在说话 → Shot N+1 无交代突然换了一群人 （人物跳）
  - 禁止：Shot N 白天 → Shot N+1 突然深夜 无过渡 （时间跳）
  - ✅ 正确：Shot N 走出房间 → Shot N+1 到达走廊 （空间自然推进）
  - ✅ 正确：Shot N 被打倒 → Shot N+1 倒地后的痛苦反应 （因果承接）
  - ✅ 正确：Shot N 天亮对话 → Shot N+1 夕阳独白+字幕"几小时后" （有交代的时间跳）
  如发现内容跳变，必须插入过渡镜头或修改 description 让衔接自然。

⚠️ 运镜必须从剧本内容推导，不是随机选择！同一场景内的镜头运镜可以不同，取决于具体内容变化。
4. 每个镜头必须含 environment 字段，描述环境的详细背景（时间·天气·年代·场景氛围）
  5. 【核心】每个镜头必须包含 outfit / props / char_ages 字段，服装道具年龄随剧情进展自然变化。
     - 如果故事时间跨度大，角色年龄要分阶段变化
     - 服装要随场景（家里/公司/战场）、角色发展（穷人→富人）而变化
     - 道具要与动作匹配（打架时拿武器，做饭时拿锅铲）"""
            self.report_progress("LLM设计分镜（对标电影级）...", 30)
            result = self._call_llm_json(STORYBOARD_PROMPT, user_prompt, retries=0, timeout=180)
            # 后处理：缺字段自动填补
            if isinstance(result, dict):
                shots = result.get("shots", [])
                if not shots and isinstance(result.get("data"), dict):
                    shots = result["data"].get("shots", [])
                if shots:
                    # ── 运镜智能后处理 ──
                    # 不强制百分比，只修复明显不合理（LLM未正确理解的场景）
                    movements = [s.get('camera_movement','固定') for s in shots]
                    fix_count = 0
                    for idx, s in enumerate(shots):
                        desc = s.get('description','')
                        mov = s.get('camera_movement','固定')
                        imp = s.get('importance','medium')
                        # high镜头 但描述含情感爆发词 → 应该是动态运镜
                        if imp == 'high' and mov == '固定':
                            emotional_words = ['爆发','冲突','对决','揭示','真相','高潮','转折','绝望','崩溃','愤怒']
                            if any(w in desc for w in emotional_words):
                                s['camera_movement'] = '推'
                                fix_count += 1
                                logger.info(f"[Storyboard] Shot {idx+1}: emotional high+fixed → 推")
                        # 结尾镜头 但描述含离别/远去词 → 应该用拉
                        if idx == len(shots) - 1 and mov == '固定':
                            ending_words = ['离去','远去','离开','告别','背影','走远','消失']
                            if any(w in desc for w in ending_words):
                                s['camera_movement'] = '拉'
                                fix_count += 1
                                logger.info(f"[Storyboard] Shot {idx+1}: ending scene → 拉")
                    if fix_count > 0:
                        logger.info(f"[Storyboard] Auto-corrected {fix_count} unreasonable camera movements")
                    # 拍摄角度智能后处理
                    angles = [s.get('camera_angle','') for s in shots]
                    if all(a in ('','平视','平视') for a in angles):
                        for idx, s in enumerate(shots):
                            desc = s.get('description','')
                            imp = s.get('importance','medium')
                            if '仰' in desc or '威严' in desc or '巨大' in desc:
                                s['camera_angle'] = '仰视'
                            elif '俯' in desc or '渺小' in desc or '孤独' in desc:
                                s['camera_angle'] = '俯视'
                            elif imp == 'high':
                                s['camera_angle'] = ['仰视','俯视','倾斜'][idx % 3]
                            else:
                                s['camera_angle'] = ['平视','仰视','俯视'][idx % 3]
                        logger.info(f"[Storyboard] Applied contextual angle diversity")
                    # ── 工具箱驱动自我优化 (v3.0) ──
                    tool_results = {}
                    if getattr(self, "tool_registry", None):
                        try:
                            import json as _json
                            shots_json = _json.dumps(shots, ensure_ascii=False)
                            tool_check = self._try_tool_redo([
                                {"name": "shot_continuity_check", "params": {"shots_json": shots_json}, "weight": 1.0},
                                {"name": "highlight_scene_design", 
                                 "params": {"script_text": script[:5000], "genre": genre}, "weight": 0.5},
                            ], min_score=70)
                            
                            if tool_check["tool_results"]:
                                tool_results["continuity_check"] = tool_check["tool_results"][0].get("data", {}) if len(tool_check["tool_results"])>0 else {}
                                tool_results["highlight_design"] = tool_check["tool_results"][1].get("data", {}) if len(tool_check["tool_results"])>1 else {}
                            
                            # 自动修复重度连贯问题
                            cont_data = tool_results.get("continuity_check", {})
                            issues = cont_data.get("issues", [])
                            high_issues = [i for i in issues if i.get("severity") == "high"]
                            if high_issues:
                                logger.info(f"[Storyboard+Tool] {len(high_issues)}处严重连贯问题，自动修复")
                                for issue in high_issues:
                                    pair_str = issue.get("shot_pair", "")
                                    itype = issue.get("type", "")
                                    try:
                                        parts = pair_str.replace("#", "").split("→")
                                        idx = int(parts[0]) - 1
                                        if 0 <= idx < len(shots) - 1:
                                            fix_map = {
                                                "场景跳变": "叠化", "情绪断层": "淡入淡出",
                                                "时间跳跃": "叠化", "跳轴": "切（插入中立镜）",
                                                "景别跳跃": "叠化"
                                            }
                                            shots[idx + 1]["transition"] = fix_map.get(itype, "叠化（自动修复）")
                                    except Exception:
                                        logger.warning("transition fix failed", exc_info=True)
                            
                            # 注入高光设计到对应镜头
                            hl_data = tool_results.get("highlight_design", {})
                            tips = hl_data.get("design_tips", [])
                            if tips:
                                logger.info(f"[Storyboard+Tool] {len(tips)}个高光时刻")
                                for tip in tips:
                                    moment = tip.get("moment", "")
                                    for s in shots:
                                        desc = str(s.get("description", ""))
                                        if moment in desc or any(kw in desc for kw in [moment.replace("/", ""), moment.split("/")[0]]):
                                            s["highlight_tip"] = tip
                                            s["duration_sec"] = min(15, s.get("duration_sec", 5) * 1.3)
                                            s["camera_movement"] = tip.get("camera", s.get("camera_movement", ""))
                                            break
                            
                            # 综合分低→不再触发 GLM 重生成（太慢导致超时），直接用第一次结果
                            if tool_check["should_redo"]:
                                logger.info(f"[Storyboard+Tool] score={tool_check['score']:.0f} 跳过重生成（避免GLM超时），使用首次结果")
                                    
                        except Exception as tool_e:
                            logger.warning(f"[Storyboard+Tool] 工具增强跳过: {tool_e}")

                    result["_tool_results"] = tool_results
                    fixed_shots = []
                    for s in shots:
                        if not isinstance(s, dict):
                            continue
                        # 必填字段默认值
                        s.setdefault("shot_type", "中景")
                        s.setdefault("camera_movement", "推")  # 默认推镜，不再全固定
                        s.setdefault("camera_angle", "平视")
                        s.setdefault("duration_sec", 5)
                        s.setdefault("description", "")
                        s.setdefault("dialogue", "")
                        s.setdefault("emotion", "中性")
                        s.setdefault("lighting", "自然光")
                        s.setdefault("transition", "切入")
                        s.setdefault("scene", "")
                        s.setdefault("location", "")
                        s.setdefault("action", "")
                        s.setdefault("outfit", {})
                        s.setdefault("props", {})
                        s.setdefault("char_ages", {})
                        fixed_shots.append(s)
                    result["shots"] = fixed_shots
                    # 重新计算 total
                    result["total_shots"] = len(fixed_shots)
                    result["total_duration_sec"] = sum(s.get("duration_sec", 5) for s in fixed_shots)

                    # ═══ 副导演审稿：检查文字/动作/运镜是否合理，不合理直接修 ═══
                    try:
                        fixed_shots = self._deputy_director_review(fixed_shots, script, genre)
                        result["shots"] = fixed_shots
                        result["total_duration_sec"] = sum(s.get("duration_sec", 8) for s in fixed_shots)
                    except Exception as _de:
                        logger.warning(f"[Storyboard] 副导演审稿跳过: {_de}")
            # 如果 LLM 返回空或无 shots，生成基本分镜兜底
            if not result or not result.get("shots"):
                logger.warning("[Storyboard] LLM returned empty, generating fallback shots")
                fallback_shots = self._build_fallback_shots(script, characters, scenes)
                result = {"shots": fallback_shots, "total_shots": len(fallback_shots)}

            # ═══════════════════════════════════════════════════════════════
            # 分镜质量自动化校验（三层：台词合规 + 描述质量 + 文字规范）
            # ═══════════════════════════════════════════════════════════════
            shots = result.get("shots", [])
            if shots:
                qr = self._quality_check_shots(shots, script, director_block, da, characters)
                result["quality_report"] = qr
                
                # ═══ 时长强制兜底：所有镜头不超过10秒 ═══
                for _s in shots:
                    _dur = _s.get("duration_sec", 5)
                    if isinstance(_dur, (int, float)) and _dur > 10:
                        _s["duration_sec"] = min(_dur, 10)
                        logger.warning(f"[Storyboard] Shot{_s.get('shot_num','?')}: 时长{_dur}s→10s(自动兜底)")

                # ══════════════════════════════════════════════════════
                # 分级判定 + 自动重试（严重阻断 / 警告放行）
                # ══════════════════════════════════════════════════════
                MAX_RETRIES = 3
                retry_count = 0
                _retry_shots = list(shots)
                _retry_qr = dict(qr)

                while _retry_qr.get("severe_count", 0) > 0 and retry_count < MAX_RETRIES:
                    severe_issues = _retry_qr.get("severe_issues", [])
                    logger.warning(f"[Storyboard] 第{retry_count+1}次重试：{len(severe_issues)}条严重违规")

                    # 专用修正 Prompt
                    fix_prompt = f"""上一轮分镜存在多项严重违规，本次必须全部整改，否则无法通过质检：
1. 时长硬性规则：每一个独立镜头时长必须≤10秒，长剧情必须拆分为多个短镜头，禁止单镜超过10秒；
2. 画面内容整改：禁止描述叼烟/吸烟/倚靠/瘫坐等违规动作，只写画面可见的视觉内容；
3. 台词语义保真：禁止改写原剧本高光台词，对话必须归属正确角色。
违规明细：{'; '.join(severe_issues[:8])}

请重新输出完整分镜JSON，所有违规点必须修正。"""

                    retry_result = self._call_llm_json(STORYBOARD_PROMPT, user_prompt + "\n\n" + fix_prompt, retries=0, timeout=180)
                    if retry_result and retry_result.get("shots"):
                        _retry_shots = retry_result.get("shots", [])
                        _retry_qr = self._quality_check_shots(_retry_shots, script, director_block, da, characters)
                        # 时长兜底
                        for _s in _retry_shots:
                            _d = _s.get("duration_sec", 5)
                            if isinstance(_d, (int, float)) and _d > 10:
                                _s["duration_sec"] = min(_d, 10)
                        retry_count += 1
                        logger.info(f"[Storyboard] 重试{retry_count}完成: {len(_retry_shots)}镜 严重{_retry_qr.get('severe_count',0)}")
                    else:
                        logger.warning("[Storyboard] 重试LLM返回空，终止重试")
                        break

                if retry_count >= MAX_RETRIES and _retry_qr.get("severe_count", 0) > 0:
                    logger.error(f"[Storyboard] {MAX_RETRIES}次重试仍不合格，转人工审核。违规: {_retry_qr.get('severe_issues',[])}")
                    result["_needs_manual_review"] = True

                # 使用重试后的结果
                result["shots"] = _retry_shots
                result["quality_report"] = _retry_qr
                result["retry_count"] = retry_count
                result["_regenerated"] = retry_count > 0

                if _retry_qr.get("severe_count", 0) > 0:
                    logger.warning(f"[Storyboard] 质量: {_retry_qr['severe_count']}严重 {_retry_qr.get('warn_count',0)}警告 得分{_retry_qr.get('score','?')} 重试{retry_count}次")
                else:
                    logger.info(f"[Storyboard] 质检通过 得分{_retry_qr.get('score','?')} 重试{retry_count}次")

            return AgentResult(
                data=result,
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"生成分镜失败: {e}")
            return AgentResult(success=False, error=str(e))


    # ── 三层质量校验 ──
    def _quality_check_shots(self, shots: list, script: str, director_block: str, da: dict, characters: list) -> dict:
        """台词+描述+文字规范三层校验。返回 {score, severe_count, warn_count, warnings, severe_issues}"""
        warnings = []
        severe = []
        score = 100

        # ═══ schema.py 分镜结构完整性校验 ═══
        try:
            from schema import ShotOutput
            for s in shots:
                try:
                    ShotOutput(**{k: s.get(k, "") if k != "duration_sec" else s.get(k, 5) for k in ["shot_num","description","dialogue","shot_type","camera_movement","camera_angle","duration_sec","emotion","scene","location","focus_character","lighting","transition","sound_design"]})
                except Exception as _ve:
                    sn = s.get("shot_num", "?")
                    severe.append(f"Shot{sn}: 结构缺失 {str(_ve)[:100]}")
                    score -= 10
        except ImportError:
            logger.warning("[Storyboard] schema.py 未找到，跳过结构校验")

        # ═══ 第一层：台词合规校验 ═══
        # 1a. 台词归属：检查 shot dialogue 是否匹配角色
        char_names = {c.get("name", "") for c in characters if c.get("name")}
        for s in shots:
            dlg = str(s.get("dialogue", "") or "")
            if not dlg or dlg in ("(无台词)", "(无)"):
                continue
            focus = str(s.get("focus_character", "") or "")
            # 检查台词中是否出现了不属于本镜头焦点角色的对白
            if focus and focus in char_names:
                # 简单检测：台词中提到了不该说话的角色名
                for cn in char_names:
                    if cn != focus and f"{cn}：" in dlg:
                        warnings.append(f"Shot{s.get('shot_num','?')}: 台词含'{cn}'的对白但焦点角色是'{focus}'")
                        score -= 3

        # 1b. 台词归属校验：高光台词是否被改写
        _hl_text = str(da.get("highlight_moments", "") or "") + str(da.get("core_conflict", "") or "")
        if _hl_text and len(_hl_text) > 20:
            # 从导演指令提取关键台词片段
            import re as _re
            _key_phrases = _re.findall(r'[「「]([^」」]{4,30})[」」]', str(director_block or ""))
            if not _key_phrases:
                _key_phrases = _re.findall(r'"([^"]{4,30})"', str(director_block or ""))
            for kp in _key_phrases[:5]:
                found = any(kp[:6] in str(s.get("dialogue", "")) for s in shots)
                if not found and len(kp) > 6:
                    warnings.append(f"导演指定台词'{kp[:20]}...'未在分镜中找到")

        # ═══ 第二层：镜头描述质量校验 ═══
        for s in shots:
            sn = s.get("shot_num", "?")
            desc = str(s.get("description", "") or "")
            emo = str(s.get("emotion", "") or "")
            focus = str(s.get("focus_character", "") or "")

            # 2a. 空洞化检测
            vague_patterns = ["两人对话", "气氛紧张", "场面宏大", "激烈打斗", "发生冲突"]
            for vp in vague_patterns:
                if vp in desc and len(desc) < 60:
                    severe.append(f"Shot{sn}: 空洞描述'{vp}'，缺少具体画面细节")
                    score -= 15

            # 2b. 完整性检测：需含神态+动作+画面
            has_expression = any(kw in desc for kw in ["眼神", "表情", "眉头", "嘴唇", "眼眶", "脸"])
            has_action = any(kw in desc for kw in ["走", "站", "坐", "推", "拉", "挥", "转", "抬", "放", "按", "握"])
            has_visual = len(desc) >= 50
            missing = []
            if not has_expression: missing.append("神态")
            if not has_action: missing.append("动作")
            if not has_visual: missing.append("画面细节")
            if missing:
                warnings.append(f"Shot{sn}: 描述缺少{'/'.join(missing)}")

            # 2c. 人设行为校验：角色性格 vs 镜头行为
            for c in characters:
                cname = c.get("name", "")
                if focus == cname and c.get("personality"):
                    pers = str(c.get("personality", ""))
                    if "冷静" in pers or "理性" in pers or "通透" in pers:
                        outburst_words = ["暴怒", "狂吼", "歇斯底里", "摔东西"]
                        if any(ow in desc for ow in outburst_words):
                            severe.append(f"Shot{sn}: 角色'{cname}'性格'{pers}'与镜头中暴躁行为冲突")
                            score -= 20

        # 2d. 情绪分层检测（读取导演情绪曲线）
        emos = [str(s.get("emotion", "") or "") for s in shots]
        unique_emos = set(e for e in emos if e and e not in ("", "无"))
        if len(unique_emos) <= 1 and len(shots) >= 6:
            warnings.append(f"全剧{len(shots)}镜情绪单一({unique_emos})，缺少情绪分层")

        # ═══ 第三层：基础文字规范 ═══
        for s in shots:
            sn = s.get("shot_num", "?")
            desc = str(s.get("description", "") or "")
            # 3a. 脱离剧本的狗血桥段检测
            soap_keywords = ["失忆", "车祸", "绝症", "失散多年的兄妹", "一夜情", "下药"]
            for sk in soap_keywords:
                if sk in desc and sk not in script[:3000]:
                    severe.append(f"Shot{sn}: 出现剧本外原创桥段'{sk}'，严重偏离原著")
                    score -= 25

        # 汇总 + schema.py 结构化校验
        severe_count = len(severe)
        warn_count = len(warnings)
        score = max(0, score - warn_count * 2)

        # 调用 schema.py 统一分镜质检（结构完整性 + 台词 + 空洞描述）
        schema_result = {"blocking": 0, "warnings": 0}
        try:
            from schema import quality_check_shots
            schema_result = quality_check_shots(shots)
            if schema_result.get("blocking", 0) > 0:
                severe_count += schema_result["blocking"]
                score = max(0, score - schema_result["blocking"] * 10)
            warn_count += schema_result.get("warnings", 0)
        except ImportError:
            pass

        return {
            "score": score,
            "severe_count": severe_count,
            "warn_count": warn_count,
            "warnings": warnings,
            "severe_issues": severe,
        }

    # ── 自检 Prompt ──
    SELF_CHECK_PROMPT = """你是资深审片导演。评估以下分镜质量，打分并指出问题。
输出JSON：{"score": 85, "issues": ["问题1"], "overall": "总体评价"}
评估维度：画面感(25) 运镜专业度(25) 节奏控制(25) 声画设计(25)，总分<60建议重写"""

    def _self_check(self, storyboard_json: dict) -> dict:
        """LLM自检分镜质量"""
        try:
            import json as _json
            data = _json.dumps(storyboard_json, ensure_ascii=False, default=str)
            result = self._call_llm_json(self.SELF_CHECK_PROMPT, f"分镜数据：{data[:4000]}", retries=0, timeout=120)
            if result and isinstance(result, dict):
                score = result.get("score", 0)
                issues = result.get("issues", [])
                logger.info("[StoryboardAgent] 自检评分: %s, 问题: %s", score, issues)
                return result
        except Exception as e:
            logger.warning("[StoryboardAgent] 自检失败: %s", e)
        return {}

    def _build_fallback_shots(self, script: str, characters: list, scenes: list) -> list:
        """LLM失败时的兜底分镜：从剧本简单拆分"""
        shots = []
        # 按空行/句号简单分段
        segments = [s.strip() for s in script.replace('！','。').replace('？','。').replace('！','。').split('。') if len(s.strip()) > 10]
        if not segments:
            segments = [script[:200]]
        
        for i, seg in enumerate(segments[:12]):  # 最多12个镜头
            shot = {
                "shot_num": i + 1,
                "description": seg[:80],
                "dialogue": "",
                "camera_movement": "固定",
                "camera_angle": "平视",
                "shot_type": "中景",
                "duration_sec": max(3, min(8, len(seg) // 3)),
                "lighting": "自然光",
                "emotion": "中性",
                "transition": "切入" if i > 0 else "",
                "scene": scenes[i % len(scenes)] if scenes else f"场景{i+1}",
                "location": scenes[i % len(scenes)] if scenes else "",
                "environment": "室内，自然光线",
                "importance": "medium",
                "outfit": {},
                "props": {},
                "char_ages": {}
            }
            # 添加角色
            for c in characters[:2]:
                name = c.get("name","") if isinstance(c,dict) else str(c)
                if name:
                    shot.setdefault("characters", []).append(name)
            shots.append(shot)
        
        logger.info(f"[Storyboard] Built {len(shots)} fallback shots from script")
        return shots



    def revise_shot(self, shot_data: dict, feedback: str) -> AgentResult:
        """根据反馈修改单个分镜"""
        start = time.time()
        try:
            user_prompt = f"""当前分镜：
{json.dumps(shot_data, ensure_ascii=False, indent=2)}

修改要求：{feedback}"""
            result = self._call_llm_json(
                REVISE_SHOT_PROMPT, user_prompt, retries=0)
            # 如果 LLM 返回空或无 shots，保留原分镜作为兜底
            # （revise_shot 修改的是单个镜头，作用域内没有 script/characters/scenes，
            #  早期代码引用未定义变量会 NameError）
            if not result or not result.get("shots"):
                logger.warning("[Storyboard] revise LLM returned empty, keep original shot")
                result = {"shots": [shot_data], "total_shots": 1}
            
            return AgentResult(
                data=result,
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"修改分镜失败: {e}")
            return AgentResult(success=False, error=str(e))

    def optimize_camera(self, shots: List[dict]) -> AgentResult:
        """优化所有镜头的运镜参数"""
        start = time.time()
        try:
            prompt = f"""你是一位摄影指导。优化以下分镜的运镜参数，确保：
1. 相邻镜头运镜不重复
2. 情绪高潮用动态运镜
3. 对话场景用固定或慢推

分镜：
{json.dumps(shots, ensure_ascii=False, indent=2)}

返回优化后的完整分镜JSON数组。"""
            result = self._call_llm_json(
                                "你是一位摄影指导，返回JSON数组。",
                prompt
            )
            return AgentResult(
                data={"shots": result if isinstance(result, list) else result.get("shots", [])},
                duration_ms=int((time.time() - start) * 1000)
            )
        except Exception as e:
            logger.error(f"优化运镜失败: {e}")
            return AgentResult(success=False, error=str(e))

    def _deputy_director_review(self, shots: list, script: str, genre: str) -> list:
        """副导演审稿：把好四关——镜头时长、运镜、台词、分镜质量。
        用规则引擎检查，不调LLM（省时省钱），不合理直接修。"""
        fixes = 0
        warnings = []

        for i, s in enumerate(shots):
            desc = s.get("description", "")
            dialogue = s.get("dialogue", "")
            shot_type = s.get("shot_type", "中景")
            movement = s.get("camera_movement", "固定")
            duration = s.get("duration_sec", 5)
            emotion = s.get("emotion", "")
            director_shot = s.get("director_shot", "")
            scene = s.get("scene", "")

            # ═══ 关卡一：镜头时长把关 ═══
            # 1a. 有台词但时长不够说完 → 按字数算（每3字约1秒，最少8秒）
            if dialogue and dialogue != "(无台词)":
                # 去掉角色名前缀
                pure_lines = dialogue.replace("（", "(").split("(")[0].strip()
                for prefix in ["：", ":", "—", "－"]:
                    if prefix in pure_lines:
                        pure_lines = pure_lines.split(prefix)[-1].strip()
                needed_sec = max(8, len(pure_lines) // 3)
                if duration < needed_sec:
                    s["duration_sec"] = needed_sec
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 时长{duration}s不够说完台词({len(pure_lines)}字)→{needed_sec}s")

            # 1b. 无台词但时长<5秒 → 至少5秒
            if (not dialogue or dialogue == "(无台词)") and duration < 5:
                s["duration_sec"] = 5
                fixes += 1
                logger.info(f"[副导演] shot[{i}] 时长{duration}s太短→5s")

            # 1c. 情绪高潮镜头但<8秒 → 加到8秒以上
            climax_words = ["真相", "反转", "揭秘", "告白", "分手", "背叛", "崩溃", "爆发", "认出", "错过"]
            if any(w in desc for w in climax_words) and s.get("duration_sec", 5) < 8:
                s["duration_sec"] = 10
                fixes += 1
                logger.info(f"[副导演] shot[{i}] 高潮镜头但{duration}s太短→10s")

            # ═══ 关卡二：运镜把关 ═══
            # 2a. 情绪强烈但运镜静态 → 改动态
            strong_emotions = ["愤怒", "爆发", "崩溃", "震惊", "绝望", "激烈", "冲突", "高潮", "慌乱", "恐惧"]
            if any(e in emotion for e in strong_emotions) and movement in ("固定", "固定镜头", ""):
                if any(w in desc for w in ["打", "冲", "跑", "摔", "撞"]):
                    s["camera_movement"] = "手持跟拍"
                elif any(w in desc for w in ["泪", "哭", "颤", "抖"]):
                    s["camera_movement"] = "缓推"
                else:
                    s["camera_movement"] = "推"
                fixes += 1
                logger.info(f"[副导演] shot[{i}] 情绪'{emotion}'+运镜固定→{s['camera_movement']}")

            # 2b. 远景/大全景但运镜是"固定" → 大场景应该有运动感
            if shot_type in ("远景", "大远景", "大全景") and movement in ("固定", "固定镜头"):
                s["camera_movement"] = "航拍缓推" if any(w in desc for w in ["城市", "战场", "山脉", "大海", "建筑"]) else "缓推"
                fixes += 1
                logger.info(f"[副导演] shot[{i}] 大全景+固定→{s['camera_movement']}（大场景需要运动感）")

            # 2c. 连续3个相同运镜 → 中间换
            if i >= 2:
                m1 = shots[i-1].get("camera_movement", "固定")
                m2 = shots[i-2].get("camera_movement", "固定")
                if movement == m1 == m2 and movement != "固定":
                    alts = ["缓推", "拉", "手持跟拍", "正反打", "环绕"]
                    s["camera_movement"] = alts[i % len(alts)]
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 连续3个'{movement}'→{s['camera_movement']}")

            # ═══ 关卡三：台词把关 ═══
            # 3a. 描述像在说话但没台词 → 标记警告
            if (not dialogue or dialogue == "(无台词)"):
                speak_signals = ["说道", "开口说", "他说", "她说", "低声说", "喊道", "问道", "回答", "怒道", "笑道"]
                if any(w in desc for w in speak_signals):
                    warnings.append(f"shot[{i}] 描述含说话动作但无台词，可能漏了")
                    logger.info(f"[副导演] shot[{i}] ⚠️ 描述像在说话但无台词")

            # 3b. 台词和描述不匹配 → 标记
            if dialogue and dialogue != "(无台词)" and desc:
                # 台词里的角色名不在描述里 → 可能台词配错了镜头
                pass  # 这个需要语义理解，规则难判断，先不拦

            # 3c. 台词太长（超过100字）→ 一个镜头说不完，建议拆
            if dialogue and len(dialogue) > 100:
                warnings.append(f"shot[{i}] 台词{len(dialogue)}字太长，一个镜头说不完，建议拆成2个镜头")
                logger.info(f"[副导演] shot[{i}] ⚠️ 台词太长({len(dialogue)}字)")

            # ═══ 关卡四：分镜质量把关 ═══
            # 4a. 描述太短（<15字）→ 没有画面感
            if len(desc) < 15:
                s["description"] = desc + f"，{scene}场景，{emotion}氛围，角色有明确的肢体动作和表情变化"
                fixes += 1
                logger.info(f"[副导演] shot[{i}] 描述太短({len(desc)}字)→已补充")

            # 4b. 特写镜头但描述没有细节词 → 区分人物特写和物品特写
            if shot_type in ("特写", "大特写"):
                person_detail = ["眼", "手", "唇", "泪", "汗", "指", "瞳", "肌肉", "皮肤", "嘴角", "眉", "睫毛", "鼻尖", "脸"]
                object_detail = ["文件", "合同", "手机", "屏幕", "杯", "钥匙", "手表", "照片", "刀", "枪", "信", "书", "笔", "电脑", "桌", "门", "窗", "旗", "印章", "纸"]
                has_person = any(w in desc for w in person_detail)
                has_object = any(w in desc for w in object_detail)
                if not has_person and not has_object:
                    # 既没人物细节也没物品细节 → 根据剧情判断补什么
                    if any(w in desc for w in ["说", "看", "笑", "哭", "怒", "惊", "想"]):
                        s["description"] = desc.rstrip("。") + "，镜头贴近角色面部，能清晰看到皮肤纹理和微表情细节"
                    else:
                        # 补一个物品特写（暗示剧情）
                        obj_map = {"商战": "桌上的文件和签字笔", "都市": "手机屏幕和咖啡杯", "古装": "桌上的信件和印章", "战争": "手中的兵器和旗帜", "甜宠": "桌上的情侣杯和花", "悬疑": "桌面上的线索物品"}
                        obj_hint = obj_map.get(genre, "手中的关键道具")
                        s["description"] = desc.rstrip("。") + f"，镜头特写{obj_hint}，细节清晰可见"
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 特写缺细节→已补充({'人物' if has_person else '物品'})")

            # 4b2. 每3-4个镜头检查有没有特写 → 没有就提醒
            if i >= 3 and i % 4 == 3:
                recent_types = [shots[j].get("shot_type", "中景") for j in range(max(0, i-3), i+1)]
                if not any(t in ("特写", "大特写") for t in recent_types):
                    warnings.append(f"shot[{i-3}~{i}] 连续4个镜头无特写，缺少细节展示")

            # 4c. 连续3个相同景别 → 换
            if i >= 2:
                t1 = shots[i-1].get("shot_type", "中景")
                t2 = shots[i-2].get("shot_type", "中景")
                if shot_type == t1 == t2:
                    alt_map = {"近景": "全景", "中景": "近景", "特写": "中景", "全景": "近景", "远景": "中景", "大远景": "全景"}
                    s["shot_type"] = alt_map.get(shot_type, "中景")
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 连续3个{shot_type}→{s['shot_type']}")

            # 4d. director_shot 为空 → 按"起承转合"自动生成机位单
            d = s.get("duration_sec", 8)
            if not director_shot:
                parts = []
                mid = d // 2
                for sec in range(0, d, 2):
                    end_sec = min(sec + 2, d)
                    if sec == 0:
                        # 起：让观众看清这是什么场景/谁在
                        parts.append(f"第1-{end_sec}秒（起）：{s.get('camera_movement','固定')}，让观众看清{desc[:40]}，建立画面认知")
                    elif end_sec <= mid:
                        # 承：事情发生
                        parts.append(f"第{sec+1}-{end_sec}秒（承）：角色动作推进，{emotion}情绪展开，画面有变化")
                    elif sec + 2 >= d:
                        # 合：收尾，让观众消化
                        parts.append(f"第{sec+1}-{end_sec}秒（合）：镜头停留/微调，让观众看清结果和情绪，不能突然切走")
                    else:
                        # 转：关键变化
                        parts.append(f"第{sec+1}-{end_sec}秒（转）：关键画面信息，角色微表情变化，观众注意力集中")
                        parts.append(f"第{sec+1}-{end_sec}秒：画面保持{shot_type}，角色有细微动作")
                s["director_shot"] = "；".join(parts)
                fixes += 1

            # 4e. 无 emotion → 根据描述推断
            if not emotion or emotion == "中性":
                if any(w in desc for w in ["笑", "开心", "温暖"]):
                    s["emotion"] = "温馨"
                elif any(w in desc for w in ["哭", "泪", "悲伤"]):
                    s["emotion"] = "悲伤"
                elif any(w in desc for w in ["怒", "气", "愤"]):
                    s["emotion"] = "愤怒"
                elif any(w in desc for w in ["紧张", "危险", "害怕"]):
                    s["emotion"] = "紧张"
                else:
                    s["emotion"] = "平静"
                fixes += 1

            # ═══ 关卡五：物理逻辑把关（杜绝"还没跑就有刀""摔下来像气球"）═══
            # 5a. 有武器但没拔刀/持刀动作 → 补充
            weapon_words = ["刀", "剑", "枪", "斧", "矛", "戟", "弓"]
            has_weapon = any(w in desc for w in weapon_words)
            if has_weapon:
                weapon_action = ["拔", "握", "举", "挥", "持", "持握", "紧握", "抽出", "擎", "抄起"]
                if not any(w in desc for w in weapon_action):
                    # 武器出现了但没有持握动作 → 补充
                    desc_lower = desc
                    for w in weapon_words:
                        if w in desc_lower:
                            s["description"] = desc.replace(w, f"单手紧握{w}", 1) if f"握{w}" not in desc else desc
                            break
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 有武器但无持握动作→已补充")

            # 5b. 有马但没马的动作 → 补充马匹物理细节
            if "马" in desc or "骑" in desc:
                horse_actions = ["蹄", "鬃", "嘶", "颠簸", "奔腾", "飞奔", "踏地", "扬蹄", "马背"]
                if not any(w in desc for w in horse_actions):
                    s["description"] = desc.rstrip("。") + "，战马四蹄交替蹬地，马蹄踏在泥地上溅起碎泥，马鬃随风飞扬，骑手身体随马背起伏颠簸"
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 有马但无马匹动作细节→已补充")

            # 5c. 有摔倒/掉落但没物理过程 → 标记警告
            fall_words = ["摔", "掉下", "坠", "倒地", "跌", "翻倒"]
            if any(w in desc for w in fall_words):
                # 检查有没有过程描述
                fall_process = ["抓", "滑", "翻滚", "撞击", "惯性", "失去平衡", "前倾", "栽", "扑"]
                if not any(w in desc for w in fall_process):
                    warnings.append(f"shot[{i}] 有摔倒但缺物理过程（失去平衡→抓空→落地→翻滚）")
                    logger.info(f"[副导演] shot[{i}] ⚠️ 摔倒缺物理过程")

            # 5d. 有打斗但没有碰撞反馈 → 标记警告
            fight_words = ["劈", "砍", "刺", "砸", "挥", "攻击", "打"]
            if any(w in desc for w in fight_words):
                impact_words = ["火星", "碰撞", "铿", "挡", "闪", "格挡", "弹开", "冲击", "击退"]
                if not any(w in desc for w in impact_words):
                    warnings.append(f"shot[{i}] 有打斗但无碰撞反馈（火星/声音/格挡/击退）")
                    logger.info(f"[副导演] shot[{i}] ⚠️ 打斗缺碰撞反馈")
            # ═══ 关卡5e：动作链修正（眼泪/摔倒等要有完整过程）═══
            action_chain_fixes = {
                "流泪": "一道透亮的细水流从眼角沿颧骨蜿蜒淌下",
                "落泪": "透亮的水线从眼角出发顺着颧骨侧面淌到下颌",
                "泪流满面": "两道透亮细水流分别从双眼眼角沿颧骨侧面蜿蜒淌下至下颌汇合滴落",
                "哭了": "眼眶泛红一道透亮水线从眼角沿颧骨淌下",
                "含泪": "泪光在眼眶里闪烁打转但未落下眼角泛红",
                "微笑": "嘴角缓缓上扬眼角微微弯起",
                "摔在地上": "身体失去平衡前倾双膝先触地然后趴倒",
                "倒在地上": "身体摇晃后失去平衡侧身倒下",
            }
            for bad_a, good_a in action_chain_fixes.items():
                if bad_a in desc:
                    s["description"] = desc.replace(bad_a, good_a)
                    desc = s["description"]
                    fixes += 1
                    logger.info(f"[副导演] shot[{i}] 动作链: {bad_a}->完整过程")

            # ═══ 关卡5f：动作完整性智能补全（拿起笔→要写/放，不能悬空）═══
            incomplete = {
                "拿起": ["放下", "签", "写", "递", "摔", "翻看"],
                "站起来": ["走向", "看着", "说道", "拿起"],
                "推开门": ["走进去", "看到", "扫视"],
                "转过身": ["走向", "看着", "离开"],
                "伸手": ["握住", "抓住", "接过", "指向"],
                "抬起头": ["看着", "望向", "迎上"],
                "低下头": ["看着", "沉思", "避开目光"],
                "闭上眼": ["深呼吸", "叹了口气", "沉思片刻"],
                "握住": ["攥紧", "举起", "松开"],
                "翻开": ["看到", "读着", "合上"],
                "举起": ["挥向", "砸向", "指向"],
                "坐下": ["靠着", "看着", "叹气"],
                "拿出": ["打开", "递给", "放在桌上"],
                "抬手": ["指向", "挡住", "擦去"],
            }
            for a_start, a_ends in incomplete.items():
                if a_start in desc:
                    a_pos = desc.find(a_start)
                    after = desc[a_pos+len(a_start):a_pos+len(a_start)+15]
                    if not any(e in after for e in a_ends):
                        s["description"] = desc.replace(a_start, a_start+a_ends[0], 1)
                        desc = s["description"]
                        fixes += 1
                        logger.info(f"[副导演] shot[{i}] 动作补全: {a_start}->{a_start}{a_ends[0]}")


        # ═══ 关卡5g：LLM 动作完整性检查（豆包读一遍，补全悬空动作）═══
        try:
            import json as _json
            # 只取 description 和 director_shot 发给 LLM 检查（省 token）
            _check_items = []
            for ci, cs in enumerate(shots):
                _d = cs.get("description", "")
                _ds = cs.get("director_shot", "")
                if _d or _ds:
                    _check_items.append({"idx": ci, "desc": _d[:100], "shot": _ds[:100]})
            if _check_items:
                _check_prompt = (
                    "你是副导演。检查以下分镜每个镜头的动作描述是否完整。"
                    "每个动作必须有完整的因果链（起因到过程到结果），"
                    "不能出现拿起笔就没了、转过身就没了这种悬空动作。"
                    "返回JSON数组，每个元素含idx和fixed_desc（修正后的完整描述，原描述已完整就不用改）。"
                    " 分镜列表： " + _json.dumps(_check_items, ensure_ascii=False)
                )
                _llm_result = self._call_llm_json("你是副导演，检查动作完整性", _check_prompt, retries=0, timeout=60)
                if isinstance(_llm_result, list):
                    for item in _llm_result:
                        _fi = item.get("idx")
                        _fd = item.get("fixed_desc", "")
                        if _fi is not None and _fd and _fi < len(shots):
                            old_d = shots[_fi].get("description", "")
                            if _fd != old_d and len(_fd) > len(old_d):
                                shots[_fi]["description"] = _fd
                                fixes += 1
                                logger.info(f"[副导演] LLM动作补全 shot[{_fi}]")
                elif isinstance(_llm_result, dict) and "items" in _llm_result:
                    for item in _llm_result["items"]:
                        _fi = item.get("idx")
                        _fd = item.get("fixed_desc", "")
                        if _fi is not None and _fd and _fi < len(shots):
                            old_d = shots[_fi].get("description", "")
                            if _fd != old_d and len(_fd) > len(old_d):
                                shots[_fi]["description"] = _fd
                                fixes += 1
                                logger.info(f"[副导演] LLM动作补全 shot[{_fi}]")
        except Exception as _lle:
            logger.warning(f"[副导演] LLM动作检查跳过: {_lle}")

        # ═══ 关卡六：内容安全过滤（避免视频生成被审核拦截）═══
        try:
            from agents.content_safety import sanitize_shots
            shots = sanitize_shots(shots)
        except Exception as _cs:
            logger.warning(f"[副导演] 内容安全过滤跳过: {_cs}")

        # ═══ 关卡七：场景智能增强（音效/动作/表情/语气自动匹配）═══
        try:
            from agents.scene_intelligence import enhance_shots
            shots = enhance_shots(shots, genre)
            enhanced_count = sum(1 for s in shots if s.get("_enhanced"))
            if enhanced_count:
                logger.info(f"[副导演] 场景智能增强：{enhanced_count}/{len(shots)}镜已补充音效/动作/表情/语气")
        except Exception as _se:
            logger.warning(f"[副导演] 场景智能增强跳过: {_se}")

        # 汇总
        total_dur = sum(s.get("duration_sec", 8) for s in shots)
        logger.info(f"[副导演] 审稿完成：{fixes}处修正，{len(warnings)}条警告，总时长{total_dur}s（{total_dur//60}分{total_dur%60}秒）")
        for w in warnings:
            logger.info(f"[副导演] ⚠️ {w}")
        return shots

    def run(self, action: str = "generate", **kwargs) -> AgentResult:
        if action == "generate" or action == "regenerate":
            return self.generate_storyboard(
                kwargs.get("script", "") or kwargs.get("script_text", ""),
                kwargs.get("characters", []),
                kwargs.get("scenes", []),
                include_environment=kwargs.get("include_environment", False),
                costume_models=kwargs.get("costume_models", kwargs.get("params", {}).get("costume_models", None)),
                genre=kwargs.get("genre", kwargs.get("params", {}).get("genre", "")),
                director_tasks=kwargs.get("director_tasks", kwargs.get("params", {}).get("director_tasks", None)),
                director_analysis=kwargs.get("director_analysis", kwargs.get("params", {}).get("director_analysis", None)),
            )
        elif action == "revise":
            return self.revise_shot(
                kwargs.get("shot_data", {}),
                kwargs.get("feedback", "")
            )
        elif action == "optimize":
            return self.optimize_camera(kwargs.get("shots", []))
        return AgentResult(success=False, error=f"未知动作: {action}")


    def execute(self, script_text: str = "", characters: list = None, title: str = "", max_shots: int = 12, costume_models: list = None, genre: str = "", scenes: list = None, **kwargs):
        """唯一入口：生成分镜 — 根据剧本长度动态调整分镜数"""
        chars = characters or []
        models = costume_models or []
        scns = scenes or []
        # 按剧本长度动态计算分镜数
        script_len = len(script_text)
        if script_len <= 500:
            shots = max(5, min(8, max_shots))
        elif script_len <= 1000:
            shots = max(8, min(12, max_shots))
        elif script_len <= 2000:
            shots = max(10, min(15, max_shots))
        elif script_len <= 3000:
            shots = max(12, min(15, max_shots))
        else:
            shots = max(12, min(15, max_shots))
        logger.info(f"[Storyboard] 剧本{script_len}字, 动态分镜数: {shots}")
        return self.generate_storyboard(script_text, chars, scns, include_environment=False, costume_models=models, genre=genre,
                                       director_tasks=kwargs.get("director_tasks"), director_analysis=kwargs.get("director_analysis"))
