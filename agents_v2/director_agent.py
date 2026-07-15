# -*- coding: utf-8 -*-
"""
DirectorAgent V5 — 导演大脑：深度推理 + 智能分派 + 记忆驱动

导演读完剧本后，必须：
1. 理解剧本题材/风格/情绪
2. 推断视觉方案（构图/光影/色调/镜头语言）
3. 为每个角色设计具体外貌+服装
4. 为每个场景设计色调+光源+空间布局
5. 为分镜师设计镜头节奏
6. 为场景设计师设计场景方案
7. 为配音师设计声音方案
8. 为剪辑师设计转场方案
9. 查记忆：同类题材过去成功/失败经验，融入本次推理
"""
import json, logging, re, time
from core.agent_base_v3 import AgentV3
from services.model_client import UnifiedModel

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是短剧总导演，也是整个创作团队的决策中枢。你的职责：深度理解剧本，然后为每个部门下达精准指令。

【工作流程】
1. 完整阅读剧本，理解题材（古装/现代/仙侠/悬疑/甜宠/科幻...）
2. 推断视觉风格（构图方式、光影方案、色彩调性、镜头语言）
3. 提取剧本中的所有角色信息（姓名、性别、年龄、性格、外貌、服装）
4. 提取剧本中的所有场景信息（地点、时间、氛围、色调、光源）
5. 为分镜师设计镜头节奏（镜头数量、景别比例、转场方式）
6. 为场景设计师设计场景方案（色调、光源、空间布局、关键道具）
7. 为配音师设计声音方案（每个角色的音色、语速、情绪基调）
8. 为剪辑师设计转场方案（转场方式、剪辑节奏、特效需求、色彩调性）

【核心原则】
- 剧本内容原封不动保留，不修改不重写
- 只提取信息，不创作新内容
- 每个指令必须具体可执行，不能泛泛而谈

【四大段落模板自动匹配】
根据剧本内容自动识别段落类型，套用对应模板：
1）开篇氛围模板（0-25s）：铺垫人设假象，建立环境氛围
   - 镜1：全景交代环境（8-12s）
   - 镜2：中近景主角状态（7-12s）
   - 镜3：特写外力信号（3-6s）
2）双人对线冲突模板：家庭争吵、职场对峙
   - 镜1：近景甲方先发难（8-13s）
   - 镜2：侧脸特写主角冷淡回应（8-12s）
   - 镜3：近景对方情绪加剧（7-12s）
   - 镜4：面部特写主角输出观点（长台词拆分两段）（10-14s）
   - 镜5：特写对手错愕（5-10s）
3）高光觉醒反转模板：男主站起来输出金句
   - 镜1：中景主角停止动作，起身抬头（6-12s）
   - 镜2：双人中景主角输出价值观（10-14s）
   - 镜3：特写对手震惊反驳（5-10s）
   - 镜4：单人特写主角升华金句（10-14s）
4）结尾悬念钩子模板：标准化收尾
   - 镜1：中景对手抛出重磅消息（8-12s）
   - 镜2：全景安静留白（3-6s）
   - 镜3：侧脸慢推特写点睛反问（7-12s）
   - 镜4：定格人物侧脸黑屏（2-3s）

【角色外貌提取标准（真人短剧·纯视觉）】
每个角色只提取五个要素：年龄、服装、五官、头发/古代装饰、（有图就按图）。

输出格式：
{
  "name": "角色名",
  "gender": "男/女",
  "age": "年龄，如18-19岁",
  "wardrobe": "服装款式+颜色+材质+新旧。现代：宽松发白纯色短袖运动束脚裤旧拖鞋。古装：素色襦裙/红色披风/铠甲。仙侠：白色长袍/紫色法衣。",
  "features": "五官特征。例：双眼皮自然眼型眉毛淡杂乱嘴唇偏薄鼻子秀气圆脸下颌柔和。有参考图时以图为准。",
  "hair_accessory": "发型+头饰。现代：黑色短发久没修剪刘海遮眉自然乱微油。古装：发髻/簪花/发冠。仙侠：长发披肩+珠钗。"
}

