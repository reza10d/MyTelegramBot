import sqlite3
import logging

DB_PATH = "sessions.db"

def init_session_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            phone TEXT PRIMARY KEY,
            session_string TEXT NOT NULL,
            user_id INTEGER NOT NULL,
            first_name TEXT,
            username TEXT,
            is_active INTEGER DEFAULT 1,
            last_active TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
    logging.info("✅ Session database initialized")

def save_session_to_db(phone, session_string, user_id, first_name="", username=""):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO sessions 
        (phone, session_string, user_id, first_name, username, is_active, last_active)
        VALUES (?, ?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
    ''', (phone, session_string, user_id, first_name or "", username or ""))
    conn.commit()
    conn.close()

def get_all_sessions_from_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT phone, session_string, user_id, first_name, username 
        FROM sessions WHERE is_active = 1
    ''')
    results = cursor.fetchall()
    conn.close()
    return results

def get_session_by_user_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT phone, session_string, user_id, first_name, username 
        FROM sessions WHERE user_id = ? AND is_active = 1
    ''', (user_id,))
    result = cursor.fetchone()
    conn.close()
    return result

def delete_session_from_db(phone):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sessions WHERE phone = ?', (phone,))
    conn.commit()
    conn.close()

def delete_session_by_user_id(user_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('DELETE FROM sessions WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

def get_session_count():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('SELECT COUNT(*) FROM sessions WHERE is_active = 1')
    count = cursor.fetchone()[0]
    conn.close()
    return count

def clear_inactive_sessions():
    logging.info("🧹 clear_inactive_sessions called")
