"""智能体2：人设智能体 v2 — 多模型路由生成立绘+锁脸"""
import json, time, logging
from typing import Optional, Dict, List, Any
from .agent_base_legacy import BaseAgent, AgentResult
from .concurrency_pool import concurrency_pool
from .result_cache import get as cache_get, set as cache_set, download_and_cache as cache_download
try:
    from services.experience_engine import experience_engine
except Exception:
    experience_engine = None

logger = logging.getLogger(__name__)

CHARACTER_PROMPT = """你是一位金牌短剧角色设计师，深谙角色原型理论、人物弧线工程与观众共情心理学。你设计的角色让观众"一眼记住、三秒共情、追到底"。

【专业知识·角色设计】
▎短剧角色设计原则：
- 角色必须"一眼记住"：突出1-2个标志性特征（口头禅/动作/外貌记忆点）
- 每个角色要有"反差面"增加立体感（霸道总裁怕虫、冷血杀手养流浪猫）
- 配角不超过2个记忆标签，避免模糊
- 主角必须有"主动选择"时刻，不能全程被动挨打

▎角色原型库（基于荣格原型 + 短剧本土化）：
- 霸总型：控制欲强、外表冷酷、内心柔软、占有欲；原型=权威者+暗藏软肋
- 甜妹型：外表柔弱、内心坚韧、单纯但不傻；原型=天真者+幸存者
- 逆袭型：前期隐忍、遇契机爆发、势不可挡；原型=灰姑娘+战士觉醒
- 反派型：有动机的恶、非纯坏、有高光时刻；原型=阴影+合理创伤
- 智囊型：话少、精准、洞察力强；原型=智者
- 白月光型：完美但易碎、推动主角执念；原型=理想
- 绿茶型：表柔弱内算计、制造误会冲突；原型=变形者

▎人物弧线三阶段（每个主要角色必填 arc 字段）：
- 起始态：角色出场时的状态（弱势/伪装/执念/麻木）
- 转折触发：什么事件打破平衡（背叛/遇见/真相/危机）
- 终末态：角色完成怎样的转变（觉醒/黑化/释怀/毁灭）

▎动机层次（让角色行为合理可信）：
- 表层动机：角色自己以为想要的（如：复仇、赚钱、追爱）
- 深层动机：真正驱动的心理需求（如：渴望被认可、恐惧被抛弃、证明自我价值）
- 冲突来源：表层与深层动机的矛盾制造内心戏

▎形象设计建议：
- 身高体重配比要合理
- 发型=角色性格外化（洒脱=长发飘/干练=短发/隐忍=遮眼刘海）
- 着装风格呼应职业和性格，且有色彩心理学暗示
- 标志性配饰形成记忆点（项链/耳钉/手表/发夹）
- 整体描述要可视化（可被AI绘图直接使用）

▎音色匹配（指导下游 TTS 选角）：
- 霸总：低沉磁性、语速慢、有压迫感
- 甜妹：清脆明亮、语调上扬、活泼
- 沉稳型：中音平稳、气息稳定
- 反派：低沉带笑、拖尾音、有穿透力
- 少年：清亮带稚气、语速快
- 御姐：成熟醇厚、尾音下沉、气场强

返回JSON格式（不要markdown代码块）：
{
  "name": "角色名",
  "basic": {"gender": "男/女", "age": 25, "height_cm": 175, "body_type": "消瘦/匀称/健壮/丰满", "face_shape": "圆脸/方脸/瓜子脸/鹅蛋脸", "hair_style": "发型描述", "hair_color": "发色", "distinctive_features": ["特征1", "特征2"]},
  "personality": {"type": "MBTI类型", "traits": ["性格特质1", "性格特质2"], "motivation": "表层动机", "deep_motivation": "深层心理需求", "archetype": "角色原型"},
  "style": {"clothing_style": "着装风格", "color_palette": ["主色1", "主色2"], "signature_accessory": "标志性配饰"},
  "voice": {"tone": "音色描述", "speed": "slow/normal/fast", "emotion_range": ["情绪1", "情绪2"]},
  "backstory": "角色背景故事（100-200字）",
  "arc": "角色成长弧线：起始态 → 转折触发 → 终末态"
}"""


