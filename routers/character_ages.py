from fastapi import APIRouter,Request
import json,logging
router=APIRouter(prefix="/api/v1/character",tags=["角色年龄"])

logger=logging.getLogger(__name__)

@router.post("/generate-ages")
async def generate_ages(request:Request):
    try:
        body=await request.json()
        base_url=body.get("portrait_url","")
        char_name=body.get("name","角色")
        project_id=body.get("project_id","")
        
        from agents.age_generator import age_generator
        portraits=age_generator.auto_setup_character(base_url,char_name)
        
        if project_id:
            from routers.pipeline import _execute_db
            rows=_execute_db("SELECT data,id FROM pipeline_progress WHERE project_id=? AND stage='character' ORDER BY id DESC LIMIT 1",(str(project_id),))
            if rows:
                d=json.loads(rows[0]['data'] or '{}')
                d['age_portraits']={char_name:portraits}
                _execute_db("UPDATE pipeline_progress SET data=? WHERE id=?",(json.dumps(d,ensure_ascii=False),rows[0]['id']))
        
        return {"success":True,"data":{"name":char_name,"portraits":portraits}}
    except Exception as e:
        return {"success":False,"error":str(e)[:200]}

@router.get("/ages/{project_id}/{char_name}")
async def list_ages(project_id:str,char_name:str):
    from agents.age_generator import age_generator
    portraits=age_generator._load_from_cache(char_name)
    return {"success":True,"data":{"name":char_name,"portraits":portraits}}

@router.post("/regenerate-age")
async def regenerate_age(request:Request):
    body=await request.json()
    char_name=body.get("name","")
    age_stage=body.get("stage","青年")
    base_url=body.get("base_url","")
    if not base_url or not char_name:
        return {"success":False,"error":"缺少参数"}
    
    from agents.age_generator import age_generator,AGING_PROMPTS
    from services.ai_providers import ARKImageProvider
    
    ark=ARKImageProvider()
    prompt=AGING_PROMPTS.get(age_stage,'')
    r=ark.generate_image_to_image(prompt,base_url,size='1440x2560',strength=0.35)
    new_url=r[0] if isinstance(r,list) and r else ''
    
    if new_url:
        cached=age_generator._load_from_cache(char_name)
        cached[age_stage]=new_url
        age_generator._save_to_cache(char_name,cached)
        return {"success":True,"data":{"stage":age_stage,"url":new_url}}
    return {"success":False,"error":"生成失败"}

@router.get("/preflight/{project_id}")
async def preflight_check(project_id:str):
    from routers.pipeline import _execute_db
    rows=_execute_db("SELECT data FROM pipeline_progress WHERE project_id=? AND stage='storyboard' ORDER BY id DESC LIMIT 1",(project_id,))
    if not rows: return {"success":False,"error":"无分镜数据"}
    d=json.loads(rows[0]['data'] or '{}')
    shots=d.get('shots',[])
    from agents.preflight_checker import preflight
    report=preflight.check_all(shots)
    return {"success":True,"data":report}
