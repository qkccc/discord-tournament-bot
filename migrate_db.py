import sqlite3
import os
import shutil

# 設定
NEW_DB = "data/main.db"

# 移行元DBと、テーブル名の変更ルール
# 形式: "元ファイル": {"元テーブル名": "新テーブル名", ...}
MIGRATION_MAP = {
    # Deck関連
    "data/deck_data.db": {
        "decks": "sv_decks"
    },
    # Gemini関連
    "data/gemini_history.db": {
        "chat_history": "gemini_chat_history"
    },
    # Voice Logger関連
    "data/voice_log.db": {
        "voice_sessions": "voice_sessions",
        "sub_accounts": "user_sub_accounts",
        "guild_settings": "voice_guild_settings"
    },
    # 大会・イベント関連
    "data/tournaments.db": {
        "settings": "event_settings",           # 名前変更: 衝突回避
        "players": "event_players",             # 名前変更: 明確化
        "tournaments": "swiss_tournaments",     # 名前変更: 明確化
        "current_pairings": "swiss_pairings",
        "reported_matches": "swiss_results",
        "se_tournaments": "se_tournaments",
        "se_matches": "se_matches",
        "recruitment_sessions": "event_recruitments",
        "recruitment_participants": "event_participants",
        "rr_teams": "rr_teams",
        "rr_players": "rr_players",
        "rr_tournaments": "rr_tournaments",
        "rr_matches": "rr_matches",
        "rr_config": "rr_config"
    },
    # Shadowverse戦績関連
    "data/shadowverse_data.db": {
        "matches": "sv_matches",
        "user_settings": "sv_user_settings"
    }
}

def migrate():
    print(f"🚀 データベース統合を開始します: {NEW_DB}")

    # 新しいDBに接続
    if os.path.exists(NEW_DB):
        print(f"⚠️  警告: {NEW_DB} は既に存在します。統合データで上書きまたは追加されます。")
    
    conn_new = sqlite3.connect(NEW_DB)
    cursor_new = conn_new.cursor()

    for old_db_path, table_map in MIGRATION_MAP.items():
        if not os.path.exists(old_db_path):
            print(f"⚠️  スキップ: {old_db_path} が見つかりません。")
            continue
        
        print(f"📦 {old_db_path} からデータを移行中...")
        
        # 古いDBに接続
        try:
            conn_old = sqlite3.connect(old_db_path)
            # 行を辞書形式で取得できるように設定
            conn_old.row_factory = sqlite3.Row
            cursor_old = conn_old.cursor()

            for old_table, new_table in table_map.items():
                try:
                    # 1. 古いテーブルから全データを取得
                    cursor_old.execute(f"SELECT * FROM {old_table}")
                    rows = cursor_old.fetchall()
                    
                    if not rows:
                        print(f"  - {old_table} は空でした。スキップします。")
                        continue

                    # 2. スキーマ（列の情報）を取得してCREATE TABLE文を作成
                    # PRAGMA table_infoを使って列名を取得する手もあるが、
                    # sqlite_masterからCREATE文を取得してテーブル名だけ書き換えるのが手っ取り早い
                    cursor_old.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{old_table}'")
                    create_sql = cursor_old.fetchone()[0]
                    
                    # CREATE文のテーブル名を置換
                    new_create_sql = create_sql.replace(old_table, new_table, 1)
                    
                    # 新しいDBにテーブルを作成
                    cursor_new.execute(f"DROP TABLE IF EXISTS {new_table}") # 既存なら一旦削除して作り直し
                    cursor_new.execute(new_create_sql)
                    
                    # 3. データを挿入
                    # 列の数に合わせたプレースホルダ (?, ?, ...) を作成
                    col_count = len(rows[0])
                    placeholders = ', '.join(['?'] * col_count)
                    
                    # データをリストのタプルに変換して一括挿入
                    data_to_insert = [tuple(row) for row in rows]
                    cursor_new.executemany(f"INSERT INTO {new_table} VALUES ({placeholders})", data_to_insert)
                    
                    print(f"  ✅ {old_table} -> {new_table} ({len(rows)}件)")

                except sqlite3.OperationalError as e:
                    print(f"  ❌ エラー ({old_table}): {e}")
            
            conn_old.close()
        
        except Exception as e:
            print(f"❌ {old_db_path} の処理中にエラー: {e}")

    # 特別対応: user_data.db (Shadowverse Panel設定) の統合
    # shadowverse_data.db の user_settings と重複する可能性があるため、マージ処理を行う
    user_data_db = "data/user_data.db"
    if os.path.exists(user_data_db):
        print(f"📦 {user_data_db} をマージ中...")
        try:
            conn_ud = sqlite3.connect(user_data_db)
            cursor_ud = conn_ud.cursor()
            # sv_channel_settings があるか確認
            cursor_ud.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sv_channel_settings'")
            if cursor_ud.fetchone():
                cursor_ud.execute("SELECT * FROM sv_channel_settings")
                rows = cursor_ud.fetchall()
                # sv_user_settings に追記（重複は無視）
                cursor_new.executemany("INSERT OR IGNORE INTO sv_user_settings (user_id, channel_id) VALUES (?, ?)", rows)
                print(f"  ✅ sv_channel_settings -> sv_user_settings にマージ完了 ({len(rows)}件)")
            conn_ud.close()
        except Exception as e:
            print(f"  ⚠️ user_data.db のマージ中に軽微なエラー: {e}")

    conn_new.commit()
    conn_new.close()
    print("\n🎉 統合完了！ 'data/main.db' が作成されました。")

if __name__ == "__main__":
    migrate()