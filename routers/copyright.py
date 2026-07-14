from fastapi import APIRouter
import json,time
router = APIRouter(prefix="/api/v1/copyright", tags=["版权证明"])

@router.get("/report/{project_id}")
async def copyright_report(project_id: str):
    from routers.pipeline import _execute_db
    try:
        proj = _execute_db('SELECT * FROM projects WHERE id=?',(str(project_id),))
        if not proj: return {"success":False,"error":"项目不存在"}
        p = dict(proj[0])
        t = p.get('title','') or p.get('script','')[:30]
        
        sb_rows = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' ORDER BY id DESC LIMIT 1",(str(project_id),))
        shots = json.loads(sb_rows[0]['data'] or '{}').get('shots',[]) if sb_rows else []
        
        scene_rows = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='scene' ORDER BY id DESC LIMIT 1",(str(project_id),))
        scene_data = json.loads(scene_rows[0]['data'] or '{}') if scene_rows else {}
        
        vid_rows = _execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='video' ORDER BY id DESC LIMIT 1",(str(project_id),))
        videos = json.loads(vid_rows[0]['data'] or '{}').get('videos',[]) if vid_rows else []
        
        chars = json.loads(p.get('characters','[]') or '[]')
        
        report = {
            "report_type": "原创短剧版权证明",
            "project_id": project_id,
            "title": t,
            "genre": p.get('genre',''),
            "created_at": p.get('created_at',''),
            "report_time": time.strftime('%Y-%m-%d %H:%M:%S'),
            
            "1_原创声明": {
                "declaration": f"本作品《{t}》由AI短剧创作平台原创生成，内容包括剧本、角色设计、分镜画面、视频成片均为AI辅助原创制作，未使用任何第三方版权素材。创作者拥有完整著作权。",
                "creator": str(p.get('user_id','')),
                "create_date": p.get('created_at','')
            },
            
            "2_剧本授权": {
                "script_summary": (p.get('script','') or '')[:500],
                "total_shots": len(shots),
                "script_proof": "剧本存储于项目数据库，可通过平台追溯完整创作过程"
            },
            
            "3_分镜证明": {
                "total_shots": len(shots),
                "shot_list": [{"id":i+1,"description":s.get('description','')[:100],"scene":s.get('scene_image',''),"video":s.get('video_url','')[:80] if s.get('video_url') else ''} for i,s in enumerate(shots[:20])],
                "scene_images": scene_data.get('scene_images',[])[:5] if isinstance(scene_data.get('scene_images'),list) else []
            },
            
            "4_角色授权": {
                "characters": [{"name":c.get('name',''),"role":c.get('role','主角'),"portrait":c.get('portrait_url','')[:80]} for c in chars[:10]],
                "declaration": "所有角色形象均为AI虚拟生成，不涉及任何真实人物肖像权"
            },
            
            "5_生成记录": {
                "generated_videos": len(videos),
                "model": "Seedance 2.0 / LiZhen (Kuaizi API)",
                "platform": "火山方舟 / 筷子API",
                "generation_time": time.strftime('%Y-%m-%d')
            },
            
            "6_内容合规声明": {
                "no_real_person": True,
                "no_copyright_material": True,
                "compliance_note": "本作品所有画面、声音均由AI生成，不包含任何真实人物肖像、第三方版权素材或违规内容"
            }
        }
        return {"success":True,"data":report}
    except Exception as e:
        import traceback
        return {"success":False,"error":str(e)[:200],"trace":traceback.format_exc()[-300:]}
