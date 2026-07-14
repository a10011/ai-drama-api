import json,re,os

class DirectorDesigner:
    def __init__(self):
        kb_path=os.path.join(os.path.dirname(__file__),'..','knowledge','director_kb.json')
        with open(kb_path,'r') as f:
            self.kb=json.load(f)
    
    def analyze_script(self,script_text):
        result={'characters':[],'scenes':[],'timeline':[]}
        # Extract character names (heuristic: proper nouns + titles)
        chars=set(re.findall(r'[陆江][\u4e00-\u9fa5]{1,3}',script_text))
        result['characters']=[{'name':c,'type':'将军' if '陆' in c else '仙女'} for c in chars]
        # Detect scene keywords
        for kw,scene_type in self.kb['scene_analysis']['keywords_to_scene_type'].items():
            if re.search(kw,script_text):
                result['scenes'].append({'keywords':kw,'type':scene_type})
        return result
    
    def design_character(self,name,char_type,age_stage='青年',scene_type='日常'):
        wardrobe=self.kb['wardrobe'].get('将军/男性武将' if '将' in char_type else '仙女/女性主角',{})
        outfit=wardrobe.get(scene_type,wardrobe.get('日常',{}))
        aging=self.kb['aging'].get(age_stage,{})
        return {
            'name':name,
            'age_stage':age_stage,
            'face':aging.get('face',''),
            'voice':aging.get('voice',''),
            'outfit':outfit.get('name',''),
            'outfit_prompt':outfit.get('prompt',''),
            'scene_type':scene_type
        }
    
    def get_outfit_prompt(self,char_type,scene_type):
        wardrobe=self.kb['wardrobe'].get(char_type,{})
        outfit=wardrobe.get(scene_type,wardrobe.get('日常',{}))
        return outfit.get('prompt','')
    
    def detect_scene_type(self,text):
        for kw,scene_type in self.kb['scene_analysis']['keywords_to_scene_type'].items():
            if re.search(kw,text):
                return scene_type
        return '日常'

# Singleton
director_designer=DirectorDesigner()
