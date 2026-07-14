"""角色库路由"""
import json, time, logging, asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import APIRouter, Request, HTTPException
from app_db import fetchone, fetchall, execute
logger = logging.getLogger("api.characters")
router = APIRouter(prefix="/api/v1/characters", tags=["角色库"])

@router.get("/personal")
async def list_personal_characters(request: Request):
    user_id = getattr(request.state, "user_id", 0)
    if not user_id:
        raise HTTPException(401)
    rows = fetchall("SELECT characters FROM projects WHERE user_id=? AND characters IS NOT NULL AND characters!='[]' ORDER BY created_at DESC", (user_id,))
    seen = set()
    chars = []
    for row in rows:
        try:
            for c in json.loads(row.get("characters", "[]")):
                n = c.get("name", "")
                if n and n not in seen:
                    seen.add(n)
                    chars.append(dict(name=n, gender=c.get("gender",""), age=c.get("age",""),
                        personality=c.get("personality",""), appearance=c.get("appearance",""),
                        image_url=c.get("image_url",c.get("photo","")),
                        ref_image_url=c.get("ref_image_url",""), role_type=c.get("role_type","配角")))
        except:
            pass
    return {"success": True, "data": {"characters": chars, "total": len(chars)}}

@router.put("/personal/{char_name}")
async def update_personal_character(char_name: str, body: dict, request: Request):
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT id, characters FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,))
    if not row:
        raise HTTPException(404, "无项目")
    chars = json.loads(row.get("characters", "[]"))
    found = False
    for c in chars:
        if c.get("name") == char_name:
            for k,v in body.items():
                if v: c[k] = v
            found = True
            break
    if not found:
        chars.append(body)
    execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), row["id"]))
    return {"success": True, "data": {"characters": chars}}

@router.post("/{char_name}/generate")
async def generate_character_image(char_name: str, request: Request):
    from services.model_client import UnifiedModel
    user_id = getattr(request.state, "user_id", 0)
    row = fetchone("SELECT id, characters FROM projects WHERE user_id=? ORDER BY created_at DESC LIMIT 1", (user_id,))
    if not row:
        raise HTTPException(404, "无项目")
    chars = json.loads(row.get("characters", "[]"))
    char = next((c for c in chars if c.get("name") == char_name), None)
    if not char:
        raise HTTPException(404, "角色不存在")
    appearance = char.get("appearance", "")
    prompt = f"\u89d2\u8272{char_name}\uff0c{appearance}\uff0c\u771f\u5b9e\u4eba\u7c7b\u98ce\u683c\uff0c\u4e0d\u662f\u5361\u901a\u4e0d\u662f\u52a8\u6f2b\u4e0d\u662f3D | {char_name}, {appearance}, photorealistic, NOT cartoon"
    ref = char.get("ref_image_url", "")
    try:
        if ref and (ref.startswith("http://") or ref.startswith("https://")):
            # V2: 使用统一图片接口（走生态链AgnesAI）
            try:
                result = UnifiedModel.image(
                    prompt="keep the exact same face, same person, same identity, only change clothing and background to match the character description, photorealistic, studio lighting, clean solid background",
                    preferred=None,
                    size="1024x1024",
                    timeout=120,
                    reference_image=ref
                )
            except Exception as e:
                result = {"success": False, "url": "", "model": "", "error": str(e)}
        else:
            result = UnifiedModel.image(prompt=prompt, size="1024x1024", timeout=120)
    except Exception as e:
        return {"success": False, "error": str(e)}
    if isinstance(result, dict) and result.get("success"):
        url = result.get("url", "")
        # download to local storage (ARK URL expires in 24h)
        try:
            local_url = UnifiedModel.download_to_storage(url, char_name, user_id)
            if local_url:
                url = local_url
                logger.info("[characters] portrait persisted: %s -> %s", char_name, local_url)
        except Exception as e:
            logger.warning("[characters] download failed (fallback to ARK URL): %s", e)
        for c in chars:
            if c.get("name") == char_name:
                c["image_url"] = url
                break
        execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), row["id"]))
        return {"success": True, "data": {"image_url": url, "char_name": char_name}}
    return {"success": False, "error": result.get("error","failed") if isinstance(result, dict) else str(result)}

