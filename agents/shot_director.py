import json,logging
logger=logging.getLogger('ShotDirector')

class ShotDirector:
    def build_shot_context(self,shot,prev_shot,next_shot,scene_bible,characters):
        desc=shot.get('description','')
        emotion=shot.get('emotion','')
        shot_type=shot.get('shot_type','')
        focus=shot.get('focus_character','')
        dialogue=shot.get('dialogue','')
        
        parts=[scene_bible.get('lighting','真实光影'),'{genre}题材,真实影视质感']
        
        # Add character outfit prompts
        for ch in scene_bible.get('characters',[]):
            if ch['name'] in desc+dialogue+focus:
                parts.append(ch['outfit_prompt'])
        
        parts.append(desc)
        if shot_type: parts.append('景别:'+shot_type)
        if emotion: parts.append('人物表情:'+emotion)
        if dialogue: parts.append('台词:'+dialogue)
        
        # Continuity
        if prev_shot:
            prev_focus=prev_shot.get('focus_character','')
            cur_focus=shot.get('focus_character','')
            new_chars=set(cur_focus.split(','))-set(prev_focus.split(','))
            if new_chars: parts.append('新出场人物:'+','.join(new_chars)+',自然入画')
            parts.append('保持与前镜相同光线色调服装场景,时空连续不跳戏')
        
        parts.append('真实影视质感,固定广角全景,无动漫卡通手绘')
        
        return ','.join(parts)

shot_director=ShotDirector()
