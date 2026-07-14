# Scene image router V2 - face lock support
from fastapi import APIRouter, Request
from agents_v2.scene_agent import SceneAgent
import logging, json, sqlite3

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/v1/shot', tags=['scene'])

def _load_character_portraits(project_id):
    try:
        db = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
        db.row_factory = sqlite3.Row
        r = db.execute('SELECT characters FROM projects WHERE id=?', (str(project_id),)).fetchone()
        db.close()
        if r:
            chars = json.loads(r['characters'] or '[]')
            return {c.get('name', ''): c.get('portrait_url', '') or c.get('figure_url', '') for c in chars if c.get('name') and (c.get('portrait_url') or c.get('figure_url'))}
    except:
        pass
    return {}

@router.post('/scene-image')
async def generate_scene_image(request: Request):
    try:
        body = await request.json()
        desc = body.get('description', '')
        project_id = body.get('project_id', '')
        if not desc:
            return {'success': False, 'message': 'no desc'}

        portraits = _load_character_portraits(project_id) if project_id else {}
        reference_image = ''
        ref_images = []
        for name, url in portraits.items():
            if name in desc and url:
                if not reference_image:
                    reference_image = url
                elif url not in ref_images:
                    ref_images.append(url)

        genre_val = ''
        if project_id:
            try:
                db = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
                db.row_factory = sqlite3.Row
                dr = db.execute('SELECT data FROM pipeline_progress WHERE project_id=? AND stage="director" AND status="completed" ORDER BY id DESC LIMIT 1', (str(project_id),)).fetchone()
                if dr:
                    dd = json.loads(dr['data'] or '{}')
                    genre_val = dd.get('analysis', {}).get('genre', '')
                db.close()
            except:
                pass

        agent = SceneAgent(0)
        shot = {'description': desc}
        if reference_image:
            shot['reference_image'] = reference_image
            if ref_images:
                shot['ref_images'] = ref_images

        # 加载角色外貌数据（保持服装一致）
        chars_data = []
        if project_id:
            try:
                db2 = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
                db2.row_factory = sqlite3.Row
                cr = db2.execute('SELECT characters FROM projects WHERE id=?', (str(project_id),)).fetchone()
                db2.close()
                if cr and cr['characters']:
                    chars_data = json.loads(cr['characters'] or '[]')
            except:
                pass

        task = {'pipeline_id': '', 'user_id': 0, 'data': {'shots': [shot], 'genre': genre_val, 'characters': chars_data}}
        result = agent.execute(task)

        images = result.get('scene_images', result.get('images', {}))
        url = ''
        if isinstance(images, dict):
            url = list(images.values())[0] if images else ''
        elif isinstance(images, list) and images:
            u = images[0]
            url = u if isinstance(u, str) else u.get('image_url', u.get('url', ''))
        if url:
            return {'success': True, 'data': {'scene_image_url': url, 'image_url': url}}
        return {'success': False, 'message': result.get('error', 'gen failed')}
    except Exception as e:
        return {'success': False, 'message': str(e)[:100]}
