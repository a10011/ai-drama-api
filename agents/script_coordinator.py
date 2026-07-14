import json,re,logging
logger=logging.getLogger('ScriptCoordinator')

class ScriptCoordinator:
    def __init__(self):
        self.timeline=[]
        self.characters=[]
    
    def analyze(self,script_text):
        result={'title':'','genre':'','episodes':[],'characters':[],'timeline':[]}
        # Extract character names
        chars=set(re.findall(r'[\u4e00-\u9fa5]{2,3}(?:将军|姑娘|夫人|大人|公主)',script_text))
        for c in chars:
            result['characters'].append({'name':c,'type':'主角' if len(c)==3 else '配角'})
        # Split by scene markers
        scenes=re.split(r'(?:第[一二三四五六七八九十\d]+[场幕]|[-\*]{3,}|残阳|晨雾|夜色|次日)',script_text)
        for i,s in enumerate(scenes):
            if len(s.strip())>50:
                result['timeline'].append({'id':i,'text':s.strip()[:200],'stage':'青年'})
        return result

script_coordinator=ScriptCoordinator()
