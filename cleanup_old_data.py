"""清理旧脏数据：cancelled 管线 + 过期 OSS/TOS portrait_url"""
import sqlite3, json

DB = '/www/wwwroot/api.mzsh.top/data/short_drama.db'
conn = sqlite3.connect(DB)

# 1. 删除 cancelled/failed 管线
n1 = conn.execute("DELETE FROM pipelines WHERE status IN ('cancelled','failed')").rowcount
print('deleted cancelled/failed pipelines:', n1)

# 2. 清理过期 OSS/TOS portrait_url（保留空值，让新流程重新生成）
rows = conn.execute('SELECT id, characters FROM projects').fetchall()
cleaned = 0
for r in rows:
    chars = json.loads(r[1] or '[]')
    changed = False
    for c in chars:
        pu = c.get('portrait_url', '')
        if pu and ('aliyuncs.com' in pu or 'volces.com' in pu):
            c['portrait_url'] = ''
            c['image_url'] = ''
            changed = True
    if changed:
        conn.execute('UPDATE projects SET characters=? WHERE id=?', (json.dumps(chars, ensure_ascii=False), r[0]))
        cleaned += 1
print('cleaned expired portrait projects:', cleaned)

conn.commit()
conn.close()
print('cleanup done')
