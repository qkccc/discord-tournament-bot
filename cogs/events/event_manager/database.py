# cogs/events/event_manager/database.py
import sqlite3
import logging
import json
from typing import Union, List, Tuple
from cogs.utils.constants import DB_FILE

log = logging.getLogger(__name__)

class DatabaseManager:
    """SQLiteデータベースの接続と操作を管理するクラス"""
    def __init__(self):
        # 定数ファイルからデータベースパスを取得
        self.db_path = DB_FILE
        self.setup_database()

    def _get_connection(self) -> sqlite3.Connection:
        """データベース接続を取得し、外部キー制約を有効にする"""
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def setup_database(self):
        """データベースのテーブルを初期化し、必要な列が存在するか確認・追加する"""
        with self._get_connection() as conn:
            c = conn.cursor()
            
            # --- テーブル作成 (IF NOT EXISTS) ---
            # 名前変更: settings -> event_settings
            c.execute('''CREATE TABLE IF NOT EXISTS event_settings (guild_id INTEGER PRIMARY KEY, main_channel_id INTEGER, match_channel_id INTEGER)''')
            
            # 名前変更: players -> event_players
            c.execute('''CREATE TABLE IF NOT EXISTS event_players (guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, display_name TEXT NOT NULL, is_dummy BOOLEAN NOT NULL, score REAL DEFAULT 0, opponents TEXT DEFAULT '[]', byes INTEGER DEFAULT 0, wins REAL DEFAULT 0, losses REAL DEFAULT 0, matches_played INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id))''')
            
            # 名前変更: tournaments -> swiss_tournaments (スイスドロー用)
            c.execute('''CREATE TABLE IF NOT EXISTS swiss_tournaments (guild_id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL, round_num INTEGER NOT NULL, max_rounds INTEGER)''')
            
            # 名前変更: current_pairings -> swiss_pairings
            c.execute('''CREATE TABLE IF NOT EXISTS swiss_pairings (guild_id INTEGER NOT NULL, player1_id INTEGER NOT NULL, player2_id INTEGER, PRIMARY KEY (guild_id, player1_id))''')
            
            # 名前変更: reported_matches -> swiss_results
            c.execute('''CREATE TABLE IF NOT EXISTS swiss_results (guild_id INTEGER NOT NULL, round_num INTEGER NOT NULL, winner_id INTEGER NOT NULL, loser_id INTEGER NOT NULL, PRIMARY KEY (guild_id, round_num, winner_id, loser_id))''')
            
            # se_tournaments (変更なし)
            c.execute('''CREATE TABLE IF NOT EXISTS se_tournaments (guild_id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL, num_players INTEGER NOT NULL, num_rounds INTEGER NOT NULL, bracket_message_id INTEGER)''')
            
            # se_matches (変更なし)
            c.execute('''CREATE TABLE IF NOT EXISTS se_matches (guild_id INTEGER NOT NULL, match_id TEXT NOT NULL, round_num INTEGER NOT NULL, match_in_round INTEGER NOT NULL, player1_id INTEGER, player2_id INTEGER, player1_source_match_id TEXT, player2_source_match_id TEXT, winner_id INTEGER, is_bye BOOLEAN DEFAULT FALSE, PRIMARY KEY (guild_id, match_id))''')
            
            # 名前変更: recruitment_sessions -> event_recruitments
            c.execute('''
                CREATE TABLE IF NOT EXISTS event_recruitments (
                    guild_id INTEGER PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL
                )
            ''')
            
            # 名前変更: recruitment_participants -> event_participants
            c.execute('''
                CREATE TABLE IF NOT EXISTS event_participants (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    display_name TEXT NOT NULL,
                    is_dummy BOOLEAN NOT NULL DEFAULT FALSE,
                    PRIMARY KEY (guild_id, user_id)
                )
            ''')

            # rr_teams (変更なし)
            c.execute('''
                CREATE TABLE IF NOT EXISTS rr_teams (
                    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    wins INTEGER DEFAULT 0,
                    losses INTEGER DEFAULT 0,
                    score_for INTEGER DEFAULT 0,
                    role_id INTEGER
                )
            ''')
            
            # rr_players (変更なし)
            c.execute('''CREATE TABLE IF NOT EXISTS rr_players (user_id INTEGER NOT NULL, guild_id INTEGER NOT NULL, team_id INTEGER NOT NULL, display_name TEXT NOT NULL, is_dummy BOOLEAN NOT NULL, position INTEGER, PRIMARY KEY (user_id, guild_id), FOREIGN KEY (team_id) REFERENCES rr_teams(team_id) ON DELETE CASCADE)''')
            
            # rr_tournaments (変更なし)
            c.execute('''
                CREATE TABLE IF NOT EXISTS rr_tournaments (
                    guild_id INTEGER PRIMARY KEY, is_active BOOLEAN NOT NULL,
                    message_id INTEGER, channel_id INTEGER, current_round INTEGER DEFAULT 1,
                    member_order TEXT, participant_role_id INTEGER
                )
            ''')
            
            # rr_matches (変更なし)
            c.execute('''
                CREATE TABLE IF NOT EXISTS rr_matches (
                    match_id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL,
                    round_num INTEGER, team1_id INTEGER NOT NULL, team2_id INTEGER NOT NULL,
                    team1_score INTEGER, team2_score INTEGER, status TEXT NOT NULL DEFAULT 'pending',
                    message_id INTEGER,
                    FOREIGN KEY (team1_id) REFERENCES rr_teams(team_id) ON DELETE CASCADE,
                    FOREIGN KEY (team2_id) REFERENCES rr_teams(team_id) ON DELETE CASCADE
                )
            ''')
            
            # rr_config (変更なし)
            c.execute('''
                CREATE TABLE IF NOT EXISTS rr_config (
                    guild_id INTEGER PRIMARY KEY,
                    role_id INTEGER,
                    panel_message_id INTEGER,
                    panel_channel_id INTEGER
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS history_tournaments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,  -- 'swiss', 'se', 'roundrobin'
                    end_date TEXT DEFAULT CURRENT_TIMESTAMP,
                    winner_name TEXT,
                    details TEXT -- JSON形式でその他の情報を保存
                )
            ''')

            c.execute('''
                CREATE TABLE IF NOT EXISTS history_rankings (
                    tournament_id INTEGER,
                    rank INTEGER,
                    name TEXT,
                    info TEXT,
                    FOREIGN KEY(tournament_id) REFERENCES history_tournaments(id) ON DELETE CASCADE
                )
            ''')

            # --- テーブル構造の自動修復機能 (カラム追加チェック) ---
            # RR関連はテーブル名変更がないため、そのままチェック
            
            c.execute("PRAGMA table_info(rr_tournaments)")
            columns = [row[1] for row in c.fetchall()]
            if 'current_round' not in columns: c.execute("ALTER TABLE rr_tournaments ADD COLUMN current_round INTEGER DEFAULT 1")
            if 'member_order' not in columns: c.execute("ALTER TABLE rr_tournaments ADD COLUMN member_order TEXT")
            if 'participant_role_id' not in columns: c.execute("ALTER TABLE rr_tournaments ADD COLUMN participant_role_id INTEGER")
            
            c.execute("PRAGMA table_info(rr_matches)")
            columns = [row[1] for row in c.fetchall()]
            if 'round_num' not in columns: c.execute("ALTER TABLE rr_matches ADD COLUMN round_num INTEGER")

            c.execute("PRAGMA table_info(rr_teams)")
            columns = [row[1] for row in c.fetchall()]
            if 'score_for' not in columns: c.execute("ALTER TABLE rr_teams ADD COLUMN score_for INTEGER DEFAULT 0")
            if 'role_id' not in columns: 
                log.info("rr_teamsテーブルにrole_id列を追加します...")
                c.execute("ALTER TABLE rr_teams ADD COLUMN role_id INTEGER")

            c.execute("PRAGMA table_info(rr_players)")
            columns = [row[1] for row in c.fetchall()]
            if 'position' not in columns: c.execute("ALTER TABLE rr_players ADD COLUMN position INTEGER")

            c.execute("PRAGMA table_info(rr_config)")
            columns = [row[1] for row in c.fetchall()]
            if 'panel_message_id' not in columns: c.execute("ALTER TABLE rr_config ADD COLUMN panel_message_id INTEGER")
            if 'panel_channel_id' not in columns: c.execute("ALTER TABLE rr_config ADD COLUMN panel_channel_id INTEGER")

            # インデックス
            c.execute('CREATE INDEX IF NOT EXISTS idx_rr_matches_message_id ON rr_matches (message_id)')
            c.execute('CREATE INDEX IF NOT EXISTS idx_rr_players_team_id ON rr_players (team_id)')

            conn.commit()
            log.info("データベースのセットアップ/更新が完了しました。")

    def execute(self, query: str, params: Union[tuple, List[Tuple]] = (), *, return_lastrowid: bool = False):
        with self._get_connection() as conn:
            c = conn.cursor()
            if isinstance(params, list): c.executemany(query, params)
            else: c.execute(query, params)
            conn.commit()
            if return_lastrowid: return c.lastrowid

    def fetchone(self, query: str, params: tuple = ()):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row; c = conn.cursor(); c.execute(query, params); return c.fetchone()

    def fetchall(self, query: str, params: tuple = ()):
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row; c = conn.cursor(); c.execute(query, params); return c.fetchall()
