import re,logging
logger=logging.getLogger('Compliance')

FORBIDDEN_PATTERNS={
    '真实历史人物':[r'曹操',r'刘备',r'诸葛亮',r'岳飞',r'秦始皇',r'朱元璋'],
    '血腥近景':[r'血[溅喷射泼]',r'断[肢臂腿]',r'脑浆',r'内脏',r'开膛',r'斩首'],
    '殉情轻生':[r'殉情',r'自[杀刎尽]',r'跳[崖河楼]',r'上吊',r'服毒'],
    '玄幻法术':[r'法术',r'仙术',r'飞剑',r'御[剑风]',r'腾云',r'渡劫',r'修仙'],
    '仇恨复仇':[r'复仇',r'报仇',r'血[洗恨]',r'灭[门族满]'],
    '低俗猎奇':[r'裸[体露]',r'性[爱感]',r'猥[亵琐]',r'强[暴奸]']
}

COMPLIANCE_RULES={
    'battlefield_wide_only':True,
    'no_wound_closeup':True,
    'max_violence_level':'远景',
    'required_end_labels':['AI生成','虚拟创作']
}

class ContentCompliance:
    def check(self,text):
        violations=[]
        for category,patterns in FORBIDDEN_PATTERNS.items():
            for p in patterns:
                if re.search(p,text):
                    violations.append({'category':category,'pattern':p,'match':re.search(p,text).group()})
        return {'passed':len(violations)==0,'violations':violations}
    
    def sanitize(self,text):
        result=text
        for patterns in FORBIDDEN_PATTERNS.values():
            for p in patterns:
                result=re.sub(p,'***',result)
        return result
    
    def check_scene(self,shot_desc):
        issues=[]
        # Battlefield must be wide/medium shot only
        if any(w in shot_desc for w in ['战场','沙场','厮杀','冲锋','铁骑']):
            if '特写' in shot_desc or '近景' in shot_desc:
                if any(w in shot_desc for w in ['血','伤','倒','死']):
                    issues.append('战场血腥场景仅允许远景/中景拍摄')
        # No wound close-ups
        if any(w in shot_desc for w in ['伤口','流血','血迹','受伤']):
            if '特写' in shot_desc:
                issues.append('禁止伤口特写镜头')
        return {'passed':len(issues)==0,'issues':issues}
    
    def get_end_labels(self):
        return ['本作品由AI生成，角色形象均为虚拟创作','AI Generated Content - Virtual Characters Only']

compliance=ContentCompliance()
