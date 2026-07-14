import json,logging
from agents.director_designer import director_designer

logger=logging.getLogger('ArtDirector')

class ArtDirector:
    def create_scene_bible(self,scene_text,characters,genre='现代'):
        scene_type=director_designer.detect_scene_type(scene_text)
        bible={
            'scene_type':scene_type,
            'lighting':'夕阳残照,暖黄逆光,血色黄昏' if '夕阳' in scene_text or '残阳' in scene_text else '自然光',
            'color_temp':'3200K暖黄偏红',
            'depth_of_field':'f/2.8浅景深',
            'characters':[]
        }
        for ch in characters:
            design=director_designer.design_character(ch.get('name',''),ch.get('type',''),'青年',scene_type)
            bible['characters'].append(design)
        return bible

    def get_consistent_prompt_prefix(self,scene_bible):
        return f"{scene_bible['lighting']},{genre}题材,真实影视质感,固定广角全景,无动漫卡通手绘"

art_director=ArtDirector()
