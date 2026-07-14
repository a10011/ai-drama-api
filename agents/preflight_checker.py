import json,logging,re
logger=logging.getLogger('PreflightChecker')

# Stage 1: 剧本/分镜文案质检
SCRIPT_CHECKS={
    '描述不能空': lambda s: bool(s.get('description','').strip()),
    '景别必须填': lambda s: bool(s.get('shot_type','')),
    '情绪必须填': lambda s: bool(s.get('emotion','')),
    '时长3-15秒': lambda s: 3<=int(s.get('duration',0))<=15,
    '战场有方向': lambda s: not any(w in s.get('description','') for w in ['战场','冲锋','铁骑']) or any(w in s.get('description','') for w in ['向前','冲锋','杀向','冲向','直奔']),
    '战场戴头盔': lambda s: not any(w in s.get('description','') for w in ['战场','冲锋','铁骑','杀']) or '头盔' in s.get('description',''),
    '骑兵拿武器': lambda s: not any(w in s.get('description','') for w in ['骑兵','铁骑','战马']) or any(w in s.get('description','') for w in ['长戟','环首刀','剑','刀','枪']),
    '禁止一字长蛇': lambda s: '一字长蛇' not in s.get('description',''),
    '禁止卡通动漫词': lambda s: not any(w in s.get('description','') for w in ['动漫','卡通','赛璐璐','手绘','二次元']),
    '新角色有出场': lambda s,i,shots: i==0 or not set(s.get('focus_character','').split(','))-set(shots[i-1].get('focus_character','').split(',')) or any(w in s.get('description','') for w in ['入画','出现','出场','走入','步入']),
    '情绪不跳变': lambda s,i,shots: i==0 or s.get('emotion','')==shots[i-1].get('emotion','') or any(w in s.get('description','') for w in ['突然','骤然','猛地','瞬间']),
}

# Stage 2: 角色台词语境质检
DIALOGUE_CHECKS={
    '台词匹配角色': lambda s: not s.get('dialogue','') or any(c in s.get('dialogue','') for c in ['将军','我','你','他','她']),
    '台词有标点': lambda s: not s.get('dialogue','') or any(p in s.get('dialogue','') for p in ['？','！','。','…']),
    '台词不超100字': lambda s: len(s.get('dialogue',''))<=100,
}

# Stage 3: 动作逻辑质检
ACTION_CHECKS={
    '冲锋不倒退': lambda s: not any(w in s.get('description','') for w in ['冲锋','向前','杀向']) or '后退' not in s.get('description',''),
    '摔倒有站起': lambda s: '摔倒' not in s.get('description','') or '爬起' in s.get('description','') or '站起' in s.get('description',''),
    '拔剑有剑': lambda s: '拔剑' not in s.get('description','') or any(w in s.get('description','') for w in ['长剑','剑刃','剑锋','剑光']),
}

class PreflightChecker:
    def check_all(self,shots):
        report={'passed':True,'stage1':[],'stage2':[],'stage3':[],'total_issues':0}
        
        for i,shot in enumerate(shots):
            issues=[]
            # Stage 1
            for name,check in SCRIPT_CHECKS.items():
                try:
                    if not check(shot) if 'i' not in str(check.__code__.co_varnames)[:50] else not check(shot,i,shots):
                        issues.append({'stage':'文案','shot':i,'issue':name,'desc':shot.get('description','')[:50]})
                except: pass
            
            # Stage 2
            for name,check in DIALOGUE_CHECKS.items():
                try:
                    if not check(shot):
                        issues.append({'stage':'台词','shot':i,'issue':name})
                except: pass
            
            # Stage 3
            for name,check in ACTION_CHECKS.items():
                try:
                    if not check(shot):
                        issues.append({'stage':'动作','shot':i,'issue':name})
                except: pass
            
            report['stage1'].extend([x for x in issues if x['stage']=='文案'])
            report['stage2'].extend([x for x in issues if x['stage']=='台词'])
            report['stage3'].extend([x for x in issues if x['stage']=='动作'])
            report['total_issues']+=len(issues)
        
        report['passed']=report['total_issues']==0
        return report

preflight=PreflightChecker()
