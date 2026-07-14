from fastapi import APIRouter,Request
import json,sqlite3,os,time
router=APIRouter(prefix="/api/v1",tags=["用户Key"])

DB=os.path.join(os.path.dirname(__file__),'..','data','short_drama.db')

@router.get("/user-keys/")
async def get_keys(request:Request):
    from routers.pipeline import _execute_db
    try:
        token=request.headers.get('Authorization','').replace('Bearer ','')
        rows=_execute_db('SELECT id FROM users WHERE token=?',(token,))
        uid=str(rows[0]['id']) if rows else '0'
        conn=sqlite3.connect(DB);conn.row_factory=sqlite3.Row
        conn.execute('CREATE TABLE IF NOT EXISTS user_api_keys (user_id TEXT PRIMARY KEY,ark_key TEXT,updated REAL)')
        r=conn.execute('SELECT ark_key FROM user_api_keys WHERE user_id=?',(uid,)).fetchone()
        conn.close()
        if r:
            ark=r['ark_key']; ali=r['ali_key'] if len(r.keys())>2 else ''
            return {"success":True,"data":{"ark_volc":{"api_key":(ark[:10]+'****') if ark else '',"has":bool(ark)},"ali_bailian":{"api_key":(ali[:10]+'****') if ali else '',"has":bool(ali)}}}
        return {"success":True,"data":{"ark_volc":{"api_key":"","has":False},"ali_bailian":{"api_key":"","has":False}}}
    except: return {"success":True,"data":{"ark_volc":{"api_key":"","has":False},"ali_bailian":{"api_key":"","has":False}}}

@router.post("/user-keys/")
async def save_keys(request:Request):
    try:
        body=await request.json()
        from routers.pipeline import _execute_db
        token=request.headers.get('Authorization','').replace('Bearer ','')
        rows=_execute_db('SELECT id FROM users WHERE token=?',(token,))
        uid=str(rows[0]['id']) if rows else '0'
        provider=body.get('provider','ark_volc'); api_key=body.get('api_key','').strip()
        if not api_key: return {"success":False,"error":"Key empty"}
        col='ali_key' if provider=='ali_bailian' else 'ark_key'
        conn=sqlite3.connect(DB)
        conn.execute('CREATE TABLE IF NOT EXISTS user_api_keys (user_id TEXT PRIMARY KEY,ark_key TEXT DEFAULT "",ali_key TEXT DEFAULT "",updated REAL)')
        if provider == 'ali_bailian':
            conn.execute('INSERT OR REPLACE INTO user_api_keys (user_id,ali_key,updated) VALUES (?,?,?)',(uid,api_key,time.time()))
        else:
            conn.execute('INSERT OR REPLACE INTO user_api_keys (user_id,ark_key,updated) VALUES (?,?,?)',(uid,api_key,time.time()))
        conn.commit();conn.close()
        return {"success":True,"message":"保存成功"}
    except Exception as e: return {"success":False,"error":str(e)[:200]}


@router.post("/user-keys/activate")
async def activate_provider(request:Request):
    try:
        body=await request.json()
        from routers.pipeline import _execute_db
        token=request.headers.get("Authorization","").replace("Bearer ","")
        rows=_execute_db("SELECT id FROM users WHERE token=?",(token,))
        uid=str(rows[0]["id"]) if rows else "0"
        provider=body.get("provider","ark_volc")
        conn=sqlite3.connect(DB)
        conn.execute("CREATE TABLE IF NOT EXISTS user_settings (user_id TEXT PRIMARY KEY,active_provider TEXT DEFAULT 'ark_volc')")
        conn.execute("INSERT OR REPLACE INTO user_settings VALUES (?,?)",(uid,provider))
        conn.commit();conn.close()
        return {"success":True,"message":"switched"}
    except Exception as e: return {"success":False,"error":str(e)[:200]}

@router.get("/user-keys/active")
async def get_active_provider(request:Request):
    try:
        from routers.pipeline import _execute_db
        token=request.headers.get("Authorization","").replace("Bearer ","")
        rows=_execute_db("SELECT id FROM users WHERE token=?",(token,))
        uid=str(rows[0]["id"]) if rows else "0"
        conn=sqlite3.connect(DB)
        conn.execute("CREATE TABLE IF NOT EXISTS user_settings (user_id TEXT PRIMARY KEY,active_provider TEXT DEFAULT 'ark_volc')")
        r=conn.execute("SELECT active_provider FROM user_settings WHERE user_id=?",(uid,)).fetchone()
        conn.close()
        return {"success":True,"data":{"active":r["active_provider"] if r else "ark_volc"}}
    except: return {"success":True,"data":{"active":"ark_volc"}}

@router.get("/user-keys/providers")
async def list_providers():
    return {"success":True,"data":{"ark_volc":"火山方舟(豆包Seedance/DeepSeek)","ali_bailian":"阿里百炼(快乐马/wan2.7视频)"}}

@router.delete("/user-keys/{provider}")
async def delete_keys(provider:str,request:Request):
    try:
        from routers.pipeline import _execute_db
        token=request.headers.get('Authorization','').replace('Bearer ','')
        rows=_execute_db('SELECT id FROM users WHERE token=?',(token,))
        uid=str(rows[0]['id']) if rows else '0'
        conn=sqlite3.connect(DB)
        conn.execute('DELETE FROM user_api_keys WHERE user_id=?',(uid,))
        conn.commit();conn.close()
        return {"success":True,"message":"已删除"}
    except Exception as e: return {"success":False,"error":str(e)[:200]}
