import sqlite3
conn = sqlite3.connect('/www/wwwroot/api.mzsh.top/data/short_drama.db')
conn.row_factory = sqlite3.Row

# 表结构
cols = conn.execute('PRAGMA table_info(users)').fetchall()
print('users 表列:', [c['name'] for c in cols])

# 最近10个用户
rows = conn.execute('SELECT * FROM users ORDER BY id DESC LIMIT 10').fetchall()
print('\n最近用户:')
for r in rows:
    d = dict(r)
    print(f'  id={d.get("id")} username={d.get("username","")} token={str(d.get("token",""))[:20]}... phone={d.get("phone","")}')

# 重复用户名
dups = conn.execute('SELECT username, COUNT(*) as c FROM users GROUP BY username HAVING c > 1 LIMIT 5').fetchall()
if dups:
    print('\n重复用户名:', [(d['username'], d['c']) for d in dups])
else:
    print('\n无重复用户名')

# user_id 10/13/20 对应谁
for uid in [10, 13, 20]:
    r = conn.execute('SELECT id, username FROM users WHERE id=?', (uid,)).fetchone()
    print(f'  user_id={uid} -> {r["username"] if r else "不存在"}')

# 这些用户的项目
print('\n各用户项目数:')
rows = conn.execute('SELECT user_id, COUNT(*) as c FROM projects GROUP BY user_id ORDER BY c DESC LIMIT 10').fetchall()
for r in rows:
    print(f'  user_id={r["user_id"]}: {r["c"]}个项目')

conn.close()
