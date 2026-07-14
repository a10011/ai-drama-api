#!/usr/bin/env python3
"""Replace beautify_face prompts in agent_character.py"""
with open('agents/agent_character.py') as f:
    c = f.read()

old_cn = '极致美颜磨皮，零毛孔细腻肌肤，无瑕瓷光肌，白里透红好气色，面容干净清爽，无瑕洁净面容，面部清洁无油光，高清细节，清晰五官，眼部美颜提亮，消除眼袋黑眼圈，眼周紧致无细纹，卧蚕饱满，眼神明亮有神，目光炯炯，眼神光，精神饱满，神采奕奕，气质出众，魅力动人，极致美容模式，肌肤抛光处理，完美无瑕面容，面部精修，完美肤色，头戴精美传统冠饰/盔帽，发饰华丽完整，精心梳理的发型，完美妆造，电影级画质，影视级角色造型，史诗级战争场面氛围，侧逆光电影布光，明暗对比强烈，皮肤质感真实可见，汗毛细节肌理清晰'

new_cn = '商业摄影级面部精修，脸部肌肤零毛孔无瑕水润，无瑕疵洁净面容，肤色均匀通透，眼周肌肤完美无瑕，眼部双重美颜精修，泪沟法令纹完全消除，眼袋黑眼圈消失，眼周紧致光亮，卧蚕饱满提亮，眼神光锐利清晰，目光如炬有神采，眼部轮廓深邃，好莱坞数码修片标准，面部柔焦空气感处理，中画幅专业拍摄，电影级三点布光，质感到微毫，皮肤自然细腻有光泽，表情生动自然，发际线完美无碎发，头戴华丽传统冠饰/盔帽，发饰精美完整，完美妆造无死角'

if old_cn in c:
    c = c.replace(old_cn, new_cn)
    print('✅ 中文美颜段替换成功')
else:
    print('⚠️ 中文段未找到精确匹配')
    idx = c.find('极致美颜磨皮')
    if idx >= 0:
        print(f'  找到于位置 {idx}')
    else:
        print('  完全未找到')

old_en = 'anti-wrinkle, HD detailed face, clear facial features, brightened eye area, no dark circles or eye bags, firm eye contour, under-eye brightness, clean flawless face, matte finish, fresh complexion, bright expressive eyes with catchlights, spirited and energetic expression, flawless porcelain skin, glowing complexion, charismatic, max beauty mode, extreme skin smoothing, flawless airbrushed skin, face retouched, perfect complexion, porcelain skin texture'

new_en = 'professional beauty retouching, commercial photography skin edit, flawless edited complexion, zero skin imperfections, airbrushed clear smooth skin, eye area retouched perfection, no dark circles no eye bags no tear trough no nasolabial folds, firm bright under-eye area, prominent bright tear trough highlight, sharp bright catchlights in eyes, piercing expressive eyes, deep eye contour definition, Hollywood digital retouching standard, soft focus glow finish, medium format portrait, cinematic three-point lighting setup, studio professional lighting, natural radiant luminous skin texture, vivid natural expression, wearing exquisite traditional crown helmet headwear, ornate complete hair accessories, perfect flawless makeup styling'

if old_en in c:
    c = c.replace(old_en, new_en)
    print('✅ 英文美颜段替换成功')
else:
    print('⚠️ 英文段未找到精确匹配')
    idx = c.find('anti-wrinkle')
    if idx >= 0:
        print(f'  找到于位置 {idx}')
    else:
        print('  完全未找到')

with open('agents/agent_character.py', 'w') as f:
    f.write(c)
print('✅ 文件已保存')
