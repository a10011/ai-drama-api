#!/usr/bin/env python3
"""查10011158在哪个DB有记录"""
import sqlite3, json, os

for dbf in ['data/short_drama.db', 'data/app.db', 'app.db']:
    if not os.path.exists('/www/wwwroot/api.mzsh.top/' + dbf):
        continue
    dbpath = '/www/wwwroot/api.mzsh.top/' + dbf
    try:
        db = sqlite3.connect(dbpath)
        db.row_factory = sqlite3.Row
        tabs = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        if 'pipeline_progress' in tabs:
            rows = db.execute("SELECT project_id,stage,status FROM pipeline_progress WHERE project_id='10011158' ORDER BY id").fetchall()
            if rows:
                print("%s:" % dbf)
                for r in rows:
                    print("  %s: %s" % (r['stage'], r['status']))
            else:
                print("%s: pipeline_progress表存在但无10011158记录" % dbf)
        db.close()
    except Exception as e:
        print("%s: %s" % (dbf, e))
