import sqlite3
import os

# データベースファイル名を指定 (ルートフォルダに作成)
DB_NAME = 'data/sv_cards.db'

# 既にファイルが存在する場合は実行しない
if os.path.exists(DB_NAME):
    print(f"データベース '{DB_NAME}' は既に存在します。")
else:
    # データベースに接続（ファイルがなければ新規作成される）
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # "cards" という名前のテーブルを作成
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        class TEXT NOT NULL,
        rarity TEXT,
        type TEXT,
        cost INTEGER,
        attack INTEGER,
        health INTEGER,
        text TEXT,
        image_url TEXT
    )
    ''')
    
    # nameカラムにUNIQUE制約を追加したので、インデックスを作成しておくと効率が良い
    cursor.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_card_name ON cards(name)')

    conn.commit()
    conn.close()
    print(f"データベース '{DB_NAME}' とテーブル 'cards' を新規作成しました。")