注意：
- 有参考图时一切以图为准
- 只写长相外形，不写情绪神态
- 古装/仙侠要写古代头饰发冠簪花等

【输出JSON】
{
  "genre": "题材",
  "visual_style": "视觉风格方案，300字。必须具体描述：构图方式、光影方案、色彩调性、镜头语言、转场风格。根据题材给出差异化方案。",
  "core_conflict": "一句话冲突",
  "director_vision": "导演总纲：整体风格、视觉基调、叙事手法",
  "emotional_curve": "情绪变化曲线",
  "highlight_moments": ["高光时刻1", "高光时刻2", "高光时刻3"],
  "pacing_notes": "节奏要求",
  "paragraph_templates": ["开篇氛围", "双人对线", "高光觉醒", "结尾钩子"],
  "characters": [
    {
      "name": "角色名",
      "gender": "男/女",
      "age": "年龄",
      "wardrobe": "服装款式+颜色+材质+新旧",
      "features": "五官特征，有参考图以图为准",
      "hair_accessory": "发型+头饰，古装写发髻/簪花/发冠"
    }
  ],
  "scenes": [
    {
      "scene_num": 1,
      "location": "地点（从剧本提取）",
      "time": "白天/黑夜",
      "atmosphere": "氛围",
      "color_tone": "场景主色调",
      "lighting": "光源方案"
    }
  ],
  "服装设计方案": "按剧情发展列出每个角色的服装变化。古装：第1场穿素色襦裙，第3场换红色披风，第5场穿铠甲。现代：第1场休闲装，第3场换西装。仙侠：第1场白色长袍，第5场换紫色法衣。具体到款式颜色材质。",
  "道具设计方案": "按剧情发展列出关键道具。古装：玉佩、折扇、长剑、酒壶。现代：手机、公文包、咖啡杯。仙侠：法器、丹药、卷轴。具体到外观和使用场景。",
  "化妆设计方案": "每个角色的妆容方案。古装：淡妆/浓妆/油头。现代：日常妆/职业妆。仙侠：仙气妆/妖媚妆。具体到眼妆唇妆腮红。",
  "特效设计方案": "按剧情发展列出特效需求。古装：烟雾、火焰、雨水。仙侠：法术光效、飞剑、灵气。现代：霓虹、雨痕、血迹。具体到效果和出现时机。",
  "给角色设计师": "每个角色的服装+发型+妆容+气质。根据题材给方案：古装用汉服/旗袍+簪花，现代用休闲/职业装+配饰，仙侠用飘逸长袍+珠钗，悬疑用暗色系+帽子围巾。具体到款式颜色材质。",
  "给分镜师": "按剧本内容输出完整分镜表，严格遵守以下规则：\n\n【分镜规则】\n- 单镜头时长：6-12秒（最长不超过15秒）\n- 3分钟剧本：32-38个镜头\n- 对话镜头：每句台词1个镜头，长台词（>25字）拆成2个镜头\n- 情绪留白：2-5秒（沉默、眼神特写、停顿反应）\n- 钩子定格：2-3秒（片尾固定配置）\n\n【镜头类型定义】\n- 全景：交代空间环境、人物位置关系（开篇、多人同场）\n- 中景：膝盖以上，人物肢体动作、双人对话主力镜头\n- 中近景：腰以上，对话主流镜头\n- 近景：胸口以上，面部情绪\n- 特写：五官、手部、物品（门把手、手机、酒杯、眼神）\n- 侧脸特写：氛围感、人物内心戏（清醒男主、隐忍女主高频）\n\n【运镜守则】\n- 普通对话：固定机位，轻微缓推\n- 冲突升级：小幅缓慢前推\n- 高光觉醒：静态机位+轮廓光，少运镜\n- 结尾钩子：慢推至人物面部特写，最后定格\n\n【自动切镜逻辑】\n1. 标点切分：句号、问号、感叹号优先作为镜头分割点\n2. 人物切换：A说完→切A收尾特写→切B开始说话\n3. 动作描述：「放下鼠标，起身转身」单独分配镜头\n4. 情绪拐点：散漫→严肃，必须插入动作过渡镜头\n5. 留白插入：冲突结束、重磅消息出现，自动增加2-4秒静默镜头\n6. 时长分配：台词按语速（每秒3字）预估，空镜默认6-8s\n\n【输出格式示例】\n镜1｜00:00-00:12｜全景｜昏暗卧室，窗帘半拉，桌面堆满外卖盒\n镜2｜00:12-00:20｜中近景｜林小远瘫电竞椅，叼未点燃香烟，敲击键盘\n镜3｜00:18-00:20｜特写｜门把手转动，房门推开\n镜4｜00:20-00:33｜近景｜母亲进门，压抑怒火\n母：你到底要在家混到什么时候？\n镜5｜00:33-00:45｜近景｜母亲加重语气\n母：书不读班不上，外人都笑话我们家养了个废人！\n...\n\n【校验规则】\n1. 严禁添加剧本中没有的镜头\n2. 人物交替对话必须切镜头\n3. 连续3个特写后插入1个全景/中景\n4. 情绪拐点必须插入动作过渡镜头\n5. 长台词（>25字）强制拆分两个镜头\n6. 结尾必须有定格特写+留白钩子\n7. 总时长对应镜头数量：3分钟=32-38镜，低于28镜预警，高于42镜预警",
  "给场景设计师": "每个场景的色调+光源+空间布局+关键道具。根据题材给方案：古装用暖黄调+烛光/日光+木质家具+屏风，现代用自然光+城市霓虹+玻璃幕墙+金属，仙侠用冷蓝调+雾气柔光+玉石+仙草，悬疑用暗绿调+阴影+铁栏杆+雨痕。",
  "给配音师": "每个角色的声音年龄、语速、情绪基调、特殊语气",
  "给剪辑师": "转场方式、剪辑节奏快慢、特效需求、色彩调性",
  "director_analysis": "导演内心独白：你对这个剧本的理解，你脑中看到的画面，你想传达的情感。100-200字",
  "场景资产库": {
    "主场景设定图": [
      {
        "场景名": "林家卧室",
        "提示词": "9:16竖屏短剧场景图，狭小昏暗的年轻男生卧室，窗帘半拉，桌面堆满外卖盒、饮料瓶、游戏手柄、耳机和杂乱书本，电脑屏幕亮着游戏界面，床铺凌乱，墙边有电竞椅和简单置物架，整体空间压抑但真实，低亮度柔光，冷灰色调，轻微侧光，写实短剧画面质感，电影感构图，空间层次清晰，无人物",
        "情绪氛围图": [
          {"情绪": "颓废压抑", "提示词": "9:16竖屏短剧场景图，同一间狭小卧室，空间布局与原场景保持一致，桌面仍有外卖盒和电脑设备，光线变得更硬，门框方向有明显侧光，室内明暗对比增强，画面有家庭争吵前的紧张感，冷灰色调，真实短剧质感，电影感光影，无人物"},
          {"情绪": "高光觉醒", "提示词": "9:16竖屏短剧场景图，同一间狭小卧室，空间布局保持一致，窗帘缝隙透入冷白侧逆光，电脑屏幕微弱发亮，桌面杂乱但不抢画面，整体光线更有戏剧张力，突出人物即将觉醒反转的氛围，强明暗对比，写实短剧画面，电影感构图，无人物"},
          {"情绪": "沉默悬念", "提示词": "9:16竖屏短剧场景图，同一间狭小卧室，空间布局保持一致，房间突然安静，电脑屏幕变暗，窗外冷光微弱照入，室内低饱和，画面压抑克制，适合人物听到坏消息后的沉默段落，真实短剧质感，电影感光影，无人物"}
        ],
        "分镜机位参考图": [
          {"机位": "推门进入", "提示词": "9:16竖屏短剧分镜参考图，狭小卧室内，镜头从房间内侧看向门口，门把手正在转动，房门半开，母亲的身影出现在门口逆光处，室内桌面杂乱，电脑屏幕亮着，画面有压抑家庭冲突感，写实短剧画面，低饱和冷灰色调，电影感构图，中景机位，无夸张表情"},
          {"机位": "侧脸特写", "提示词": "9:16竖屏短剧分镜参考图，狭小卧室内，林小远侧脸对着电脑屏幕，电竞椅背影，电脑蓝光映脸，桌面外卖盒堆叠，画面压抑，写实短剧画面，冷灰色调，电影感构图，近景机位"},
          {"机位": "起身转身", "提示词": "9:16竖屏短剧分镜参考图，林小远从电竞椅上缓缓站起，转身面对母亲，褪去懒散，眼神沉静，侧逆光勾勒轮廓，背景是杂乱卧室，写实短剧画面，电影感光影，中近景机位"}
        ]
      }
    ],
    "跨集复用规则": "续集必须复用本集场景资产库，空间布局、光影风格、道具位置保持一致，不得重新设计"
  }
}

