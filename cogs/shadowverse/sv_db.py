# sv_db.py
import sqlite3
import pandas as pd
from .sv_constants import DB_FILE

def init_database():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sv_matches (
                user_id INTEGER NOT NULL, match_time TEXT NOT NULL, my_class TEXT,
                opponent_class TEXT, result TEXT, turn_order TEXT,
                PRIMARY KEY (user_id, match_time))""")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sv_user_settings (
                user_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)""")
        conn.commit()

def set_user_channel_setting(user_id: int, channel_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO sv_user_settings (user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))

def get_user_channel_setting(user_id: int) -> int | None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute("SELECT channel_id FROM sv_user_settings WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

def save_records_to_db(user_id: int, records: list[dict]) -> tuple[list[dict], int]:
    if not records:
        return [], 0
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    new_records_saved = []
    total_attempted = len(records)
    for record in records:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO sv_matches (user_id, match_time, my_class, opponent_class, result, turn_order)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, record['match_time'], record['my_class'], record['opponent_class'], record['result'], record.get('turn_order', '不明')))
            if cursor.rowcount > 0:
                new_records_saved.append(record)
        except sqlite3.Error as e:
            print(f"データベース挿入エラー: {e}")
    conn.commit()
    conn.close()
    return new_records_saved, total_attempted - len(new_records_saved)

def get_records_as_df(user_id: int) -> pd.DataFrame:
    conn = sqlite3.connect(DB_FILE)
    try:
        user_df = pd.read_sql_query("SELECT * FROM sv_matches WHERE user_id = ?", conn, params=(user_id,))
        return user_df
    finally:
        conn.close()

def delete_match_record(user_id: int, match_time: str) -> int:
    """
    指定されたユーザーIDと対戦時間（match_time）に一致する戦績を1件削除します。
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM sv_matches WHERE user_id = ? AND match_time = ?",
            (user_id, match_time)
        )
        conn.commit()
        return cursor.rowcount

def delete_all_user_records(user_id: int) -> int:
    """
    指定されたユーザーIDの戦績をすべて削除します。
    """
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM sv_matches WHERE user_id = ?",
            (user_id,)
        )
        conn.commit()
        return cursor.rowcount

