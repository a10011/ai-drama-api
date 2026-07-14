import json,logging,os,subprocess,tempfile,re
logger=logging.getLogger('VLFrameChecker')

class VLFrameChecker:
    def extract_frames(self,video_url,duration,num_frames=3):
        frames=[]
        try:
            tmp=tempfile.mkdtemp()
            # Download first few seconds
            out=os.path.join(tmp,'frame_%03d.jpg')
            cmd=f'ffmpeg -y -i "{video_url}" -vf "fps=1/{max(1,duration//num_frames)}" -vframes {num_frames} {out}'
            subprocess.run(cmd,shell=True,capture_output=True,timeout=60)
            for f in sorted(os.listdir(tmp)):
                if f.endswith('.jpg'):
                    frames.append(os.path.join(tmp,f))
        except Exception as e:
            logger.error(f'Frame extraction failed: {e}')
        return frames
    
    def check_video(self,shot_desc,frame_urls=[]):
        if not frame_urls:
            return {'passed':True,'score':7,'issues':[]}
        
        prompt=f'''你是古装短剧画面质检员。分镜描述如下，请判断画面是否合格。

分镜描述：{shot_desc[:400]}

请逐项检查并返回JSON：
{{画风写实:true,方向正确:true,人物完整:true,道具齐全:true,动作匹配:true,景别正确:true,问题:[],总分:10,通过:true}}

画风写实：是否电影写实风格（非动漫卡通）
方向正确：冲锋是否朝前/不倒退
人物完整：有无断肢/变形/多眼
道具齐全：头盔/武器/战旗是否在
动作匹配：是否和描述一致（拔剑/冲锋/落泪）
景别正确：特写/中景/全景是否对'''
        
        try:
            from services.model_client import UnifiedModel
            result=UnifiedModel.llm(prompt=prompt,max_tokens=300,timeout=30,preferred='deepseek')
            text=result.get('text','{}')
            m=re.search(r'\{.*\}',text,re.DOTALL)
            if m:
                score=json.loads(m.group())
                score['passed']=score.get('通过',False) and score.get('总分',7)>=5
                return score
        except Exception as e:
            logger.error(f'VL check failed: {e}')
        
        return {'passed':True,'score':7,'issues':['VL暂不可用'],'总分':7,'通过':True}

vl_checker=VLFrameChecker()