class CharacterAgent(BaseAgent):
    name = "人设智能体"
    description = "角色设计、立绘生成、多模型路由锁脸"
    version = "2.0.0"

    def create_character(self, name: str, role_type: str, script_context: str) -> AgentResult:
        start = time.time()
        try:
            user_prompt = f"角色名：{name}\n角色类型：{role_type}（主角/配角/反派）\n剧本上下文：{script_context[:2000]}"
            result = self._call_llm_json(CHARACTER_PROMPT, user_prompt, retries=3)
            result["material_matches"] = self._match_materials_for_char(result)
            return AgentResult(data=result, duration_ms=int((time.time() - start) * 1000))
        except Exception as e:
            logger.error(f"创建角色人设失败: {e}")
            return AgentResult(success=False, error=str(e))

    def _build_figure_prompt(self, params: dict) -> str:
        import re as _re
        name = params.get('name', params.get('char_name', '角色'))
        appearance = params.get('appearance', '')
        clothing = params.get('outfit', params.get('clothingStyle', ''))
        reference = params.get('reference_image', params.get('ref_photo', ''))
        
        # 智能解析 description（可能包含 姓名+角色类型+性别+年龄等拼接）
        raw_desc = str(appearance)
        age_from_desc = ''
        gender_from_desc = ''
        # 尝试从 description 提取年龄
        _age_m = _re.search(r'(\d{1,3})\s*岁', raw_desc)
        if _age_m: age_from_desc = _age_m.group(1)
        # 尝试从 description 提取性别
        if '男' in raw_desc: gender_from_desc = '男'
        elif '女' in raw_desc: gender_from_desc = '女'
        
        base = f"角色{name}"
        gender_val = params.get('gender') or gender_from_desc
        if gender_val: base += f"，{gender_val}性"
        
        age_val = params.get('age') or age_from_desc
        if age_val:
            try: a = int(age_val)
            except Exception: a = 30
            if a <= 12: age_desc = f"{a}岁儿童"
            elif a <= 17: age_desc = f"{a}岁青少年"
            elif a <= 25: age_desc = f"{a}岁年轻{ '女性' if gender_val=='女' else '男性' }"
            elif a <= 35: age_desc = f"{a}岁{ '女性' if gender_val=='女' else '男性' }"
            elif a <= 50: age_desc = f"{a}岁中年{ '女性' if gender_val=='女' else '男性' }"
            else: age_desc = f"{a}岁老年{ '女性' if gender_val=='女' else '男性' }"
            base += f"，{age_desc}"
        
        if h := params.get('height'): base += f"，身高{h}"
        if bt := params.get('bodyType'): base += f"，体型{bt}"
        if fsh := params.get('faceShape'): base += f"，脸型{fsh}"
        if hs := params.get('hairStyle'): base += f"，发型{hs}"
        if clothing:
            if isinstance(clothing, list): clothing = '、'.join(clothing)
            base += f"，穿着{clothing}"
        
        # 原始外貌描述
        if appearance:
            desc_text = str(appearance)
            # 去掉已提取的年龄/性别信息，保留外貌细节
            desc_text = _re.sub(r'\d{1,3}\s*岁', '', desc_text)
            desc_text = _re.sub(r'[男女]性?', '', desc_text)
            desc_text = desc_text.replace(name, '').strip('，, ')
            # 过滤夸张化形容（反派靠表演不靠长相）
            banned = ['奸诈', '阴险', '凶恶', '狰狞', '丑陋', '邪恶', '猥琐', '狡诈', '阴森', '可怕',
                         '苍白', '煞白', '惨白', '蜡黄', '铁青',
                         '深陷', '凹陷', '浮肿', '红肿', '歪斜',
                         '阴鸷', '诡', '狡黠', '蛇蝎', '虎狼',
                         '魔焰', '黑气', '邪气', '鬼气', '戾气',
                         '披散', '凌乱', '蓬乱']
            for w in banned:
                desc_text = desc_text.replace(w, '')
            desc_text = desc_text.strip('，, ')
            if desc_text: base += f"，{desc_text}"
        
        # 参考图提示（增强换脸一致性）
        ref_hint = ""
        if reference:
            ref_hint = "，面部特征与参考照片保持一致"
        
        # 性别化美颜关键词（水灵感+真实质感）
        if gender_val == '女':
            gn_cn = "，肤色自然健康，自然肤质质感，五官端正清秀，眼神清澈自然明亮，真实人像气质"
            gn_en = ", natural healthy complexion, real skin texture, refined natural features with fresh aura, natural bright eyes, real human photo quality"
        else:
            gn_cn = "，肤色健康均匀，自然肤质质感，轮廓分明五官立体，眼神自然深邃，真实人像气质"
            gn_en = ", healthy clear complexion, real skin texture, sharp natural features, deep natural eyes, real human photo quality"
        style_val = params.get("style", params.get("genre", ""))
        # Fallback: detect era from character description if genre not explicitly set
        if not style_val:
            char_desc = str(params.get("description", params.get("appearance", params.get("personality", ""))))
            if any(kw in char_desc for kw in ["古","仙","修","侠","道","袍","剑","鼎","宫","殿","袍","簪"]):
                style_val = ""
            elif any(kw in char_desc for kw in ["现","都","市","校","公","司","电","车","机"]):
                style_val = ""
        era_hint_cn = ""
        era_hint_en = ""
        if style_val == "现代":
            era_hint_cn = "，现代都市风格，时尚现代发型和服装，现代妆容，现代时装造型"
            era_hint_en = ", modern urban style, fashionable modern hairstyle and clothing, modern makeup, contemporary fashion style"
        elif style_val in ("古风", "仙侠", "武侠", "宫廷", "玄幻"):
            era_hint_cn = f"，{style_val}风格造型"
            era_hint_en = f", {style_val} style costume"
        # 剧集上下文（角色所属的剧）
        drama_title = params.get("title", params.get("drama_name", ""))
        drama_genre = params.get("style", params.get("genre", ""))
        if drama_title:
            drama_ctx = f"，来自{drama_genre}剧《{drama_title}》" if drama_genre else f"，来自剧《{drama_title}》"
        else:
            drama_ctx = ""
        cn = f"一位中国真人，{base}{ref_hint}{drama_ctx}，正脸证件照式正面肖像，脸部完全正对镜头双眼直视相机不能侧脸不能转头，面部特写，肩膀以上构图，脸部占画面主体，正面向前，专业人像摄影自然光，自然真实肤质{gn_cn}，专业人像精修，8K超高清，真实人像照片，真人实拍电影摄影质感"
        en = f"photorealistic Chinese person, straight-on mugshot-style frontal portrait, face absolutely centered facing camera directly, both eyes looking straight at lens, perfectly symmetrical frontal view, head and shoulders framing, face fills the composition, professional portrait photography natural light, natural real skin texture{gn_en}, professional portrait retouching, max beauty mode, flawless airbrushed skin, face retouched, perfect complexion, 8K, real photograph, live action portrait photography, cinematic photorealistic human"
        return f"{cn} | {en}"


    def extract_characters(self, script: str, director_task: str = '', genre: str = '') -> AgentResult:
        """从剧本中提取角色 - 含名字+描述+性格+外貌，使用LLM深度理解"""
        start = __import__("time").time()
        system = (
            "你是一个专业的剧本角色分析师。请先完整阅读剧本，理解整个故事后再提取角色。\n\n"
            "规则：\n"
            "1. 只提取剧中【实际出场露脸】的角色（有画面、有台词或有行动）\n"
            "2. 以下类型角色【必须过滤，不要提取】：\n"
            "   - 仅通过电话/回忆/旁白提及，未实际出场\n"
            "   - 无台词、仅作为背景动作描写（如「服务员端上咖啡」「路人经过」）\n"
            "   - 全剧出场≤1个镜头且无推动剧情作用的纯背景人物\n"
            "3. 分析每个人物的：名字、性别(男/女)、角色类型(主角/配角/反派/龙套)、性格特征、外貌\n"
            "4. 外貌(appearance)必须包含以下五个维度，用逗号分隔，50-80字：\n"
            "   ①体形：身高(高/中等/矮)、胖瘦(清瘦/匀称/微胖)、身形特征(单薄/含胸/挺拔)\n"
            "   ②脸型五官：脸型(鹅蛋脸/圆脸/方脸)、眉毛(浓密/淡/杂乱)、眼睛(大小/眼皮单双/眼神特征)、鼻子、嘴唇(厚/薄)、肤色(白/黄/黑)、皮肤状态(光滑/暗沉/黑眼圈/细纹)\n"
            "   ③发型：长度(短发/中长/长发)、颜色(黑/棕/白)、造型(凌乱/整齐/扎马尾)、打理状态(蓬松/油塌/精心)\n"
            "   ④穿着：上衣(款式+颜色+材质+新旧)、下装(款式+颜色+材质)、鞋履\n"
            "   ⑤标志特征(可选)：眼镜、胡茬、特定饰品\n"
            "   ⚠️ 外貌只写固定身体和穿着特征，以下严禁写入：\n"
            "   - 叼烟/抽烟/吃东西 → 动作不是外貌\n"
            "   - 瘫坐/倚靠/跷腿/叉腰/手插兜 → 姿态不是外貌\n"
            "   - 皱眉/微笑/冷峻/懒散/麻木 → 表情情绪不是外貌\n"
            "   - 敲键盘/拿手机/拎包 → 动作道具不是外貌\n"
            "   正确示例：「中等身高偏清瘦，鹅蛋脸，淡眉杂乱眼皮微耷瞳孔深邃，唇薄，肤色苍白带黑眼圈。黑色短发凌乱微油。宽松旧白T恤洗得发白，薄运动裤光脚拖鞋。」\n"
            "   错误示例：「嘴里叼着烟，瘫在电竞椅上，眼神懒散地打游戏」← 全是动作/姿态/表情，不合格\n"
            "5. 导演可通过'【保留角色】'指令强制保留指定龙套\n"
            + ("\n【导演指令】" + director_task[:500] + "\n" if director_task else "") + "\n"
            "以JSON数组格式返回：\n"
            + '{"characters": [{"name": "角色名", "type": "主角/配角/反派/龙套", "description": "描述25字内", '
            + '"personality": "性格15字内", "appearance": "外貌50-80字(体形+五官+发型+穿着)", "role_notes": "设定30字内"}]}'
        )
        genre_hint = "\n【剧集类型】{}: 角色外貌、服装、气质必须符合{}剧设定。".format(genre, genre) if genre else ""
        # 完整剧本传给LLM，最多12000字
        result = self._call_llm_json(system, '══════ 信息权重：完整剧本 > 导演指令 > 局部分片 ══════\n\n' + '══════ 完整剧本 ══════\n\n' + script[:12000] + genre_hint + "\n\n══════ 请分析以上剧本，提取所有实际出场角色 ══════", temp=0.15, retries=3, timeout=90)
        chars = result.get('characters', [])
        # 容错：如果LLM返回单角色对象而非数组
        if not chars and isinstance(result, dict) and 'name' in result:
            chars = [result]
            logger.info("[Agent] 角色提取：LLM返回单角色对象，自动包装")
        if not isinstance(chars, list):
            chars = []
        refined = []
        for c in chars:
            if isinstance(c, dict):
                refined.append({
                    'name': c.get('name', str(c)),
                    'type': c.get('type', c.get('role_type', '配角')),
                    'gender': c.get('gender', ''),
                    'description': c.get('description', c.get('desc', '')),
                    'personality': c.get('personality', ''),
                    'appearance': c.get('appearance', ''),
                    'role_notes': c.get('role_notes', c.get('notes', '')),
                })
            else:
                refined.append({'name': str(c), 'type': '配角', 'gender': '', 'description': '', 'personality': '', 'appearance': '', 'role_notes': ''})
        if not refined:
            _names = set()
            for _m in __import__('re').finditer(r'[（(]\s*(.{1,4})\s*[)）]', script):
                _names.add(_m.group(1))
            for _m in __import__('re').finditer(r'【(.{1,4})】', script):
                _names.add(_m.group(1))
            refined = [{'name': n, 'type': '配角', 'gender': '', 'description': '', 'personality': '', 'appearance': '', 'role_notes': ''} for n in _names if n]
            if not refined:
                refined = [{'name': '主角', 'type': '主角', 'gender': '', 'description': '', 'personality': '', 'appearance': '', 'role_notes': ''}]

        # ═══ 集合比对：全剧本出场人物 vs 过滤后核心角色 ═══
        import re as _re_set
        _all_names = set()
        # 从剧本提取所有角色名（对话标记 + 描述标记）
        for _m in _re_set.finditer(r'(?:^|\n)\s*([^\s：:，,。！!（(]{2,4})[：:]', script[:12000]):
            _all_names.add(_m.group(1))
        for _m in _re_set.finditer(r'【([^】]{1,8})】', script[:12000]):
            _all_names.add(_m.group(1))
        # 过滤常见非角色词
        _noise = {'第','如果','但是','然而','因为','所以','于是','接着','突然','同时','此外','另外','最后','最终','镜头','画面','旁白','字幕','场景','切换','备注','注意','提示'}
        _all_names = {n for n in _all_names if n not in _noise and len(n) >= 2}
        _output_names = {c.get('name','') for c in refined if c.get('name')}
        _filtered_out = _all_names - _output_names
        _extra = _output_names - _all_names
        if _filtered_out:
            logger.info(f"[Character] 全剧本{len(_all_names)}人 → 输出{len(_output_names)}人, 过滤{len(_filtered_out)}龙套: {sorted(_filtered_out)[:10]}")
        if _extra:
            logger.warning(f"[Character] 输出多出剧本未出现的角色: {_extra}")
        # 标记过滤数量
        _result_data = {'characters': refined, 'total_in_script': len(_all_names), 'filtered_out': list(_filtered_out)[:20], 'kept': len(_output_names)}

        return AgentResult(data=_result_data, duration_ms=int((time.time()-start)*1000))

    def complete_character(self, name: str, script_context: str = "") -> AgentResult:
        """补齐单个角色信息 - 使用LLM"""
        start = __import__("time").time()
        system = '你是专业角色设计师。根据角色名和剧本上下文，生成角色详细信息。以JSON格式返回：{"name": "角色名", "gender": "male/female", "age": "青年/中年/老年/少年", "appearance": "外貌描述40-80字，用于AI生图。必须包含:发型+五官+衣着+气质。例如:白衣胜雪长发束冠，剑眉星目面容俊朗，腰悬三尺青锋，气质清冷出尘", "personality": "3-5个性格词+一句话描述30字内", "voice_type": "配音类型", "basic": {"face_shape": "脸型", "hair_style": "发型", "gender": "male/female"}}'
        user = '角色名：' + name + '\n剧本上下文：' + (script_context or '(无)')
        result = self._call_llm_json(system, user, temp=0.3)
        if not result:
            result = {'name': name, 'gender': '未知', 'age': '', 'appearance': '', 'personality': '', 'voice_type': ''}
        result.get("material_matches") or result.update({"material_matches": self._match_materials_for_char(result)})
        return AgentResult(data=result, duration_ms=int((time.time()-start)*1000))

    def _count_characters(self, script: str) -> list:
        import re
        names = set()
        patterns = [r'[（(]\s*(.*?)[)）]', r'【(.+?)】', r'[：:]\s*(\S{2,4})[：:]']
        for pat in patterns:
            for m in re.finditer(pat, script):
                name = m.group(1).strip()
                if 1 < len(name) < 6 and not re.search(r'[0-9a-zA-Z]', name):
                    names.add(name)
        return list(names)

    def generate_figure(self, **params) -> AgentResult:
        """生成角色立绘（多模型路由 via route_manager，带缓存）"""
        start = time.time()
        model_routes = params.get("model_routes") or {}
        prompt = self._build_figure_prompt(params)

        from services.model_client import UnifiedModel

        # 有参考图 → 图生图换脸（优先级最高，跳过所有缓存）
        if params.get("reference_image") and (params["reference_image"].startswith("http") or params["reference_image"].startswith("https")):
            logger.info(f"[CharacterAgent] 图生图模式: reference={params['reference_image'][:80]}")

            # 提取角色外观描述
            character = params.get("character", {})
            appearance_text = str(params.get("description",
                params.get("appearance",
                character.get("appearance",
                character.get("description", char_name or "角色")))))

            # [504修复] 砍掉 LLM 优化描述这步——它最坏重试 12 次×15s=180s，
            # 且第 299 行 appearance_text 会重新从 params 读取，把这里的优化结果覆盖掉，
            # 所以这步既耗时又无用，是 504 的主要元凶之一。
            # i2i prompt 已在第 380-388 行构建了详细的保脸+换装指令，足够用。

            # 构建图生图专用 prompt：保留脸型轮廓，发型服饰用剧本
            import re as _re2
            name = params.get('name', '角色')
            # 使用工具优化后的描述（优先用 description，工具会优化它）
            appearance_text = str(params.get('description', params.get('appearance', '')))
            gender_val = params.get('gender') or ('男' if '男' in appearance_text else ('女' if '女' in appearance_text else ''))
            age_val = params.get('age') or 0
            # 如果前端没传年龄（age=0），尝试从描述文本中提取
            if not age_val or age_val == 0:
                age_match = _re2.search(r'(\d+)\s*岁', appearance_text)
                if age_match:
                    age_val = int(age_match.group(1))
                else:
                    # 尝试从纯数字匹配（如 "27，温柔"）
                    age_match2 = _re2.search(r'(\d{1,3})\b', str(params.get('description', '')))
                    if age_match2:
                        age_val = int(age_match2.group(1))
            if not age_val or age_val == 0:
                age_val = 30  # 最终兜底

            # 年龄描述
            try: a = int(age_val)
            except Exception: a = 30
            if a <= 12: age_desc = f"{a}岁儿童"
            elif a <= 17: age_desc = f"{a}岁青少年"
            elif a <= 25: age_desc = f"{a}岁年轻{'女性' if gender_val=='女' else '男性'}"
            elif a <= 35: age_desc = f"{a}岁{'女性' if gender_val=='女' else '男性'}"
            elif a <= 50: age_desc = f"{a}岁中年{'女性' if gender_val=='女' else '男性'}"
            else: age_desc = f"{a}岁老年{'女性' if gender_val=='女' else '男性'}"

            # 清理外貌文本，移除年龄数字和性别字，保留描述
            clean_appearance = _re2.sub(r'\d{1,3}\s*岁', '', appearance_text)
            clean_appearance = _re2.sub(r'[男女]性?', '', clean_appearance)
            clean_appearance = clean_appearance.replace(name, '').strip('，, ')

            # 图生图专用 prompt：强调保留参考图脸型轮廓，其他用剧本描述
            # 剧集上下文（加入到img2img prompt中以保持风格一致性）
            drama_title = params.get("title", params.get("drama_name", ""))
            drama_genre = params.get("style", params.get("genre", ""))
            if drama_title:
                drama_ctx_cn = f"，{drama_genre}剧《{drama_title}》" if drama_genre else f"，剧《{drama_title}》"
                drama_ctx_en = f", {drama_genre} drama \"{drama_title}\"" if drama_genre else f", drama \"{drama_title}\""
            else:
                drama_ctx_cn = ""
                drama_ctx_en = ""
            # 简化服装描述：只提服装大类（古装/现代），不提面部细节
            # Auto-detect era from character fields if genre not set
            _genre = params.get("genre","") or params.get("style","")
            if not _genre:
                _ch = str(params.get("description","")) + str(params.get("appearance",""))
                if any(k in _ch for k in ["古","仙","修","侠","袍","剑","簪"]):
                    _genre = "古风"
            # 智能检测服化道风格
            _genre_lower = _genre.lower() if _genre else ""
            _ch = str(params.get("description","")) + str(params.get("appearance","")) + str(params.get("personality",""))
            _title = str(params.get("title",""))
            if "古" in _genre_lower or "仙" in _genre_lower or "修" in _genre_lower or "侠" in _genre_lower:
                costume_simple = "古装"
            elif "民国" in _genre_lower or "谍" in _genre_lower or "军" in _genre_lower or "抗" in _genre_lower:
                costume_simple = "民国年代装"
            elif "科幻" in _genre_lower or "赛博" in _genre_lower or "未来" in _genre_lower:
                costume_simple = "科幻未来风"
            elif "奇幻" in _genre_lower or "魔法" in _genre_lower or "异世" in _genre_lower:
                costume_simple = "奇幻风格"
            elif "都市" in _genre_lower or "青春" in _genre_lower or "校园" in _genre_lower or "现言" in _genre_lower:
                costume_simple = "现代时尚装"
            elif "悬疑" in _genre_lower or "推理" in _genre_lower or "恐怖" in _genre_lower:
                costume_simple = "现代日常装"
            elif any(k in _ch for k in ["古","仙","修","侠","袍","剑","簪","宫","扇"]):
                costume_simple = "古装"
            elif any(k in _title+_ch for k in ["民国","抗战","军","谍"]):
                costume_simple = "民国年代装"
            elif any(k in _ch for k in ["科幻","赛博","机甲","未来"]):
                costume_simple = "科幻未来风"
            elif any(k in _ch for k in ["都市","校","公","司","CEO","总裁","律师","医生","警察","播主"]):
                costume_simple = "现代时尚装"
            else:
                # 无明确线索 → 不预设服装，让模型根据角色描述自己决定
                costume_simple = ""  # 不限定，自由生成
            # 面部保持（不添加美颜描述，避免模型改脸）
            beauty_cn = "自然真实肤质"
            beauty_en = "natural real skin texture"
            # 风格提示：优先 drama_ctx，其次 costume_simple
            costume_hint_cn = f"，{costume_simple}，" if costume_simple else "，"
            _ctx_cn = f"{drama_ctx_cn}" if drama_ctx_cn else (f"{costume_simple}风格" if costume_simple else "")
            i2i_cn = ("严格保持参考照片人脸完全不变：五官、脸型、眼睛、鼻子、嘴巴、眉毛与参考照片完全一致，"
                "禁止任何面部改变，禁止整容，禁止美颜磨皮，仅可更换服装和发型"
                + (_ctx_cn if _ctx_cn else ""))
            costume_hint_en = f", {costume_simple} style, " if costume_simple else ", "
            _ctx_en = f"{drama_ctx_en}" if drama_ctx_en else ""
            i2i_en = ("IDENTICAL FACE as reference: keep facial identity 100% the same, eyes nose mouth chin jawline UNCHANGED, "
                "NO face alteration NO plastic surgery NO skin smoothing, ONLY change clothing and hairstyle"
                + (_ctx_en if _ctx_en else ""))
            i2i_prompt = f"{i2i_cn} | {i2i_en}"

            client_result = UnifiedModel.image_to_image(
                prompt=i2i_prompt,
                reference_image=params["reference_image"],
                size="1920x1920",
                timeout=120,
                strength=0.08
            )
        else:
            # 查缓存（跳过过期 OSS/TOS 临时 URL，避免返回 403 链接）
            cached = cache_get(prompt, "character", "1920x1920")
            if cached and cached.get("image_url"):
                cached_url = cached["image_url"]
                _EXPIRED = ("aliyuncs.com", "volces.com", "dashscope", "ark-acg")
                if not any(d in cached_url for d in _EXPIRED):
                    logger.info(f"[CharCache] HIT for char prompt[:50]={prompt[:50]!r}")
                    return AgentResult(success=True, data={"figure_url": cached_url, "prompt": prompt,
                                                             "model": cached.get("model", "cache"), "cached": True},
                                       duration_ms=0)
                else:
                    logger.info(f"[CharCache] SKIP expired URL in cache, regenerating")
            # 缓存未命中，走文生图 — 人脸特写优先seedream（人脸优化链）
            client_result = UnifiedModel.image(
                prompt=prompt,
                size="1920x1920",
                timeout=120
            )

        if client_result.get("success"):
            url = client_result["url"]
            cache_set(prompt, "character", "1920x1920", {"image_url": url, "model": client_result.get("model", "")})
            local = cache_download(url, prompt, client_result.get("model", "figure"), "1920x1920")
            persistent_url = UnifiedModel.download_to_storage(url, params.get("name", "") or params.get("char_name", "") or "figure", user_id=params.get("user_id", 0), project_id=str(params.get("project_id", "")))
            try:
                if experience_engine:
                    experience_engine.log_generation("agent_character", "generate_figure", "", prompt, url, "", True, 4)
            except Exception as ex_: logger.warning(f"[agent_character]  {ex_}")
            return AgentResult(
                success=True,
                data={"figure_url": persistent_url, "prompt": prompt, "model": client_result.get("model", ""),
                      "local_path": local or ""},
                duration_ms=int((time.time()-start)*1000)
            )

        return AgentResult(
            success=False,
            data={"figure_url": ""},
            error=client_result.get("error", "所有模型均失败")
        )

    def _generate_modeling_brief(self, char_name: str, gender: str, age: str,
                                  style: str, description: str) -> dict:
        """调用 DeepSeek 生成专业角色建模说明书"""
        from services.model_client import UnifiedModel

        gender_cn = {"male":"男","female":"女","男":"男","女":"女"}.get(gender, gender or "中性")

        system_prompt = """你是一个专业的角色建模设计师。你的任务是根据用户提供的角色信息，生成一份结构化的角色建模说明书。

说明书必须包含以下字段（JSON格式）：
{
  "identity": "角色身份一句话描述，例如'古代守护边疆的青年名将'",
  "face": "面部特征详细描述，包括脸型、眉目、鼻梁、嘴唇、肤色等",
  "hair": "发型详细描述，包括长度、颜色、发型、发饰、头冠、簪钗等发饰（古装角色必须写明：如\"束发金冠\"\"高髻盘发戴凤钗\"\"将军兜鍪\"等）",
  "clothing": "服装详细描述，包括内搭外披、颜色、款式",
  "armor_or_accessories": "盔甲或配饰描述（如有），包括材质、纹理、图案",
  "headwear": "头冠/头盔/发饰描述，古装历史角色必须写明具体头饰（如：凤翅盔、束发金冠、珠钗步摇等）",
  "materials": "材质对比描述，如'精钢银甲的反光质感' vs '粗布白袍的哑光质感'",
  "weapons_or_props": "武器或道具描述（如有）",
  "pose": "推荐姿态描述",
  "atmosphere": "光影氛围，如'侧逆光，电影级明暗对比，史诗感'",
  "battle_damage": "战损或真实感细节，如'侧脸刀疤、黄沙沾染、披风裂口'",
  "dynamic_elements": "动态元素，如'披风飞扬、发丝凌乱'",
  "quality_tags": "推荐的质量权重标签，如'(masterpiece, best quality, 8k, ultra-detailed:1.3)'"
}

要求：
- 每个字段用中文描述，越详细越好
- 突出材质对比和动态感
- 严格符合角色身份和时代背景
- 返回纯 JSON，不要 markdown 代码块包装"""

        user_prompt = f"""
角色名：{char_name}
性别：{gender_cn}
年龄：{age or '青年'}
题材风格：{style or '现代'}
角色描述：{description or '无额外描述'}
"""

        try:
            result = UnifiedModel.llm(
                prompt=user_prompt,
                system=system_prompt,
                timeout=25,
                max_tokens=2048
            )
            if result and result.get("success"):
                # [bugfix] UnifiedModel.llm 返回的文本字段是 "text"，不是 "data"
                text = result.get("text", "") or ""
                if not text.strip():
                    logger.warning(f"[ModelingBrief] LLM 返回空文本")
                    return None
                import json, re
                json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
                if json_match:
                    brief = json.loads(json_match.group(1))
                else:
                    # 兜底:提取第一个 {...} 块
                    brace_start = text.find('{')
                    brace_end = text.rfind('}')
                    if brace_start != -1 and brace_end > brace_start:
                        brief = json.loads(text[brace_start:brace_end+1])
                    else:
                        brief = json.loads(text)
                return brief
            else:
                logger.warning(f"[ModelingBrief] LLM call failed: {result}")
                return None
        except Exception as e:
            logger.error(f"[ModelingBrief] error: {e}")
            return None

    def beautify_face(self, user_id: str = "", char_name: str = "", 
                      ref_image: str = "", age: str = "", 
                      style: str = "", gender: str = "",
                      description: str = "") -> AgentResult:
        """剧装扮相生成：基于参考人脸，换上角色对应的古装/武侠/仙侠造型+美颜"""
        start = time.time()
        try:
            if not ref_image:
                return AgentResult(success=False, error="缺少参考图片", duration_ms=0)
            
            gender_cn = {"male":"男","female":"女","男":"男","女":"女"}.get(gender, gender or "中性")
            style_cn = style or "现代"  # 默认现代，不是古风
            age_part = f"，{age}" if age else ""
            
            # 风格→服装提示
            costume_map = {
                "仙侠": "仙气飘飘的白色/青色古装长袍，长发束冠或披散，发饰典雅，仙门弟子装扮，仙风道骨",
                "武侠": "江湖侠客劲装，束袖绑腿，利落干练，佩剑或暗器，英气逼人",
                "古风": "汉服古装，盘发簪钗，古典妆容，飘逸长裙或长袍，风姿绰约",
                "古装": "汉服古装，盘发簪钗，古典妆容，飘逸长裙或长袍，风姿绰约",
                "玄幻": "奇幻古装，灵气环绕，异色发丝，神秘符文配饰，超凡脱俗",
                "宫廷": "华丽宫装，金冠珠钗，贵气逼人，雍容华贵",
                "现代": "现代服装（T恤/衬衫/休闲装/西装/连衣裙），现代发型，日常妆容，真实自然",
                "都市": "现代服装（T恤/衬衫/休闲装/西装/连衣裙），现代发型，日常妆容，真实自然",
                "甜宠": "现代时尚服装，清新可爱风格，现代发型，精致妆容",
                "职场": "现代职场装（衬衫/西裤/职业裙装），简洁干练，现代发型",
                "商战": "现代商务装（西装/职业装/领带/高跟鞋），精英气质，现代发型",
                "悬疑": "现代服装，偏暗色调，简洁低调，现代发型",
                "历史": "古代将帅铠甲/盔甲，寒光战甲，精钢锁子甲，暗铜色金属甲片泛着寒光，红色战袍披风，头戴兜鍪或凤翅盔，束发金冠，威风凛凛，气吞山河，虎威凛凛",
                "战争": "古代戎装铠甲，战损盔甲，铁灰色战甲斑驳带刀痕，暗红色战袍沾染风沙，头戴铁盔兜鍪，杀气腾腾，百战精锐，一身煞气",
                "西方": "欧式贵族礼服或中世纪铠甲骑士，欧洲古典服饰，西式发型",
            }
            
            # 判断是否有服装描述
            clothing_keywords = ["穿","衣","裙","袍","甲","装","衫","披","戴","着","色","襟","领","袖","冠","钗","簪","服","扮","装束","打扮","服饰","着装","穿着"]
            has_clothing_desc = description and any(kw in description for kw in clothing_keywords)
            
            if has_clothing_desc:
                costume_detail = f"严格按角色描述塑造：{description}。{costume_map.get(style_cn, '')}"
                log_msg = f"[Beautify] {char_name} has clothing description"
            else:
                costume_detail = costume_map.get(style_cn, f"{style_cn}风格古装造型")
                log_msg = f"[Beautify] {char_name} using genre default: {style_cn}"
            logger.info(log_msg)

            # ===== Step 1: 用 DeepSeek 生成角色建模说明书 =====
            brief = self._generate_modeling_brief(char_name, gender, age, style_cn, description)

            if brief:
                logger.info(f"[Beautify] {char_name} modeling brief generated successfully")
                identity = brief.get("identity", f"{style_cn}角色")
                face_desc = brief.get("face", "英武面庞")
                hair_desc = brief.get("hair", "传统发式")
                clothing_desc = brief.get("clothing", "")
                armor_desc = brief.get("armor_or_accessories", "")
                headwear_desc = brief.get("headwear", "")
                materials_desc = brief.get("materials", "")
                weapon_desc = brief.get("weapons_or_props", "")
                pose_desc = brief.get("pose", "站立半身")
                atmosphere_desc = brief.get("atmosphere", "专业影棚布光")
                battle_damage = brief.get("battle_damage", "")
                dynamic_elements = brief.get("dynamic_elements", "")
                quality_tags = brief.get("quality_tags", "(masterpiece, best quality, 8k, ultra-detailed:1.3), (realistic, photo-realistic:1.3)")

                prompt_cn = (
                    f"{quality_tags}，"
                    f"一位真实中国{gender_cn}子，{char_name}，{identity}，"
                    f"面部特征：{face_desc}，"
                    f"发型：{hair_desc}，"
                    f"服装：{clothing_desc}，"
                    + (f"盔甲：{armor_desc}，" if armor_desc else "")
                + (f"头饰：{headwear_desc}，" if headwear_desc else "")
                    + (f"武器：{weapon_desc}，" if weapon_desc else "")
                    + (f"材质质感：{materials_desc}，" if materials_desc else "")
                    + (f"动态：{dynamic_elements}，" if dynamic_elements else "")
                    + (f"战损真实感：{battle_damage}，" if battle_damage else "")
                    + f"半身像上半身肖像照，正面朝前，正脸面向镜头，{pose_desc}，{atmosphere_desc}，{quality_tags}，{face_desc}，{hair_desc}，{clothing_desc}，"
                    + f"保留参考图的面部轮廓和五官特征，严格保持换脸真实自然，"
                    + f"对此人物肖像进行专业精修。保留真实的皮肤纹理和毛孔细节，拒绝过度磨皮和塑料感。温和去除面部明显痘印、黑眼圈和法令纹，肤色提亮均匀，呈现清透自然的底妆感。增强眼神光，让眼睛更明亮有神，发丝边缘增加柔和的自然光，整体画面通透真实。如为女性角色：打造细腻的水光肌质感，自然放大双眼，优化卧蚕，肤色白皙透亮带有健康光泽。如为男性角色：保留皮肤的颗粒感和肌理，不改变原有骨相和五官比例，增强面部立体光影，强化下颌线轮廓，提亮眼神光，去除油腻感，肤色呈现健康自然的质感。发际线完美无碎发，头戴华丽传统冠饰/盔帽，发饰精美完整，完美妆造，中画幅专业拍摄，电影级三点布光，高通透感摄影成片效果"
                    + f"纯色简洁背景，人物居中正对镜头，DSLR专业摄影风，8K超高清，高级人像摄影感，"
                    + f"真实人物照片，自然不僵硬，纯色背景干净无杂物，英俊帅气/美丽动人，端正五官，端正气质，角色魅力十足"
                )

                prompt_en = (
                    f"{quality_tags}, (RAW photo, professional photography:1.2), "
                    f"real Chinese person, {char_name}, {identity}, "
                    f"face: {face_desc}, "
                    f"hair: {hair_desc}, "
                    f"clothing: {clothing_desc}, "
                    + (f"armor: {armor_desc}, " if armor_desc else "")
                + (f"headwear: {headwear_desc}, " if headwear_desc else "")
                    + (f"weapon: {weapon_desc}, " if weapon_desc else "")
                    + (f"materials: {materials_desc}, " if materials_desc else "")
                    + (f"dynamic: {dynamic_elements}, " if dynamic_elements else "")
                    + (f"battle damage: {battle_damage}, " if battle_damage else "")
                    + f"{pose_desc}, half body portrait, front-facing, straight-on to camera, bust shot, {atmosphere_desc}, "
                    + f"preserve face identity and facial features EXACTLY as reference photo, "
                    + f"professional portrait retouching, retain authentic skin texture and pores, no plastic face, no over-smoothing, gently remove blemishes dark circles and nasolabial folds, even bright skin tone, natural luminous base makeup look, enhanced eye catchlights, bright expressive eyes, soft natural rim light on hair edges, clear transparent realistic look overall, if female: delicate dewy glass skin texture, naturally enlarged eyes, prominent tear trough, fair glowing healthy complexion, if male: retain skin grain and texture, preserve bone structure and facial proportions, enhance facial contour, define jawline, brighten eyes, remove oiliness, healthy natural skin tone, perfect hairline no stray hairs, wearing exquisite traditional crown helmet headwear, ornate perfect hair accessories, flawless makeup styling, medium format professional photography, cinematic three-point lighting, high clarity photographic final image "
                    + f"clean solid background, person centered facing camera, DSLR photography style, 8K ultra HD, "
                    + f"high-end portrait photography, real Chinese person photo, natural appearance, clean background no distractions, handsome/beautiful, attractive, charismatic, cinematic lighting, Hollywood production quality, epic war atmosphere, photorealistic skin texture, visible skin pores and hair details, chiaroscuro lighting, dramatic shadows, volumetric light, dignified"
                )
            else:
                # 建模失败时的降级（使用 fallback prompt，不用假数据）
                logger.info(f"[Beautify] {char_name} modeling brief failed, using fallback prompt")
                costume = costume_map.get(style_cn, f"{style_cn}风格古装造型")
                desc_part = f"，{description}" if description else ""
                prompt_cn = (
                    f"(masterpiece, best quality, 8k, ultra-detailed:1.3), "
                    f"一位中国真人，{char_name}，{gender_cn}性{age_part}{desc_part}，"
                    f"{style_cn}剧装扮相：{costume}，"
                    f"保留参考图的面部轮廓和五官特征，"
                    + f"半身像上半身肖像照，正面朝前，正脸面向镜头，站立半身，面容干净清爽，无瑕洁净面容，面部清洁无油光，对此人物肖像进行专业精修。保留真实的皮肤纹理和毛孔细节，拒绝过度磨皮和塑料感。温和去除面部明显痘印、黑眼圈和法令纹，肤色提亮均匀，呈现清透自然的底妆感。增强眼神光，让眼睛更明亮有神，发丝边缘增加柔和的自然光，整体画面通透真实。如为女性角色：打造细腻的水光肌质感，自然放大双眼，优化卧蚕，肤色白皙透亮带有健康光泽。如为男性角色：保留皮肤的颗粒感和肌理，不改变原有骨相和五官比例，增强面部立体光影，强化下颌线轮廓，提亮眼神光，去除油腻感，肤色呈现健康自然的质感。发际线完美无碎发，头戴华丽传统冠饰/盔帽，发饰精美完整，完美妆造，中画幅专业拍摄，电影级三点布光，高通透感摄影成片效果"
                    + f"纯色干净背景，人物居中，专业影棚布光，8K超高清，纯色摄影背景"
                )
                prompt_en = (
                    f"(masterpiece, best quality, 8k, ultra-detailed:1.3), "
                    f"A real Chinese person as {char_name}, {gender} {age or 'young'}, "
                    f"{style_cn} drama costume: {costume}, "
                    f"preserve face identity, "
                    f"half body portrait, front-facing, straight-on to camera, clean flawless face, matte finish, fresh complexion, brightened eye area, no dark circles or eye bags, firm eye contour, under-eye brightness, professional portrait retouching, retain authentic skin texture and pores, no plastic face, no over-smoothing, gently remove blemishes dark circles and nasolabial folds, even bright skin tone, natural luminous base makeup look, enhanced eye catchlights, bright expressive eyes, soft natural rim light on hair edges, clear transparent realistic look overall, if female: delicate dewy glass skin texture, naturally enlarged eyes, prominent tear trough, fair glowing healthy complexion, if male: retain skin grain and texture, preserve bone structure and facial proportions, enhance facial contour, define jawline, brighten eyes, remove oiliness, healthy natural skin tone, perfect hairline no stray hairs, wearing exquisite traditional crown helmet headwear, ornate perfect hair accessories, flawless makeup styling, medium format professional photography, cinematic three-point lighting, high clarity photographic final image"
                )

            prompt = (
                prompt_cn
                + "，不是卡通不是动漫不是插画不是3D渲染不是CGI，高清真实照片质感"
                + " | "
                + prompt_en
                + ", NOT cartoon, NOT anime, NOT illustration, NOT 3D render, NOT CGI, NOT painting, NOT digital art, "
                + "NOT plastic skin, NOT doll-like, NOT stylized, NOT wrinkles, NOT pores, NOT blemishes, NOT spots, NOT freckles, NOT moles, NOT scars, NOT acne, NOT rough skin, NOT uneven skin tone, NOT dark circles, NOT eye bags, NOT natural skin texture, "
                + "real Chinese person, DSLR photography, 8K ultra HD, photorealistic"
            )
            from services.ai_providers import ARKImageProvider
            from services.model_client import UnifiedModel
            ark = ARKImageProvider()
            urls = ark.generate_image_to_image(
                prompt=prompt,
                reference_image=ref_image,
                size="1440x2560",   # 9:16 竖版肖像(满足Seedream最低3686400像素)（人脸占主体，避免"2K"模糊尺寸生成横图）
            )
            if urls and len(urls) > 0:
                url = urls[0]
                logger.info(f"[Beautify] {char_name} dressed as {style_cn} raw_url={url[:60]}")
                # 火山方舟返回的是 TOS 临时 URL（X-Tos-Expires=86400，24h 过期），
                # 必须下载本地化，否则下游场景图 i2i 用时会 403。
                try:
                    persistent_url = UnifiedModel.download_to_storage(url, params.get("name", "") or params.get("char_name", "") or "figure", user_id=params.get("user_id", 0), project_id=str(params.get("project_id", "")))
                    if persistent_url:
                        url = persistent_url
                        logger.info(f"[Beautify] {char_name} 已本地化: {url[:60]}")
                except Exception as dl_e:
                    logger.warning(f"[Beautify] {char_name} 本地化失败(用原URL): {dl_e}")
                return AgentResult(success=True,
                                  data={"figure_url": url, "prompt": prompt, "model": "seedream_i2i"},
                                  duration_ms=int((time.time()-start)*1000))
            return AgentResult(success=False, error="seedream i2i returned empty",
                             duration_ms=int((time.time()-start)*1000))
        except Exception as e:
            logger.error(f"[Beautify] failed: {e}")
            return AgentResult(success=False, error=str(e),
                             duration_ms=int((time.time()-start)*1000))

    def generate_face_locked_figure(self, char_name: str, char_data: dict, ref_image: str,
                                   model_routes: dict = None, scene_context: str = "",
                                   outfit: str = "", props: str = "", char_age: str = "") -> AgentResult:
        """生成锁脸角色图（近景/特写用），带场景情绪（直接调用seedream i2i，零降级）"""
        start = time.time()

        emotion_part = f"，{scene_context}" if scene_context else ""
        prompt = f"角色{char_name}，{char_data.get('basic',{}).get('face_shape','')}脸，{char_data.get('basic',{}).get('hair_style','')}，{char_data.get('style',{}).get('clothing_style','')}风格，近景/特写，高清{emotion_part}"

        # 查缓存
        cached = cache_get(prompt, "face", "768x1024")
        if cached and cached.get("image_url"):
            logger.info(f"[FaceCache] HIT for {char_name}")
            return AgentResult(success=True, data={"figure_url": cached["image_url"],
                                                     "model": cached.get("model", "cache"), "face_locked": True, "cached": True},
                               duration_ms=0)

        from services.model_client import UnifiedModel
        
        if ref_image and (ref_image.startswith("http://") or ref_image.startswith("https://")):
            # 统一走 beautify_face 的质量体系
            # 构建高质量prompt（复用 beautify_face 的质量思路）
            quality_tags = "(masterpiece, best quality, 8k, ultra-detailed:1.3), (realistic, photo-realistic:1.3)"
            age_part = f"{char_age}岁" if char_age else ""
            prompt = (
                f"{quality_tags}, "
                f"真实中国人物，{char_name}，{age_part}，{emotion_part}，"
                f"保留参考图面部特征和五官，精致美颜磨皮，无瑕肌肤，"
                f"面部特写肖象，肩膀以上构图，脸部占画面主体，纯色干净背景，"
                f"专业影棚柔光，DSLR摄影风，8K超高清，真实人像照片，自然真实不僵硬"
            )
            client_result = UnifiedModel.image_to_image(
                prompt=prompt,
                reference_image=ref_image,
                size="1920x1920",
                timeout=120,
                strength=0.08  # 保持原值（用户之前确认过的锁脸强度）
            )
        else:
            client_result = UnifiedModel.image(
                prompt=prompt,
                size=None,
                timeout=120
            )

        if client_result.get("success"):
            url = client_result.get("url", "")
            if url:
                cache_set(prompt, "face", "768x1024", {"image_url": url, "model": client_result.get("model", "seedream")})
                local = cache_download(url, prompt, client_result.get("model", "face"), "768x1024")
                # 模型返回的临时 URL（TOS/OSS）下载本地化，避免下游用过期 URL 403
                try:
                    persistent_url = UnifiedModel.download_to_storage(url, char_name or "face")
                    if persistent_url:
                        url = persistent_url
                except Exception as dl_e:
                    logger.warning(f"[FaceLock] {char_name} 本地化失败(用原URL): {dl_e}")
                return AgentResult(success=True, data={"figure_url": url, "model": client_result.get("model", "seedream"), "face_locked": True,
                                                        "local_path": local or ""}, duration_ms=int((time.time()-start)*1000))

        return AgentResult(success=False, data={"figure_url": ""}, error=client_result.get("error","failed"),
                          duration_ms=int((time.time()-start)*1000))

    def run(self, action: str = "create", **kwargs) -> AgentResult:
        model_routes = kwargs.get("model_routes")
        if action in ("extract", "extract_characters", "aiBeautify"):
            # Handle both direct kwarg params and unpacked params from **req.params
            if "params" in kwargs:
                params = kwargs["params"]
                if isinstance(params, dict):
                    script_text = params.get("script_text", params.get("script", params.get("text", "")))
                else:
                    script_text = params
            else:
                script_text = kwargs.get("script", kwargs.get("script_text",
                            kwargs.get("text", kwargs.get("prompt", ""))))
            # ═══ 导演指令 ═══
            director_task = kwargs.get('director_task', '')
            if not director_task:
                dt = kwargs.get('director_tasks', params.get('director_tasks', {}) if 'params' in kwargs else {})
                if isinstance(dt, dict):
                    director_task = dt.get('character_design', dt.get('character_dev', ''))
            if not director_task:
                da = kwargs.get('director_analysis', params.get('director_analysis', {}) if 'params' in kwargs else {})
                if isinstance(da, dict):
                    arch = da.get('character_archetypes', '')
                    if arch:
                        director_task = str(arch)
            genre = kwargs.get('genre', params.get('genre', '') if 'params' in kwargs else '')
            return self.extract_characters(script_text if isinstance(script_text, str) else str(script_text), director_task, genre)
        if action == "complete":
            return self.complete_character(kwargs.get("name", kwargs.get("character", "")), kwargs.get("script_context", ""))
        if action in ("generate_figure", "gen_figure", "wardrobe"):
            kwargs["model_routes"] = model_routes
            # Handle packed params
            if "params" in kwargs and isinstance(kwargs["params"], dict):
                merged = {**kwargs["params"]}
                merged["model_routes"] = model_routes
                return self.generate_figure(**merged)
            return self.generate_figure(**kwargs)
        if action == "beautify":
            kwargs["ref_image"] = kwargs.get("ref_image", kwargs.get("params", {}).get("ref_image", ""))
            kwargs["char_name"] = kwargs.get("char_name", kwargs.get("params", {}).get("char_name", ""))
            kwargs["age"] = kwargs.get("age", kwargs.get("params", {}).get("age", ""))
            kwargs["style"] = kwargs.get("style", kwargs.get("params", {}).get("style", ""))
            kwargs["gender"] = kwargs.get("gender", kwargs.get("params", {}).get("gender", ""))
            kwargs["description"] = kwargs.get("description", kwargs.get("params", {}).get("description", ""))
            return self.beautify_face(**kwargs)
        if action == "create":
            return self.create_character(kwargs.get("name", ""), kwargs.get("role_type", "主角"), kwargs.get("script_context", ""))
        return AgentResult(success=False, error=f"未知动作: {action}")


    def _match_materials_for_char(self, char_data):
        """根据角色人设自动匹配素材库（强化版风格检测）"""
        basic = char_data.get("basic", {})
        style = char_data.get("style", {})
        name = char_data.get("name", "")
        gender = basic.get("gender", "男")
        clothing_style = style.get("clothing_style", "")
        color_palette = style.get("color_palette", [])
        traits = []
        if isinstance(char_data.get("personality"), dict):
            traits = char_data["personality"].get("traits", [])

        # 全方位风格检测
        _all = str(char_data) + " " + name + " " + clothing_style + " " + " ".join(color_palette) + " ".join(traits)
        _all = _all.lower()
        _all_cn = str(char_data) + name
        style_hint = ""
        # 玄幻检测
        if any(k in _all_cn for k in ["仙", "玄", "妖", "魔", "修", "神", "剑", "洞", "灵"]):
            style_hint = "玄幻"
        # 古装检测
        elif any(k in _all_cn for k in ["古", "皇", "帝", "公主", "将军", "娘娘", "王府", "侠", "江湖"]):
            style_hint = ""
        # 都市检测
        elif any(k in _all_cn for k in ["西装", "总裁", "白领", "现代", "都市", "职场", "酒吧", "公司", "办公室"]):
            style_hint = "都市"
        # 军旅检测
        elif any(k in _all_cn for k in ["军", "兵", "迷彩", "战场", "营"]):
            style_hint = "军旅"
        # 甜宠
        elif any(k in _all_cn for k in ["甜", "宠", "恋爱", "婚礼"]):
            style_hint = "甜宠"
        # 悬疑
        elif any(k in _all_cn for k in ["悬疑", "探案", "谋杀", "谋杀", "刑侦"]):
            style_hint = "悬疑"
        matches = {}
        for cat in ("costume", "face", "prop"):
            params = {"category": cat, "style": style_hint, "gender": gender, "limit": 3}
            items = self._search_materials(**params)
            matches[cat] = items
        # Fallback: if all empty, retry without style_hint
        if all(not v for v in matches.values()):
            for cat in ("costume", "face", "prop"):
                items2 = self._search_materials(category=cat, gender=gender, limit=3)
                if items2:
                    matches[cat] = items2
        best = {}
        for key in ("costume", "face", "prop"):
            items = matches.get(key, [])
            if items:
                best[key] = items[0]
                if len(items) > 1:
                    best[key + "_alts"] = items[1:]
        return {"suggested": best, "options": matches}

    def execute(self, shot: dict, reference_image: str = "", outfit: str = "", props: str = "", char_age: str = "", **kwargs):
        """唯一入口：seedream 图生图。有 reference_image 锁脸，无则文生图。"""
        merged = dict(shot)
        if outfit: merged["outfit"] = outfit
        if props: merged["props"] = props
        if char_age: merged["char_age"] = char_age
        if reference_image:
            merged["reference_image"] = reference_image
        return self.generate_figure(**merged)
