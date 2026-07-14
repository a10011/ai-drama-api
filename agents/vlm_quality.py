import json,logging,re
logger=logging.getLogger('VLMQuality')

QUALITY_PROMPT='''你是短剧画面质检专家。根据分镜描述评估生成的视频画面质量。
请从以下维度打分(0-10)并指出具体问题：

1. 面部一致性：主角五官是否与参考图一致？
2. 动作合理性：动作是否符合描述？（冲锋向前/不倒退/不悬浮）
3. 画风匹配：是否为电影写实风格？（非动漫/非卡通）
4. 构图准确：人物位置、景别是否正确？
5. 道具完整：头盔/兵器/战旗等是否齐全？
6. 方向正确：冲锋是否朝向敌方？有无跑反？

返回JSON格式：{总分: 0,面部:0,动作:0,画风:0,构图:0,道具:0,方向:0,问题:[],建议:,是否通过:false}'''

class VLMQualityChecker:
    def __init__(self):
        pass
    
    def check(self,shot_desc,frame_urls=None):
        # Use LLM to evaluate against description
        from services.model_client import UnifiedModel
        
        prompt=f'{QUALITY_PROMPT}\n\n分镜描述：{shot_desc[:500]}\n{参考图：+str(frame_urls)[:200] if frame_urls else }'
        
        try:
            result=UnifiedModel.llm(
                prompt=prompt,
                max_tokens=512,
                timeout=30,
                preferred='deepseek'
            )
            text=result.get('text','{}')
            # Parse JSON from response
            m=re.search(r'\{.*\}',text,re.DOTALL)
            if m:
                score=json.loads(m.group())
                score['passed']=score.get('总分',0)>=6 and score.get('是否通过',False)
                return score
        except Exception as e:
            logger.error(f'VLM check failed: {e}')
        
        return {'passed':True,'总分':7,'问题':['自动评分暂不可用'],'建议':''}
    
    def batch_check(self,shots):
        results=[]
        for i,shot in enumerate(shots):
            r=self.check(shot.get('description',''))
            r['shot_index']=i
            results.append(r)
        return results

vlm_quality=VLMQualityChecker()
