import json,logging,re
logger=logging.getLogger('SmartSceneManager')

class SmartSceneManager:
    def group_shots_by_scene(self,shots):
        groups={}
        for i,shot in enumerate(shots):
            desc=shot.get('description','')
            # Extract location keywords
            loc=None
            for kw,label in [('后山|山坡|野花|夕阳','户外后山'),('军营|营地|辕门|帐内|帐外','军营'),
                              ('战场|荒原|沙场|黄沙','战场'),('断崖|崖边|山崖','断崖'),
                              ('屋内|室内|烛火|闺房','室内')]:
                if re.search(kw,desc): loc=label; break
            if not loc: loc='其他'
            if loc not in groups: groups[loc]=[]
            groups[loc].append(i)
        return groups
    
    def auto_assign_scene_images(self,shots,existing_scene_images):
        groups=self.group_shots_by_scene(shots)
        result=[{'shot_index':i,'scene_images':[]} for i in range(len(shots))]
        
        for loc,indices in groups.items():
            # Find existing scene images for this location
            master_img=''
            for idx in indices:
                if shots[idx].get('scene_image'):
                    master_img=shots[idx]['scene_image']; break
            
            # Assign to all shots in this group
            for idx in indices:
                result[idx]['scene_images'].append({
                    'url':master_img or existing_scene_images.get(loc,''),
                    'type':'master',
                    'shared':len(indices)>1
                })
                
                # If scene needs more detail (complex scene), mark for extra image
                desc=shots[idx].get('description','')
                if any(w in desc for w in ['冲锋','杀','撞','混战','变阵']):
                    if len(result[idx]['scene_images'])<2:
                        result[idx]['scene_images'].append({
                            'url':'',
                            'type':'extra',
                            'reason':'复杂场景建议补充第二张参考图',
                            'prompt_suggestion':'中式古战场动态场景：'+desc[:80]
                        })
        
        return result

smart_scene=SmartSceneManager()
