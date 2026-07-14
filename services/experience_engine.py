import sqlite3, json, os, time, logging

logger = logging.getLogger('ExperienceEngine')
DB = os.path.join(os.path.dirname(__file__), '..', 'data', 'short_drama.db')

class ExperienceEngine:
    def __init__(self):
        self._init_db()
    
    def _conn(self):
        return sqlite3.connect(DB)
    
    def _hash(self, text):
        return str(hash(str(text)[:500]))[-16:]
    
    def _init_db(self):
        conn = self._conn()
        conn.execute('''CREATE TABLE IF NOT EXISTS agent_experience (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER DEFAULT 0,
            agent_name TEXT, task_type TEXT, scene_type TEXT,
            input_hash TEXT, input_text TEXT, output_text TEXT,
            genres TEXT, success INTEGER DEFAULT 1, effectiveness INTEGER DEFAULT 3,
            created REAL)''')
        # New: auto-improve patterns
        conn.execute('''CREATE TABLE IF NOT EXISTS smart_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            pattern_type TEXT, keywords TEXT, winning_prompt TEXT,
            success_rate REAL, times_used INTEGER DEFAULT 1)''')
        conn.commit(); conn.close()
    
    def log_generation(self, agent_name, task_type, scene_type, input_text, output_text, genres='', success=True, effectiveness=3, user_id=0):
        try:
            conn = self._conn()
            conn.execute(
                'INSERT INTO agent_experience (user_id,agent_name,task_type,scene_type,input_hash,input_text,output_text,genres,success,effectiveness,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (user_id,agent_name,task_type,scene_type,self._hash(input_text),
                 str(input_text)[:2000], str(output_text)[:5000] if output_text else '',
                 str(genres)[:200], 1 if success else 0, effectiveness, time.time()))
            conn.commit(); conn.close()
            # Auto-learn if highly effective
            if success and effectiveness >= 4:
                self._extract_pattern(agent_name, task_type, scene_type, input_text[:200], genres)
        except Exception as e:
            logger.warning(f'log_generation failed: {e}')
    
    def _extract_pattern(self, agent_name, task_type, scene_type, prompt, genres):
        keywords = ' '.join([w for w in prompt.split() if len(w) > 3][:5])
        try:
            conn = self._conn()
            existing = conn.execute('SELECT id,times_used,success_rate FROM smart_patterns WHERE pattern_type=? AND keywords=?', (agent_name, keywords)).fetchone()
            if existing:
                conn.execute('UPDATE smart_patterns SET times_used=times_used+1 WHERE id=?', (existing[0],))
            else:
                conn.execute('INSERT INTO smart_patterns (pattern_type,keywords,winning_prompt,success_rate) VALUES (?,?,?,1.0)',
                    (agent_name, keywords, prompt[:500]))
            conn.commit(); conn.close()
        except: pass
    
    def query_similar(self, agent_name, scene_type, input_text, limit=2, user_id=0):
        try:
            conn = self._conn()
            rows = conn.execute(
                'SELECT * FROM agent_experience WHERE agent_name=? AND success=1 AND user_id=? ORDER BY id DESC LIMIT ?',
                (agent_name, user_id, limit*3)).fetchall()
            conn.close()
            matches = []
            for r in rows:
                if scene_type in str(r[4]) or scene_type in str(r[5]):
                    matches.append({'output_text': r[6], 'effectiveness': r[9], 'user_edits': ''})
            return matches[:limit]
        except: return []
    
    def build_prompt_hint(self, agent_name, scene_type, input_text, user_id=0, genres=''):
        hints = []
        # 1. Get winning patterns for this type
        try:
            conn = self._conn()
            patterns = conn.execute('SELECT keywords,winning_prompt,success_rate FROM smart_patterns WHERE pattern_type=? ORDER BY success_rate DESC LIMIT 3', (agent_name,)).fetchall()
            for p in patterns:
                hints.append(f'(成功模式[成功率{p[2]:.0%}]: {p[1][:100]})')
            conn.close()
        except: pass
        
        # 2. Similar successful cases
        similar = self.query_similar(agent_name, scene_type, input_text, limit=2, user_id=user_id)
        for s in similar:
            if s.get('effectiveness', 0) >= 4:
                hints.append(f'(类似场景成功案例: {s.get("output_text","")[:80]})')
        
        return '\n'.join(hints[:3]) if hints else ''
    
    def get_failure_lessons(self, agent_name, scene_type, user_id=0):
        try:
            conn = self._conn()
            rows = conn.execute('SELECT input_text,output_text FROM agent_experience WHERE agent_name=? AND scene_type=? AND success=0 AND user_id=? ORDER BY id DESC LIMIT 3', (agent_name, scene_type, user_id)).fetchall()
            conn.close()
            if rows:
                return f'(避免重复以下失败: {rows[0][1][:80] if rows[0][1] else "无详情"})'
        except: pass
        return ''

experience_engine = ExperienceEngine()
