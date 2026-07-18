"""数据库迁移脚本 - 为现有 users 表添加 expires_at 列"""
import sqlite3
import os
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), 'data', 'short_drama.db')

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}, skipping")
        return
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Check if expires_at column already exists
    cursor.execute("PRAGMA table_info(users)")
    columns = [row[1] for row in cursor.fetchall()]
    
    if 'expires_at' not in columns:
        print("Adding expires_at column to users table...")
        cursor.execute("ALTER TABLE users ADD COLUMN expires_at INTEGER")
        # Set existing users to expire in 30 days
        import time
        cursor.execute(f"UPDATE users SET expires_at = {int(time.time()) + 2592000} WHERE expires_at IS NULL")
        conn.commit()
        print("Migration completed. All existing users expire in 30 days.")
    else:
        print("expires_at column already exists, skipping migration.")
    
    conn.close()

if __name__ == '__main__':
    migrate()
