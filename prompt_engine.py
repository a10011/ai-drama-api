
def build_t2i_prompt(subject='', scene='', style='', lighting='', composition='', quality=''):
    parts = [p for p in [subject, scene, style, lighting, composition, quality] if p]
    return ', '.join(parts)

def build_i2i_prompt(instruction='', keep_elements='', target_style='', lighting='', composition='', quality=''):
    parts = []
    if instruction: parts.append(instruction)
    if keep_elements: parts.append('keep: ' + keep_elements)
    for p in [target_style, lighting, composition, quality]:
        if p: parts.append(p)
    return ', '.join(parts)

def build_llm_prompt(role, task, context='', requirements='', output_format='JSON'):
    return f'你是{role}。任务：{task}。输出：{output_format}。', context or ''

def build_portrait_prompt(character, genre='现代'):
    name = character.get('name', '')
    gender = character.get('gender', '男')
    age = str(character.get('age', '25')).replace('岁','').strip()
    appearance = character.get('appearance', '')
    role_type = character.get('role_type', '主角')
    gender_en = 'male' if gender in ('男','male') else 'female'
    role_map = {'主角':'short drama lead actor','配角':'supporting actor','反派':'antagonist'}
    role_desc = role_map.get(role_type, 'short drama actor')
    face = f'{gender_en}, {age} years old, Chinese'
    if appearance: face += f', {appearance}'
    wardrobe_map = {'现代':'modern casual clothing','都市':'urban fashion','古装':'traditional Chinese hanfu','仙侠':'Chinese xianxia robes','豪门':'luxury designer fashion','校园':'school uniform','职场':'business formal'}
    wardrobe = wardrobe_map.get(genre, 'modern clothing')
    camera = 'vertical 9:16 portrait, short drama cinematography, studio soft lighting, natural skin texture, no heavy makeup, no beauty filter, realistic pores, authentic human skin, cinematic color grading, raw camera quality, half-body close-up, front-facing, 8K ultra HD, photorealism'
    negative = 'cartoon, anime, 3D render, CGI, illustration, painting, plastic skin, doll face, exaggerated features, heavy makeup, over-beauty-filter, blurry, distorted face, deformed, extra limbs'
    return f'{role_desc}, {face}. {wardrobe}. {camera}. Negative: {negative}'

def build_scene_prompt(shot, genre='现代', character_portraits=None, director_scene_instruction='', wardrobe_plan='', prop_plan='', makeup_plan='', sfx_plan='', scene_asset_lib=None):
    desc = shot.get('description', shot.get('content', ''))
    shot_type = shot.get('type', shot.get('shot_type', '中景'))
    emotion = shot.get('emotion', '')
    location = shot.get('location', shot.get('scene', ''))
    focus_char = shot.get('focus_character', '')
    subject = desc if desc else location
    shot_map = {'特写':'close-up','近景':'medium close-up','中景':'medium shot','全景':'full shot','远景':'long shot'}
    composition = shot_map.get(shot_type, 'medium shot')
    light_map = {'悲伤':'moody cool','愤怒':'harsh dramatic','浪漫':'warm golden','悬疑':'low key','紧张':'stark contrast','平静':'natural soft'}
    lighting = light_map.get(emotion, 'cinematic')
    quality = 'vertical 9:16, short drama cinematography, photorealistic, 8K, natural skin'
    
    # 导演场景指令叠加
    director_hint = ''
    if director_scene_instruction:
        director_hint = f'Directive: {director_scene_instruction[:300]}. '
    
    # 服装/道具/化妆/特效叠加
    scene_details = []
    if wardrobe_plan:
        scene_details.append(f'Wardrobe: {wardrobe_plan[:200]}')
    if prop_plan:
        scene_details.append(f'Props: {prop_plan[:200]}')
    if makeup_plan:
        scene_details.append(f'Makeup: {makeup_plan[:200]}')
    if sfx_plan:
        scene_details.append(f'SFX: {sfx_plan[:200]}')
    extra_details = ' '.join(scene_details)
    
    # 场景资产库复用：优先使用已有场景图
    reuse_asset = ''
    if scene_asset_lib and location:
        # 从资产库查找匹配的场景
        for scene in scene_asset_lib.get('主场景设定图', []):
            if scene.get('场景名') == location:
                # 根据情绪选择对应氛围图
                emotion_map = {'颓废压抑': '颓废压抑', '冲突争吵': '家庭冲突', '高光觉醒': '高光觉醒', '沉默悬念': '沉默悬念'}
                emotion_key = emotion_map.get(emotion, '日常')
                for atmosphere in scene.get('情绪氛围图', []):
                    if atmosphere.get('情绪') == emotion_key:
                        reuse_asset = atmosphere.get('提示词', '')
                        break
                break
    
    if reuse_asset:
        # 使用资产库中的场景图，保证一致性
        logger.info(f"[SceneAgent] 复用场景资产: {location} ({emotion_key})")
        return reuse_asset, None
    
    if character_portraits and focus_char:
        ref_url = character_portraits.get(focus_char, '')
        if ref_url:
            p = build_i2i_prompt(
                instruction=f'{director_hint}{subject}. {extra_details}',
                keep_elements=f'exact face of {focus_char}',
                target_style='short drama',
                lighting=lighting,
                composition=composition,
                quality=quality
            )
            return p, ref_url
    p = build_t2i_prompt(
        subject=subject,
        scene=location,
        style='short drama realistic',
        lighting=lighting,
        composition=composition,
        quality=quality
    )
    return p, None

def build_video_prompt(shot, audio_text='', genre='现代', reference_image=False, sfx_plan='', prop_plan=''):
    desc = shot.get('description', shot.get('content', ''))
    focus_char = shot.get('focus_character', '')
    location = shot.get('location', '')
    cam = 'subtle camera movement'
    lighting = 'short drama cinematography'
    style = 'photorealistic, consistent appearance, 8K'
    
    # 添加导演指定的特效和道具
    extra = []
    if sfx_plan:
        extra.append(f'SFX: {sfx_plan[:150]}')
    if prop_plan:
        extra.append(f'Props: {prop_plan[:150]}')
    extra_str = ' ' + ' '.join(extra) if extra else ''
    
    if reference_image:
        motion_parts = ['subtle breathing', 'natural blinking']
        if audio_text:
            motion_parts.extend(['accurate lip sync matching dialogue', 'mouth movements synchronized with speech', 'natural facial expression while talking'])
        stable = f'keeping face of {focus_char} consistent' if focus_char else 'maintaining appearance'
        motion_str = ', '.join(motion_parts)
        return f'Animate with {motion_str}, {cam}, {lighting}, while {stable}. {style}{extra_str}.'
    
    action_parts = ['subtle natural movement']
    if audio_text:
        action_parts.extend(['speaking with accurate lip sync', 'mouth movements synchronized with dialogue', 'natural facial expression while talking'])
    scene = location if location else desc
    action_str = ', '.join(action_parts)
    return f'{focus_char or character}, {action_str}, {scene}, {cam}, {lighting}, {style}{extra_str}.'
