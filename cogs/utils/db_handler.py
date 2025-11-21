# cogs/utils/db_handler.py
import aiosqlite
from cogs.utils.constants import DB_FILE

class AsyncDatabaseManager:
    """非同期データベース操作を管理するクラス"""
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path

    async def execute(self, query: str, params: tuple = (), commit: bool = True):
        """SQLを実行する（INSERT, UPDATE, DELETE, CREATEなど）"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            if commit:
                await db.commit()
            return cursor

    async def fetchone(self, query: str, params: tuple = ()):
        """1行だけデータを取得する（SELECT）"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row  # カラム名でアクセスできるようにする
            async with db.execute(query, params) as cursor:
                return await cursor.fetchone()

    async def fetchall(self, query: str, params: tuple = ()):
        """全データを取得する（SELECT）"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                return await cursor.fetchall()

    async def executemany(self, query: str, params_list: list, commit: bool = True):
        """複数のSQLを一括実行する"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(query, params_list)
            if commit:
                await db.commit()

# シングルトンインスタンス（これを使えば毎回インスタンス化しなくて済む）
db = AsyncDatabaseManager()