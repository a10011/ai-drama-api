#!/usr/bin/env python3
"""Replace negative_prompt in ai_providers.py"""
with open('services/ai_providers.py') as f:
    c = f.read()

old = 'cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic'
new = 'cartoon, anime, illustration, painting, drawing, 3D render, CGI, stylized, unrealistic, plastic skin, doll, game character, portrait painting, digital art, comic, wrinkles, pores, blemishes, spots, freckles, moles, scars, acne, rough skin, uneven skin tone, dark circles, eye bags, oily skin, sagging skin, large pores, flabby skin'

count = c.count(old)
print(f"找到 {count} 处匹配")
c = c.replace(old, new)
with open('services/ai_providers.py', 'w') as f:
    f.write(c)
print('✅ ai_providers.py negative_prompt 已更新')
