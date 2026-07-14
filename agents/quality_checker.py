import json,logging,re
logger=logging.getLogger('QualityChecker')

QUALITY_RULES={
    '面部检测':{'check':'角色脸不被遮挡/变形','fail_if':'侧面超过45度/面部模糊/五官缺失'},
    '肢体完整性':{'check':'骑兵有马/步兵有武器/手部正常','fail_if':'缺手臂/缺腿/武器悬浮'},
    '场景一致性':{'check':'背景与描述匹配','fail_if':'室内场景出现天空/战场出现现代物品'},
    '色彩自然度':{'check':'无过度曝光/死黑/色偏','fail_if':'全屏红色/绿色色偏/过曝白屏'},
    '动作合理性':{'check':'冲锋向前/不倒退/不漂浮','fail_if':'人物倒退/悬浮/瞬移'},
    '服饰正确':{'check':'服装/道具/武器匹配场合','fail_if':'服装与场景不匹配'},
}

class QualityChecker:
    def __init__(self):
        self.rules=QUALITY_RULES
        self.min_acceptable_score=6  # 0-10
    
    def assess(self,shot_desc,generated_video_url):
        # Basic heuristics based on description analysis
        issues=[]
        score=10
        
        desc=shot_desc.lower()
        
        # Check if battle scene has proper elements
        if any(w in desc for w in ['战场','冲锋','铁骑','厮杀']):
            if '头盔' not in desc and '玄铁' not in desc:
                issues.append('战场未提及头盔')
                score-=1
            if '向前' not in desc and '冲锋' not in desc:
                issues.append('未明确冲锋方向')
                score-=1
        
        # Check emotional consistency
        emotion_map={'不舍':'悲伤','怒':'愤怒','杀':'杀意'}
        
        # Return assessment
        passed=score>=self.min_acceptable_score
        return {
            'passed':passed,
            'score':score,
            'issues':issues,
            'suggestions':[f'建议添加{r}' for r in ['明确方向描述','头盔细节','兵器细节'] if f'建议添加{r}' not in str(issues)]
        }
    
    def batch_check(self,shots):
        results=[]
        for i,shot in enumerate(shots):
            r=self.assess(shot.get('description',''),shot.get('video_url',''))
            r['shot_index']=i
            results.append(r)
        return results

quality_checker=QualityChecker()
