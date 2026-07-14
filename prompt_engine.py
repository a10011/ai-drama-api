
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

def build_scene_prompt(shot, genre='现代', character_portraits=None):
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
    if character_portraits and focus_char:
        ref_url = character_portraits.get(focus_char, '')
        if ref_url:
            p = build_i2i_prompt(instruction=f'{subject}', keep_elements=f'exact face of {focus_char}', target_style='short drama', lighting=lighting, composition=composition, quality=quality)
            return p, ref_url
    p = build_t2i_prompt(subject=subject, scene=location, style='short drama realistic', lighting=lighting, composition=composition, quality=quality)
    return p, None

def build_video_prompt(shot, audio_text='', genre='现代', reference_image=False):
    desc = shot.get('description', shot.get('content', ''))
    focus_char = shot.get('focus_character', '')
    location = shot.get('location', '')
    cam = 'subtle camera movement'
    lighting = 'short drama cinematography'
    style = 'photorealistic, consistent appearance, 8K'
    if reference_image:
        motion = 'subtle breathing, natural blinking'
        if audio_text: motion += ', natural lip sync'
        stable = f'keeping face of {focus_char} consistent' if focus_char else 'maintaining appearance'
        return f'Animate with {motion}, {cam}, {lighting}, while {stable}. {style}.'
    action = 'subtle natural movement'
    if audio_text: action += ', speaking'
    scene = location if location else desc
    return f'{focus_char or character}, {action}, {scene}, {cam}, {lighting}, {style}.'
