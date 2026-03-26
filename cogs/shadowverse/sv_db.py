# cogs/shadowverse/sv_db.py
import sqlite3
import pandas as pd
from cogs.utils.constants import DB_FILE
from cogs.utils.db_handler import db  # 共通DBハンドラ


def init_database():
    """
    初期化はBot起動時に同期的に行われるか、
    もしくは各機能利用時に非同期で行う設計にするが、
    既存の互換性のためここではパスし、Cogのロード時にチェックさせるのが安全。
    """
    pass  # 共通DBハンドラ側で管理、またはCogのsetupで実行


async def async_init_database():
    """非同期でテーブル作成を行う"""
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sv_matches (
            user_id INTEGER NOT NULL, match_time TEXT NOT NULL, my_class TEXT,
            my_archetype TEXT, opponent_class TEXT, opponent_archetype TEXT, result TEXT, turn_order TEXT,
            PRIMARY KEY (user_id, match_time))""")

    # 既存DBとの互換性維持: 旧sv_matchesに列が無い場合は追加
    columns = {row["name"] for row in await db.fetchall("PRAGMA table_info(sv_matches)")}
    if "my_archetype" not in columns:
        await db.execute("ALTER TABLE sv_matches ADD COLUMN my_archetype TEXT")
    if "opponent_archetype" not in columns:
        await db.execute("ALTER TABLE sv_matches ADD COLUMN opponent_archetype TEXT")

    await db.execute("""
        CREATE TABLE IF NOT EXISTS sv_user_settings (
            user_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)""")
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sv_guild_settings (
            guild_id INTEGER PRIMARY KEY,
            season_start_date TEXT
        )
    """)
    await db.execute("""
        CREATE TABLE IF NOT EXISTS sv_class_archetypes (
            user_id INTEGER NOT NULL,
            class_name TEXT NOT NULL,
            archetype TEXT NOT NULL,
            last_used_at TEXT DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, class_name, archetype)
        )
    """)


async def set_user_channel_setting(user_id: int, channel_id: int):
    await db.execute(
        "INSERT OR REPLACE INTO sv_user_settings (user_id, channel_id) VALUES (?, ?)",
        (user_id, channel_id),
    )


async def get_user_channel_setting(user_id: int) -> int | None:
    row = await db.fetchone(
        "SELECT channel_id FROM sv_user_settings WHERE user_id = ?", (user_id,)
    )
    return row["channel_id"] if row else None


async def set_guild_season_start_date(guild_id: int, season_start_date: str):
    await db.execute(
        "INSERT OR REPLACE INTO sv_guild_settings (guild_id, season_start_date) VALUES (?, ?)",
        (guild_id, season_start_date),
    )


async def get_guild_season_start_date(guild_id: int) -> str | None:
    row = await db.fetchone(
        "SELECT season_start_date FROM sv_guild_settings WHERE guild_id = ?",
        (guild_id,),
    )
    return row["season_start_date"] if row else None


async def save_user_class_archetype(user_id: int, class_name: str, archetype: str):
    normalized = archetype.strip()
    if not normalized:
        return
    await db.execute(
        """
        INSERT INTO sv_class_archetypes (user_id, class_name, archetype, last_used_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, class_name, archetype)
        DO UPDATE SET last_used_at = CURRENT_TIMESTAMP
        """,
        (user_id, class_name, normalized),
    )


async def get_user_class_archetypes(
    user_id: int, class_name: str, limit: int = 8
) -> list[str]:
    rows = await db.fetchall(
        """
        SELECT archetype
        FROM sv_class_archetypes
        WHERE user_id = ? AND class_name = ?
        ORDER BY last_used_at DESC
        LIMIT ?
        """,
        (user_id, class_name, limit),
    )
    return [row["archetype"] for row in rows]


async def save_records_to_db(
    user_id: int, records: list[dict]
) -> tuple[list[dict], int]:
    if not records:
        return [], 0

    new_records_saved = []
    total_attempted = len(records)

    for record in records:
        try:
            cursor = await db.execute(
                """
                INSERT OR IGNORE INTO sv_matches (user_id, match_time, my_class, my_archetype, opponent_class, opponent_archetype, result, turn_order)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    user_id,
                    record["match_time"],
                    record["my_class"],
                    record.get("my_archetype"),
                    record["opponent_class"],
                    record.get("opponent_archetype"),
                    record["result"],
                    record.get("turn_order", "不明"),
                ),
            )

            if cursor.rowcount > 0:
                new_records_saved.append(record)
        except Exception as e:
            print(f"データベース挿入エラー: {e}")

    return new_records_saved, total_attempted - len(new_records_saved)


def get_records_as_df(user_id: int) -> pd.DataFrame:
    """
    Pandasは同期ライブラリなので、ここは標準のsqlite3を使用する。
    呼び出し元で asyncio.to_thread を使って非同期化されているため問題ない。
    """
    conn = sqlite3.connect(DB_FILE)
    try:
        user_df = pd.read_sql_query(
            "SELECT * FROM sv_matches WHERE user_id = ?", conn, params=(user_id,)
        )
        return user_df
    finally:
        conn.close()


async def delete_match_record(user_id: int, match_time: str) -> int:
    cursor = await db.execute(
        "DELETE FROM sv_matches WHERE user_id = ? AND match_time = ?",
        (user_id, match_time),
    )
    return cursor.rowcount


async def delete_all_user_records(user_id: int) -> int:
    cursor = await db.execute("DELETE FROM sv_matches WHERE user_id = ?", (user_id,))
    return cursor.rowcount