【硬性要求】
1. 所有字段必须填写，不得留空
2. appearance 必须详细描述：脸型+五官+发型+肤色+体型+穿搭，至少30字，要能直接用作AI生图提示词
3. wardrobe 必须具体到款式+颜色+材质
4. visual_style 必须根据题材给出差异化描述，不能泛泛而谈
5. 服装/道具/化妆/特效必须按剧情发展列出，不能笼统
6. 给每个部门的指令必须具体可执行，不能写"根据情况"或"待定"
7. 剧本原文一字不改，只提取信息
8. 给分镜师的指令必须包含完整分镜表（镜号+起止时间+景别+画面+台词）
9. 输出纯JSON
10. 每个角色必须有独立完整的外貌描写，不能简略写"年轻漂亮"或"帅气"，必须具体到五官形状、发型长度颜色、体型胖瘦高矮、穿着品牌风格"""


class DirectorAgent(AgentV3):
    name = "director"

    def execute(self, task: dict) -> dict:
        data = task.get("data", {})
        script = data.get("script_text", task.get("script_text", ""))
        genre_hint = data.get("genre", "")
        title = data.get("title", "")
        pipeline_id = task.get("pipeline_id", "")

        if not script or len(script.strip()) < 10:
            return {"success": False, "error": "剧本内容过短", "pipeline_id": pipeline_id}

        # 查记忆：同类题材过去成功/失败的经验
        evolution_tips = self._evolution_check(task)
        memory_context = ""
        if evolution_tips:
            memory_context = "\n\n【历史经验教训】\n" + "\n".join(evolution_tips)

        prompt = f"剧本：\n{script[:8000]}\n\n题材参考：{genre_hint or '都市'}\n{memory_context}\n请输出JSON。"

        try:
            result = self.call_with_safety_retry(
                None, 3,
                UnifiedModel.llm,
                prompt=prompt,
                system=SYSTEM_PROMPT,
                max_tokens=4096,
                timeout=300,
            )
            content = result.get("text", "{}")
            analysis = self._parse_json(content)

            if not analysis:
                logger.warning("[Director] JSON解析失败")
                return {"success": False, "error": "导演分析JSON解析失败", "pipeline_id": pipeline_id}

            # 整理输出
            chars = analysis.get("characters", [])
            scenes = analysis.get("scenes", [])

            analysis["refined_script"] = {
                "title": title or analysis.get("title", ""),
                "characters": chars,
                "scenes": scenes,
            }

            analysis["tasks"] = {
                "storyboard": analysis.pop("给分镜师", "") or "根据剧本设计分镜",
                "character": analysis.pop("给角色设计师", "") or "根据角色信息设计造型",
                "cinematographer": analysis.pop("给摄影师", "") or "根据场景设计摄影方案",
                "scene": analysis.pop("给场景设计师", "") or "根据剧本设计场景",
                "audio": analysis.pop("给配音师", "") or "根据角色性格设计配音",
                "video": analysis.pop("给剪辑师", "") or "根据节奏设计剪辑方案",
            }

            # 把 visual_style 也透传给下游
            analysis["visual_style"] = analysis.get("visual_style", "")

            g = analysis.get("genre", "")
            vs = analysis.get("visual_style", "")[:80]
            logger.info(f"[Director] genre={g}, visual_style={vs}..., chars={len(chars)}, tasks={list(analysis['tasks'].keys())}")

            resp = {"success": True, "pipeline_id": pipeline_id, **analysis}

            # 记录用量
            try:
                from services.usage_tracker import log_usage
                log_usage(
                    model_name="agnes-2.0-flash",
                    provider="agnes",
                    model_type="llm",
                    status="success",
                    user_id=task.get("data", {}).get("user_id", 0),
                    drama_id=pipeline_id,
                    char_count=len(script),
                )
            except Exception as e:
                logger.warning(f"[Director] 记录用量失败: {e}")

            return resp

        except Exception as e:
            logger.error("[Director] " + str(e))
            return {"success": False, "error": str(e)[:200], "pipeline_id": pipeline_id}

    def _parse_json(self, text: str) -> dict:
        if not text: return {}
        text = text.strip()
        # 1. 直接解析
        try: return json.loads(text)
        except: pass
        # 2. 提取 ```json ... ``` 块
        m = re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
        if m:
            try: return json.loads(m.group(1).strip())
            except: pass
        # 3. 提取 {} 并强制补全
        m = re.search(r'\{[\s\S]*', text)
        if m:
            raw = m.group(0)
            # 去掉末尾逗号
            raw = raw.rstrip()
            if raw.endswith(','):
                raw = raw[:-1]
            # 补全括号
            open_braces = raw.count('{')
            close_braces = raw.count('}')
            if open_braces > close_braces:
                raw += '}' * (open_braces - close_braces)
            # 补全字符串引号
            in_string = False
            escaped = False
            for i, ch in enumerate(raw):
                if escaped:
                    escaped = False
                    continue
                if ch == '\\':
                    escaped = True
                    continue
                if ch == '"':
                    in_string = not in_string
                elif ch == ':' and in_string:
                    # 字符串内的冒号，跳过
                    pass
            # 尝试解析
            try: return json.loads(raw)
            except: pass
            # 更激进：逐层剥离到最后有效的JSON
            for i in range(len(raw), 0, -10):
                try:
                    candidate = raw[:i].rstrip().rstrip(',')
                    if open_braces > close_braces:
                        candidate += '}' * (raw[:i].count('{') - raw[:i].count('}'))
                    return json.loads(candidate)
                except:
                    continue
        return {}

    def _evolution_check(self, task: dict) -> list:
        """查历史反思，返回进化建议"""
        try:
            genre = task.get("data", {}).get("genre", "")
            if not genre:
                return []
            similars = self.memory.find_similar(genre, limit=5)
            tips = []
            for s in similars:
                val = s.get("value", {})
                if isinstance(val, dict) and not val.get("success", True):
                    tips.append("上次同类失败: " + str(val.get("error", "?")))
            try:
                conn = self.memory._get_db()
                rows = conn.execute(
                    "SELECT content FROM agent_reflections WHERE user_id=? AND agent_type=? ORDER BY id DESC LIMIT 10",
                    (self.user_id, self.name)
                ).fetchall()
                conn.close()
                for r in rows:
                    content = r["content"] or ""
                    if "失败" in content and genre in content:
                        tip = content.replace("[失败] ", "").replace("[成功] ", "")
                        if tip not in tips:
                            tips.append(tip)
            except:
                pass
            return tips
        except Exception:
            return []