@router.post('/extract')
def extract_characters(body: dict, request: Request):
    # AI 从剧本提取角色
    user_id = getattr(request.state, 'user_id', 0)
    script_text = body.get('script_text', '')
    project_id = body.get('project_id', 0)
    title = body.get('title', '')
    if not script_text or len(script_text.strip()) < 10:
        return {'success': False, 'message': '\u5267\u672c\u5185\u5bb9\u8fc7\u77ed\uff0c\u8bf7\u8865\u5145\u540e\u91cd\u8bd5'}
    from services.model_client import UnifiedModel as _UM
    import re as _re, json as _json
    system_p = '\u4f60\u662f\u4e00\u4e2a\u4e13\u4e1a\u77ed\u5267\u89d2\u8272\u5206\u6790\u5668\u3002\u6839\u636e\u8f93\u5165\u7684\u5267\u672c\uff0c\u63d0\u53d6\u6240\u6709\u89d2\u8272\u4fe1\u606f\u3002\u6bcf\u4e2a\u89d2\u8272\u5fc5\u987b\u5305\u542b\uff1aname(\u89d2\u8272\u540d), gender(\u7537/\u5973/\u672a\u77e5), age(\u5e74\u9f84\u6216\u672a\u77e5), personality(\u6027\u683c\u63cf\u8ff0), appearance(\u5916\u8c8c\u63cf\u8ff0), role_type(\u4e3b\u89d2/\u914d\u89d2/\u53cd\u6d3e)\u3002\u5fc5\u987b\u8fd4\u56de\u81f3\u5c113\u4e2a\u89d2\u8272\u3002\u53ea\u8f93\u51fa\u7eafJSON\u6570\u7ec4\uff0c\u4e0d\u8981\u4efb\u4f55\u5176\u4ed6\u6587\u5b57\u3002'
    user_p = f'\u5267\u672c\uff1a{script_text[:4000]}\n\n\u8bf7\u63d0\u53d6\u6240\u6709\u89d2\u8272\uff08\u81f3\u5c113\u4e2a\uff0c\u542b\u4e3b\u89d2\u914d\u89d2\u53cd\u6d3e\uff09\u3002\u8fd4\u56de\u7eafJSON\u6570\u7ec4 [{{"name":"","gender":"","age":"","personality":"","appearance":"","role_type":""}}]'
    try:
        llm_res = _UM.llm(prompt=user_p, system=system_p, model=None, timeout=90, max_tokens=4096)
        raw = llm_res.text if hasattr(llm_res, "text") else llm_res.get("text", "")
        raw = raw.strip()
        if raw.startswith('```'):
            idx = raw.find(chr(10))
            if idx > 0: raw = raw[idx+1:]
            if raw.endswith('```'): raw = raw[:-3]
        m = _re.search(r'\[.*\]', raw, _re.DOTALL)
        if not m:
            m = _re.search(r'\{.*\}', raw, _re.DOTALL)
        if not m:
            return {'success': False, 'message': '\u89d2\u8272\u63d0\u53d6\u5931\u8d25\uff1aAI\u8fd4\u56de\u683c\u5f0f\u5f02\u5e38'}
        data = _json.loads(m.group(0))
        chars = data if isinstance(data, list) else data.get('characters', [data])
        _role_map = {'protagonist':'\u4e3b\u89d2','main':'\u4e3b\u89d2','hero':'\u4e3b\u89d2','heroine':'\u4e3b\u89d2','supporting':'\u914d\u89d2','support':'\u914d\u89d2','side':'\u914d\u89d2','antagonist':'\u53cd\u6d3e','villain':'\u53cd\u6d3e','rival':'\u53cd\u6d3e'}
        _gender_map = {'male':'\u7537','female':'\u5973','man':'\u7537','woman':'\u5973','m':'\u7537','f':'\u5973'}
        for c in chars:
            rt = str(c.get('role_type','')).lower().strip()
            if rt and rt not in ('\u4e3b\u89d2','\u914d\u89d2','\u53cd\u6d3e'):
                c['role_type'] = _role_map.get(rt, '\u914d\u89d2')
            g = str(c.get('gender','')).lower().strip()
            if g and g not in ('\u7537','\u5973'):
                c['gender'] = _gender_map.get(g, g)
        if project_id:
            try:
                row = fetchone('SELECT characters FROM projects WHERE id=? AND user_id=?', (project_id, user_id))
                if row:
                    existing = _json.loads(row.get('characters','[]') or '[]')
                    seen = {c.get('name','') for c in existing}
                    for c in chars:
                        if c.get('name') and c['name'] not in seen:
                            existing.append(c)
                            seen.add(c['name'])
                    execute('UPDATE projects SET characters=?, updated=? WHERE id=?',
                            (_json.dumps(existing, ensure_ascii=False), time.time(), project_id))
            except Exception as e:
                logger.warning(f'extract: save to project failed: {e}')
        return {'success': True, 'data': {'characters': chars}}
    except Exception as e:
        logger.error(f'extract characters error: {e}')
        return {'success': False, 'message': f'\u89d2\u8272\u63d0\u53d6\u5931\u8d25: {str(e)}'}


@router.post("/portrait")
def generate_portrait(body: dict, request: Request):
    """智能体把关：根据剧中角色信息生成电影级造型肖像。
    前端传的角色信息（性格/身份/外貌/年龄/题材）仅作参考输入，
    由 LLM 造型指导生成专业造型方案，再交 seedream 生图。"""
    from services.model_client import UnifiedModel
    user_id = getattr(request.state, "user_id", 0)
    if not user_id:
        return {"success": False, "error": "not logged in"}
    name = body.get("name", "")
    gender = body.get("gender", "")
    age = body.get("age", "")
    personality = body.get("personality", "")
    appearance = body.get("appearance", "")
    role_type = body.get("role_type", "")
    project_id = body.get("project_id", 0)
    ref_image = body.get("ref_image", "")
    genre = body.get("genre", "")
    if not name:
        return {"success": False, "message": "name required"}

    gender_cn = "男" if gender in ("男", "male") else ("女" if gender in ("女", "female") else "中性")

    # 从 DB 读取剧本内容，让智能体理解剧情来设计造型（不只靠前端传的字段）
    script_text = ""
    if project_id:
        try:
            row = fetchone("SELECT script, genre FROM projects WHERE id=? AND user_id=?", (str(project_id), user_id))
            if row:
                script_text = row.get("script", "") or ""
                if not genre:
                    genre = row.get("genre", "") or ""
        except Exception:
            pass

    # ── 第一步：LLM 造型指导 —— 根据角色信息生成电影级造型方案 ──
    # 智能体把关：不是机械拼接前端字段，而是让 LLM 像造型指导一样，
    # 根据角色身份/性格/外貌/题材，设计贴合剧情的造型（服装/发型/气质/光影/构图）
    styling_prompt_cn = ""  # LLM 生成的中文造型描述
    styling_prompt_en = ""  # LLM 生成的英文造型描述
    try:
        llm_result = UnifiedModel.llm(
            prompt=f"""角色名：{name}
性别：{gender_cn}
年龄：{age or '青年'}
题材风格：{genre or '现代'}
角色定位：{role_type or '未指定'}
性格：{personality or '未指定'}
外貌描述：{appearance or '未指定'}
{'剧本内容（请理解剧情后为角色设计贴合剧情的造型）：' + chr(10) + script_text[:2000] if script_text else ''}

请作为电影造型指导，为这个角色设计影视级造型。要根据剧本内容和角色在剧情中的身份、处境来设计造型（比如战场角色要有战损感、宫廷角色要华贵、落难角色要朴素）。直接输出用于 AI 绘图的中英文 prompt 描述（每段 80-150 字），包含：
1. 服装款式、颜色、材质（符合角色身份和题材时代背景）
2. 发型与头饰
3. 整体气质与神态（贴合角色性格和剧情处境）
4. 光影氛围与构图（电影级质感）
5. 皮肤质感要求（真实自然，不美颜不磨皮）

格式：
CN: 中文描述
EN: 英文描述
""",
            system="""你是一位顶级影视造型指导，擅长历史战争剧的角色造型设计。参考《赤壁》《英雄》《指环王》等史诗大片中将军的视觉形象（如赵云、吕布、阿拉贡）。要求：

1. 服装（必须大气华丽，气场十足）：
   - 主将：全套明光铠/兽面吞头连环甲，甲片如鳞闪烁金属光泽，肩覆兽首吞肩铠，胸前护心镜錾刻瑞兽纹饰
   - 内衬蜀锦/蜀绣战袍，下摆丝绸披风随风展开，披风上绣家族纹章/战旗图腾
   - 腰束镶玉嵌金革带，悬佩宝剑/长刀，剑鞘镶嵌宝石
   - 色彩搭配要有视觉冲击力：玄铁黑甲配赤红披风、银白甲配金边、暗金甲配墨绿袍
   - 服装要有厚重感和层次感，不是单薄的戏服，是真正的战场重甲
2. 头饰：将军兜鍪/凤翅盔/束发紫金冠，盔顶红缨如火焰飞扬，两侧护耳，威严如天神降世
3. 武器：手持长枪/陌刀/重剑，兵器要有质感（寒光闪闪/包浆古朴），增加武将杀伐气场
4. 面部（影视级化妆，不是网红脸）：
   - 精致妆容但保留男性刚毅感：剑眉星目，鼻梁高挺，下颌线锋利
   - 眼神要有穿透力，如鹰隼俯瞰战场，瞳孔有光
   - 皮肤紧致有质感，微微光泽（像刚打完仗微微出汗），不是死白
   - 面部可以有一两道战损疤痕（主将），增加沧桑感
   - 嘴唇微抿，表情冷峻，有不怒自威的统帅气场
5. 气质：大将风度，气吞山河，一人当关万夫莫开，站在那里就是千军万马的核心
6. 光影：电影级侧逆光打亮铠甲轮廓，明暗对比强烈如伦勃朗画，背景虚化有战场硝烟/旌旗
7. 构图：半身或全身，居中构图，仰拍角度增加威严感，如英雄雕像般庄重

输出要求：描述必须具体到材质、颜色、光泽、纹饰，不要笼统说"华丽战甲"，要说"玄铁鳞甲泛冷蓝光泽，甲缘鎏金，肩覆虎头吞肩铠"
只输出 CN: 和 EN: 两行，不要其它内容。""",
            model=None,  # 走生态链(AgnesAI)
            timeout=30,
            max_tokens=800
        )
        if llm_result and llm_result.get("success"):
            text = llm_result.get("text", "")
            # 解析 CN: 和 EN:
            import re as _re
            cn_match = _re.search(r'CN[:：]\s*(.+?)(?:\nEN[:：]|$)', text, _re.DOTALL)
            en_match = _re.search(r'EN[:：]\s*(.+)', text, _re.DOTALL)
            if cn_match: styling_prompt_cn = cn_match.group(1).strip()
            if en_match: styling_prompt_en = en_match.group(1).strip()
            logger.info(f"[Portrait] LLM造型指导({name}): CN={styling_prompt_cn[:60]}... EN={styling_prompt_en[:60]}...")
    except Exception as e:
        logger.warning(f"[Portrait] LLM造型指导失败，用基础描述: {e}")

    # LLM 失败时的兜底描述
    if not styling_prompt_cn:
        styling_prompt_cn = f"{gender_cn}性角色{('，' + appearance) if appearance else ''}{('，性格' + personality) if personality else ''}，穿着符合{genre or '现代'}题材的服装，真实自然，电影级质感"
    if not styling_prompt_en:
        styling_prompt_en = f"real Chinese {gender_cn}, {'appearance: ' + appearance + ', ' if appearance else ''}{'personality: ' + personality + ', ' if personality else ''}wearing {genre or 'modern'} style clothing, cinematic quality"

    # ── 第二步：seedream 生图（用 LLM 造型方案 + 锁脸指令）──
    if ref_image:
        # 有参考图 → i2i 换装保脸
        # 使用V2统一图片接口（走生态链AgnesAI）
        try:
            result = UnifiedModel.image(
                prompt=styling_prompt_en, 
                preferred=None, 
                size="1024x1024", 
                timeout=120,
                reference_image=ref_image
            )
        except Exception as e:
            result = {"success": False, "url": "", "model": "", "error": str(e)}
    else:
        # 无参考图 → 文生图
        if not appearance and not personality:
            return {"success": False, "message": "appearance or personality required when no reference image"}
        # 从 DB 读项目类型，加类型锁约束（商战→西装、古装→铠甲等）
        _genre_lock_str = ""
        if project_id:
            try:
                _prow = fetchone("SELECT genre FROM projects WHERE id=?", (str(project_id),))
                _proj_genre = (_prow.get("genre", "") if _prow else "") or ""
                if not _proj_genre:
                    # genre 空 → 从剧本内容推断
                    _srow = fetchone("SELECT script FROM projects WHERE id=?", (str(project_id),))
                    _script_text = (_srow.get("script", "") if _srow else "")[:2000]
                    for _kw in ["并购", "董事长", "股份", "CEO", "上市", "投资"]:
                        if _kw in _script_text:
                            _proj_genre = "商战"; break
                    if not _proj_genre:
                        _proj_genre = "都市"
                from agents.genre_lock import build_genre_lock_prompt
                _genre_lock_str = build_genre_lock_prompt(_proj_genre)
                if _genre_lock_str:
                    logger.info(f"[Portrait] 类型锁({(_proj_genre)}): {_genre_lock_str[:80]}")
            except Exception as _ge:
                logger.warning(f"[Portrait] 类型锁加载失败: {_ge}")
        prompt = (
            f"CINEMATIC FILM STILL, movie-grade photography, {styling_prompt_en}, "
            f"NO BEAUTY FILTER, raw unretouched, real skin texture, "
            f"standing, front-facing, cinematic lighting, 8K, shot on ARRI Alexa "
            f"{', ' + _genre_lock_str if _genre_lock_str else ''} "
            f"|| "
            f"电影级剧照质感，{styling_prompt_cn}，"
            f"禁止美颜滤镜，原始未修图，真实皮肤质感，"
            f"电影级光影，8K超高清，ARRI摄影机质感"
            f"{('，' + _genre_lock_str) if _genre_lock_str else ''}"
        )
        try:
            result = UnifiedModel.image(prompt=prompt, size="1024x1536", timeout=120)
        except Exception as e:
            return {"success": False, "error": str(e)}
    if isinstance(result, dict) and result.get("success"):
        url = result.get("url", "")
        # 下载到本地持久存储（ARK URL 24小时过期，视频锁脸需要永久URL）
        try:
            from services.model_client import UnifiedModel
            local_url = UnifiedModel.download_to_storage(url, name, user_id)
            if local_url:
                url = local_url
        except Exception as e:
            pass  # 不影响主流，继续用ARK URL
        if project_id:
            try:
                # 优先按 project_id + user_id 查；查不到则退化为只按 project_id 查
                # （会员体系 user_id 可能不一致，避免 portrait_url 写不进 DB）
                row = fetchone("SELECT characters FROM projects WHERE id=? AND user_id=?", (str(project_id), user_id))
                if not row:
                    row = fetchone("SELECT characters FROM projects WHERE id=?", (str(project_id),))
                    if row:
                        logger.info(f"[Portrait] user_id不匹配(user={user_id})，退化为只按project_id查")
                if row:
                    chars = json.loads(row.get("characters", "[]") or "[]")
                    found = False
                    for c in chars:
                        if c.get("name") == name:
                            c["portrait_url"] = url
                            c["image_url"] = url
                            found = True
                            break
                    if found:
                        execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(chars, ensure_ascii=False), time.time(), str(project_id)))
                        logger.info(f"[Portrait] portrait_url 已写入DB: project={project_id} name={name} url={url[:60]}")
                    else:
                        logger.warning(f"[Portrait] 角色{name}在项目{project_id}的characters里没找到（名字不匹配）")
                else:
                    logger.warning(f"[Portrait] DB查不到项目 project_id={project_id}")
            except Exception as e:
                logger.warning(f"[Portrait] 写DB失败: {e}")
        return {"success": True, "data": {"portrait_url": url, "image_url": url}}
    return {"success": False, "message": "generate failed"}


@router.post("/save")
async def save_characters(body: dict, request: Request):
    """save characters to project"""
    user_id = getattr(request.state, "user_id", 0)
    if not user_id:
        return {"success": False, "error": "not logged in"}
    project_id = body.get("project_id", 0)
    characters = body.get("characters", [])
    if not project_id:
        return {"success": False, "message": "project_id is required"}
    row = fetchone("SELECT id, user_id FROM projects WHERE id=?", (project_id,))
    if not row:
        return {"success": False, "message": "project not found"}
    if row["user_id"] != user_id:
        return {"success": False, "message": "permission denied"}
    try:
        execute("UPDATE projects SET characters=?, updated=? WHERE id=?", (json.dumps(characters, ensure_ascii=False), time.time(), project_id))
        return {"success": True, "data": {"project_id": project_id}}
    except Exception as e:
        return {"success": False, "message": str(e)}
