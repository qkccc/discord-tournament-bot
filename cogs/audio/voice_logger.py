import discord
from discord.ext import commands, tasks
import datetime
import sqlite3
import os
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

# --- 環境変数から設定を読み込み ---
DB_FILE = os.getenv("DB_FILE", "data/voice_log.db")

class VoiceLoggerCog(commands.Cog):
    # JSTタイムゾーンをクラス変数として定義
    JST = datetime.timezone(datetime.timedelta(hours=9))
    # JSTで土曜22:05は、UTCで土曜13:05
    MENTION_TIME_UTC = datetime.time(hour=13, minute=5, tzinfo=datetime.timezone.utc)

    def __init__(self, bot):
        self.bot = bot
        
        # --- 環境変数から設定を読み込み ---
        self.TARGET_ROLE_NAME = os.getenv("TARGET_ROLE_NAME")
        self.ALERT_CHANNEL_ID = self._get_env_var_as_int("ALERT_CHANNEL_ID")
        self.ATTENDANCE_CHANNEL_ID = self._get_env_var_as_int("ATTENDANCE_CHANNEL_ID")
        # レポート機能用の環境変数を追加
        self.VC_REPORT_TARGET_ID = self._get_env_var_as_int("VC_REPORT_TARGET_ID")
        self.VC_REPORT_DESTINATION_ID = self._get_env_var_as_int("VC_REPORT_DESTINATION_ID")

        # 必須設定のチェック (CALL_RECRUITMENT_CHANNEL_ID はサーバーごとの設定になったため削除)
        if not all([self.TARGET_ROLE_NAME, self.ALERT_CHANNEL_ID, self.ATTENDANCE_CHANNEL_ID]):
             raise ValueError("必要な設定（ロール名やチャンネルID）が.envファイルに設定されていません。")

        self.db_conn = self._setup_database()
        self.active_vc_sessions = {}
        # レポート対象VCの通話開始時刻を保持する変数
        self.report_vc_start_time = None
        # ★新規追加: 定例通知をスキップするためのフラグ
        self.skip_weekly_mention = False
        
        self.weekly_mention_task.start()

    def _get_env_var_as_int(self, var_name: str) -> int | None:
        """環境変数を整数として取得する。見つからないか、不正な値の場合はNoneを返す。"""
        val_str = os.getenv(var_name)
        if val_str and val_str.isdigit():
            return int(val_str)
        else:
            print(f"警告: 環境変数 '{var_name}' が見つからないか、有効な整数ではありません。")
            return None

    def _setup_database(self):
        """データベース接続を初期化し、テーブルが存在しない場合は作成する"""
        try:
            conn = sqlite3.connect(DB_FILE)
            cursor = conn.cursor()
            # 既存のテーブル
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS voice_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    guild_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    join_time TEXT NOT NULL,
                    leave_time TEXT
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS sub_accounts (
                    main_user_id INTEGER NOT NULL,
                    sub_user_id INTEGER NOT NULL PRIMARY KEY
                )
            """)
            # ★新規追加: サーバーごとの設定を保存するテーブル
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS guild_settings (
                    guild_id INTEGER PRIMARY KEY,
                    call_notification_channel_id INTEGER
                )
            """)
            conn.commit()
            return conn
        except sqlite3.Error as e:
            print(f"データベースエラー: {e}")
            return None

    @commands.Cog.listener()
    async def on_ready(self):
        await self._cleanup_stale_sessions()
        await self._recover_report_vc_state()

    async def _recover_report_vc_state(self):
        """ボット再起動時にレポート対象VCの状態を復元する"""
        if not self.VC_REPORT_TARGET_ID:
            return
            
        target_vc = self.bot.get_channel(self.VC_REPORT_TARGET_ID)
        if target_vc and target_vc.members:
            print(f"レポート対象VC '{target_vc.name}' で通話中のセッションを検知しました。開始時刻を復元します。")
            try:
                cursor = self.db_conn.cursor()
                member_ids = tuple(m.id for m in target_vc.members)
                cursor.execute(f"""
                    SELECT MIN(join_time) FROM voice_sessions
                    WHERE channel_id = ? AND user_id IN ({','.join('?' for _ in member_ids)}) AND leave_time IS NULL
                """, (self.VC_REPORT_TARGET_ID, *member_ids))
                result = cursor.fetchone()
                if result and result[0]:
                    self.report_vc_start_time = datetime.datetime.fromisoformat(result[0])
                    print(f"復元された通話開始時刻(UTC): {self.report_vc_start_time}")
            except sqlite3.Error as e:
                print(f"レポートVCの状態復元中にDBエラー: {e}")


    async def _cleanup_stale_sessions(self):
        cursor = self.db_conn.cursor()
        cursor.execute("SELECT id, guild_id, user_id, channel_id FROM voice_sessions WHERE leave_time IS NULL")
        stale_sessions = cursor.fetchall()
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sessions_to_close = []
        for session_id, guild_id, user_id, channel_id in stale_sessions:
            guild = self.bot.get_guild(guild_id)
            if not guild:
                sessions_to_close.append((now_utc_iso, session_id))
                continue
            member = guild.get_member(user_id)
            if not member or not member.voice or member.voice.channel.id != channel_id:
                sessions_to_close.append((now_utc_iso, session_id))
        if sessions_to_close:
            cursor.executemany("UPDATE voice_sessions SET leave_time = ? WHERE id = ?", sessions_to_close)
            self.db_conn.commit()
            print(f"{len(sessions_to_close)}件の古いセッションをクリーンアップしました。")

    def cog_unload(self):
        if self.db_conn:
            self.db_conn.close()
        self.weekly_mention_task.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        # --- 一般的な通話開始・終了通知とセッション記録 ---
        if before.channel and before.channel != after.channel:
            self._end_user_session(member, before.channel, now_utc)
            if not before.channel.members:
                start_time_utc = self.active_vc_sessions.pop(before.channel.id, None)
                if start_time_utc:
                    duration = now_utc - start_time_utc
                    if before.channel.id != self.VC_REPORT_TARGET_ID:
                        # ★変更: member.guild を渡して、そのサーバーの設定で通知を送る
                        await self.send_call_end_notification(before.channel, duration, member.guild)
        
        if after.channel and after.channel != before.channel:
            if len(after.channel.members) == 1:
                if after.channel.id != self.VC_REPORT_TARGET_ID:
                    # ★変更: member.guild を渡して、そのサーバーの設定で通知を送る
                    await self.send_call_start_notification(member, after.channel, now_utc)
                self.active_vc_sessions[after.channel.id] = now_utc
            self._start_user_session(member, after.channel, now_utc)

        # --- レポート対象VCの処理 ---
        if self.VC_REPORT_TARGET_ID:
            if after.channel and after.channel.id == self.VC_REPORT_TARGET_ID:
                if len(after.channel.members) == 1 and not self.report_vc_start_time:
                    print(f"レポート対象VC '{after.channel.name}' での通話開始を記録しました。")
                    self.report_vc_start_time = now_utc
            
            if before.channel and before.channel.id == self.VC_REPORT_TARGET_ID:
                if not before.channel.members and self.report_vc_start_time:
                    print(f"レポート対象VC '{before.channel.name}' での通話終了を検知。レポートを生成します。")
                    await self._generate_and_send_vc_report(
                        guild=before.channel.guild,
                        start_time_utc=self.report_vc_start_time,
                        end_time_utc=now_utc
                    )
                    self.report_vc_start_time = None

    def _get_notification_channel_id(self, guild_id: int) -> int | None:
        """★新規追加: データベースからサーバーの通知チャンネルIDを取得するヘルパー関数"""
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT call_notification_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id,))
            result = cursor.fetchone()
            # 結果が存在し、かつチャンネルIDが設定されている場合のみIDを返す
            return result[0] if result and result[0] else None
        except sqlite3.Error as e:
            print(f"通知チャンネルIDの取得中にDBエラーが発生しました: {e}")
            return None

    async def _generate_and_send_vc_report(self, guild: discord.Guild, start_time_utc: datetime.datetime, end_time_utc: datetime.datetime):
        """VC入室レポートを生成し、指定のスレッドに投稿する"""
        if not self.VC_REPORT_DESTINATION_ID: return
        destination_thread = self.bot.get_channel(self.VC_REPORT_DESTINATION_ID)
        if not destination_thread or not isinstance(destination_thread, discord.Thread): return
        target_vc = self.bot.get_channel(self.VC_REPORT_TARGET_ID)
        if not target_vc: return

        start_time_utc_iso = start_time_utc.isoformat()
        end_time_utc_iso = end_time_utc.isoformat()

        try:
            cursor = self.db_conn.cursor()
            cursor.execute("""
                SELECT user_id, MIN(join_time)
                FROM voice_sessions
                WHERE channel_id = ? AND join_time >= ? AND join_time < ?
                GROUP BY user_id
                ORDER BY MIN(join_time) ASC
            """, (self.VC_REPORT_TARGET_ID, start_time_utc_iso, end_time_utc_iso))
            join_records = cursor.fetchall()
        except sqlite3.Error as e:
            print(f"レポート生成中のDBエラー: {e}")
            return
        
        if not join_records: return

        report_lines = [
            f"`{datetime.datetime.fromisoformat(join_time_iso).astimezone(self.JST).strftime('%H:%M'):>5}` - {(guild.get_member(user_id) or f'ID: {user_id}').display_name}"
            for user_id, join_time_iso in join_records
        ]
        start_time_jst = start_time_utc.astimezone(self.JST)
        embed = discord.Embed(
            title=f"'{target_vc.name}' 入室レポート",
            description="\n".join(report_lines),
            color=0x3498DB
        ).set_footer(text=f"通話開始時刻: {start_time_jst.strftime('%Y/%m/%d %H:%M')}")

        try:
            await destination_thread.send(embed=embed)
        except discord.Forbidden:
            print(f"エラー: スレッド '{destination_thread.name}' への投稿権限がありません。")
        except Exception as e:
            print(f"レポートの送信中に予期せぬエラーが発生しました: {e}")


    async def send_call_start_notification(self, member: discord.Member, channel: discord.VoiceChannel, start_time_utc: datetime.datetime):
        """★変更: guild_id を元に通知先チャンネルを取得"""
        recruit_channel_id = self._get_notification_channel_id(member.guild.id)
        if not recruit_channel_id: return
        recruit_channel = self.bot.get_channel(recruit_channel_id)
        if not recruit_channel: return
        
        start_time_jst = start_time_utc.astimezone(self.JST)
        embed = discord.Embed(title="**通話開始**", color=0x5865F2)
        embed.add_field(name="チャンネル", value=channel.mention, inline=True)
        embed.add_field(name="始めた人", value=member.display_name, inline=True)
        embed.add_field(name="開始時間", value=start_time_jst.strftime("%H:%M"), inline=True)
        embed.set_thumbnail(url=member.display_avatar.url)
        await recruit_channel.send("@everyone", embed=embed, allowed_mentions=discord.AllowedMentions(everyone=True))

    async def send_call_end_notification(self, channel: discord.VoiceChannel, duration: datetime.timedelta, guild: discord.Guild):
        """★変更: guild を引数で受け取り、通知先チャンネルを取得"""
        recruit_channel_id = self._get_notification_channel_id(guild.id)
        if not recruit_channel_id: return
        recruit_channel = self.bot.get_channel(recruit_channel_id)
        if not recruit_channel: return

        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{hours:02}:{minutes:02}:{seconds:02}"
        embed = discord.Embed(title="通話終了", color=0xE74C3C)
        embed.add_field(name="チャンネル", value=channel.name, inline=True)
        embed.add_field(name="通話時間", value=duration_str, inline=True)
        await recruit_channel.send(embed=embed)

    def _start_user_session(self, member, channel, join_time_utc):
        try:
            self.db_conn.cursor().execute("INSERT INTO voice_sessions (guild_id, channel_id, user_id, join_time) VALUES (?, ?, ?, ?)",(member.guild.id, channel.id, member.id, join_time_utc.isoformat()))
            self.db_conn.commit()
        except sqlite3.Error as e: print(f"セッション開始時のDBエラー: {e}")

    def _end_user_session(self, member, channel, leave_time_utc):
        try:
            self.db_conn.cursor().execute("UPDATE voice_sessions SET leave_time = ? WHERE id = (SELECT id FROM voice_sessions WHERE user_id = ? AND channel_id = ? AND leave_time IS NULL ORDER BY join_time DESC LIMIT 1)", (leave_time_utc.isoformat(), member.id, channel.id))
            self.db_conn.commit()
        except sqlite3.Error as e: print(f"セッション終了時のDBエラー: {e}")

    def is_sub_account_in_vc(self, main_user_id: int, users_in_vc: set) -> bool:
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT sub_user_id FROM sub_accounts WHERE main_user_id = ?", (main_user_id,))
            sub_account_ids = {row[0] for row in cursor.fetchall()}
            return not sub_account_ids.isdisjoint(users_in_vc)
        except sqlite3.Error as e:
            print(f"サブアカウントのVC状況チェック中にDBエラー: {e}")
            return False

    async def get_reacted_users_from_attendance(self, guild: discord.Guild, since_utc: datetime.datetime) -> set | None:
        attendance_channel = guild.get_channel(self.ATTENDANCE_CHANNEL_ID)
        if not attendance_channel:
            print(f"エラー: 出欠チャンネル(ID: {self.ATTENDANCE_CHANNEL_ID})が見つかりません。")
            return None
        reacted_user_ids = set()
        try:
            async for message in attendance_channel.history(limit=None, after=since_utc):
                if message.reactions:
                    reacted_user_ids.add(message.author.id)
            return reacted_user_ids
        except discord.Forbidden:
            print(f"エラー: チャンネル '{attendance_channel.name}' の履歴を読む権限がありません。")
            return None

    async def _get_mention_targets(self, guild: discord.Guild) -> list[discord.Member]:
        target_role = discord.utils.get(guild.roles, name=self.TARGET_ROLE_NAME)
        if not target_role:
            print(f"エラー: ロール '{self.TARGET_ROLE_NAME}' が見つかりません。")
            return []
        now_jst = datetime.datetime.now(self.JST)
        today_weekday = now_jst.weekday()
        days_since_sunday = (today_weekday + 1) % 7
        last_sunday_date = now_jst.date() - datetime.timedelta(days=days_since_sunday)
        since_jst = datetime.datetime.combine(last_sunday_date, datetime.time(5, 0), tzinfo=self.JST)
        since_utc = since_jst.astimezone(datetime.timezone.utc)
        users_in_vc = {member.id for channel in guild.voice_channels for member in channel.members}
        reacted_user_ids = await self.get_reacted_users_from_attendance(guild, since_utc)
        if reacted_user_ids is None:
            return []
        members_to_mention = []
        for member in target_role.members:
            if member.bot: continue
            is_in_vc = member.id in users_in_vc
            has_reacted = member.id in reacted_user_ids
            sub_in_vc = self.is_sub_account_in_vc(member.id, users_in_vc)
            if not is_in_vc and not has_reacted and not sub_in_vc:
                members_to_mention.append(member)
        return members_to_mention

    @tasks.loop(time=MENTION_TIME_UTC)
    async def weekly_mention_task(self):
        # ★追加: スキップフラグの確認
        if self.skip_weekly_mention:
            now_jst_str = datetime.datetime.now(self.JST).strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{now_jst_str}] 定例通知は今週スキップするよう設定されています。")
            alert_channel = self.bot.get_channel(self.ALERT_CHANNEL_ID)
            if alert_channel:
                try:
                    await alert_channel.send("管理者によって設定されたため、今週の定例通知はスキップされました。")
                except discord.Forbidden:
                    print(f"エラー: チャンネル '{alert_channel.name}' への投稿権限がありません。")
            # 来週のためにフラグをリセット
            self.skip_weekly_mention = False
            return # タスクの実行をここで終了

        if datetime.datetime.now(datetime.timezone.utc).weekday() != 5: return

        now_jst = datetime.datetime.now(self.JST)
        print(f"[{now_jst.strftime('%Y-%m-%d %H:%M:%S')}] 定例時間チェックを実行します。")
        alert_channel = self.bot.get_channel(self.ALERT_CHANNEL_ID)
        if not alert_channel:
            print(f"エラー: メンション先のチャンネル(ID: {self.ALERT_CHANNEL_ID})が見つかりません。")
            return

        members_to_mention = await self._get_mention_targets(alert_channel.guild)
        
        if members_to_mention:
            mention_str = ' '.join([m.mention for m in members_to_mention])
            message = f"{mention_str}\n\n**定例の時間です！**\nボイスチャンネルに集合してください。"
            await alert_channel.send(message)
        else:
            print("メンション対象のユーザーはいませんでした。")

    @weekly_mention_task.before_loop
    async def before_weekly_mention_task(self):
        await self.bot.wait_until_ready()

    # --- ★新規追加: 定例通知のスキップ関連コマンド ---
    @commands.command(name='定例通知スキップ', help='今週の定例通知を一度だけスキップします。')
    @commands.has_permissions(administrator=True)
    async def skip_mention(self, ctx: commands.Context):
        self.skip_weekly_mention = True
        embed = discord.Embed(
            title="✅ 設定完了",
            description="今週の定例通知はスキップされます。\nこの設定は一度通知時間を過ぎると自動的にリセットされます。",
            color=0xF39C12 # Orange color
        )
        await ctx.send(embed=embed)

    @commands.command(name='定例通知確認', help='今週の定例通知がスキップされるかどうかを確認します。')
    @commands.has_permissions(administrator=True)
    async def check_mention_status(self, ctx: commands.Context):
        if self.skip_weekly_mention:
            description = "次回の定例通知は実行されません。"
            color = 0xF39C12 # Orange
        else:
            description = "次回の定例通知は通常通り実行されます。"
            color = 0x2ECC71 # Green
        
        embed = discord.Embed(
            title="定例通知ステータス",
            description=description,
            color=color
        )
        await ctx.send(embed=embed)
        
    @skip_mention.error
    @check_mention_status.error
    async def mention_control_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドの実行には管理者権限が必要です。")
        else:
            await ctx.send(f"コマンドの実行中にエラーが発生しました: {error}")


    @commands.command(name='メンション対象確認', help='次回の定例でメンションされる対象者の一覧を事前に確認します。')
    @commands.has_permissions(administrator=True)
    async def check_mention_targets(self, ctx: commands.Context):
        await ctx.defer()
        members_to_mention = await self._get_mention_targets(ctx.guild)
        if not members_to_mention:
            embed = discord.Embed(title="✅ メンション対象者なし", description="現在、メンション対象となるユーザーはいません。", color=0x2ECC71)
            return await ctx.send(embed=embed)
        embed = discord.Embed(title="🚨 メンション対象者リスト", description="以下の方々は、現在VCに参加しておらず、直近の出欠連絡も確認できていないため、次回の定例でメンションされます。", color=0xE67E22)
        member_names = [f"・{member.display_name}" for member in members_to_mention]
        embed.add_field(name="対象者", value="\n".join(member_names), inline=False)
        now_jst = datetime.datetime.now(self.JST)
        days_since_sunday = (now_jst.weekday() + 1) % 7
        last_sunday_date = now_jst.date() - datetime.timedelta(days=days_since_sunday)
        since_jst = datetime.datetime.combine(last_sunday_date, datetime.time(5, 0), tzinfo=self.JST)
        embed.set_footer(text=f"※出欠確認の対象期間: {since_jst.strftime('%Y/%m/%d %H:%M')} 以降")
        await ctx.send(embed=embed)

    @check_mention_targets.error
    async def check_mention_targets_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドの実行には管理者権限が必要です。")
        else:
            await ctx.send(f"コマンドの実行中にエラーが発生しました: {error}")

    # --- ★新規追加: サーバーごとの設定用コマンド ---
    @commands.command(name='通話通知設定', help='通話の開始・終了を通知するチャンネルを設定します。\n例: !通話通知設定 #通話ログ')
    @commands.has_permissions(administrator=True)
    async def set_notification_channel(self, ctx, channel: discord.TextChannel):
        try:
            cursor = self.db_conn.cursor()
            # INSERT OR REPLACEで、存在しない場合は新規作成、存在する場合は更新
            cursor.execute("INSERT OR REPLACE INTO guild_settings (guild_id, call_notification_channel_id) VALUES (?, ?)", (ctx.guild.id, channel.id))
            self.db_conn.commit()
            embed = discord.Embed(
                title="✅ 設定完了",
                description=f"今後、このサーバーでの通話通知は {channel.mention} に送信されます。",
                color=0x2ECC71
            )
            await ctx.send(embed=embed)
        except sqlite3.Error as e:
            await ctx.send(f"データベースエラーが発生しました: {e}")

    @commands.command(name='設定確認', help='このサーバーの現在の各種設定を確認します。')
    @commands.has_permissions(administrator=True)
    async def show_settings(self, ctx):
        notification_channel_id = self._get_notification_channel_id(ctx.guild.id)
        
        if notification_channel_id:
            channel = self.bot.get_channel(notification_channel_id)
            channel_mention = channel.mention if channel else f"ID: `{notification_channel_id}` (チャンネルが見つかりません)"
        else:
            channel_mention = "設定されていません"

        embed = discord.Embed(title=f"'{ctx.guild.name}' の設定", color=0x3498DB)
        embed.add_field(name="通話通知チャンネル", value=channel_mention, inline=False)
        # 他にも表示したい設定があればここに追加
        
        await ctx.send(embed=embed)
        
    @set_notification_channel.error
    @show_settings.error
    async def settings_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドの実行には管理者権限が必要です。")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send("チャンネルを指定してください。例: `#チャンネル名`")
        elif isinstance(error, commands.ChannelNotFound):
            await ctx.send("指定されたチャンネルが見つかりませんでした。")
        else:
            print(f"設定コマンドでエラー: {error}")
            await ctx.send(f"コマンドの実行中にエラーが発生しました。")


    @commands.command(name='サブ垢登録', help='ユーザーIDを使ってメインアカウントにサブアカウントを紐付けます。\n例: !サブ垢登録 <メインのID> <サブのID>')
    @commands.has_permissions(administrator=True)
    async def register_sub_account(self, ctx, main_id: int, sub_id: int):
        try:
            main_account = await self.bot.fetch_user(main_id)
            sub_account = await self.bot.fetch_user(sub_id)
        except discord.NotFound:
            return await ctx.send("指定されたIDのユーザーが見つかりませんでした。IDが正しいか確認してください。")
        if main_account.bot or sub_account.bot:
            return await ctx.send("ボットをアカウントとして登録することはできません。")
        if main_account.id == sub_account.id:
            return await ctx.send("自分自身をサブアカウントとして登録することはできません。")
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("INSERT OR REPLACE INTO sub_accounts (main_user_id, sub_user_id) VALUES (?, ?)", (main_account.id, sub_account.id))
            self.db_conn.commit()
            embed = discord.Embed(title="✅ サブアカウント登録完了", description="以下の通り登録しました。", color=0x2ECC71)
            embed.add_field(name="メインアカウント", value=f"`{str(main_account)}`", inline=False)
            embed.add_field(name="サブアカウント", value=f"`{str(sub_account)}`", inline=False)
            await ctx.send(embed=embed)
        except sqlite3.Error as e:
            await ctx.send(f"データベースエラーが発生しました: {e}")

    @commands.command(name='サブ垢削除', help='サブアカウントのユーザーIDを使って紐付けを解除します。')
    @commands.has_permissions(administrator=True)
    async def remove_sub_account(self, ctx, sub_id: int):
        try:
            user_to_remove = await self.bot.fetch_user(sub_id)
            user_name = f"`{str(user_to_remove)}`"
        except discord.NotFound:
            user_name = f"ID `{sub_id}`"
        try:
            cursor = self.db_conn.cursor()
            initial_changes = self.db_conn.total_changes
            cursor.execute("DELETE FROM sub_accounts WHERE sub_user_id = ?", (sub_id,))
            self.db_conn.commit()
            if self.db_conn.total_changes > initial_changes:
                 await ctx.send(f"✅ {user_name} のサブアカウント登録を解除しました。")
            else:
                 await ctx.send(f"ℹ️ {user_name} はサブアカウントとして登録されていませんでした。")
        except sqlite3.Error as e:
            await ctx.send(f"データベースエラーが発生しました: {e}")

    @commands.command(name='サブ垢一覧', help='登録されているサブアカウントの一覧を表示します。')
    async def list_sub_accounts(self, ctx):
        try:
            cursor = self.db_conn.cursor()
            cursor.execute("SELECT main_user_id, sub_user_id FROM sub_accounts ORDER BY main_user_id")
            all_subs = cursor.fetchall()
            if not all_subs:
                return await ctx.send("サブアカウントは一件も登録されていません。")
            embed = discord.Embed(title="サブアカウント登録一覧", color=0x3498DB)
            description_lines = []
            for main_id, sub_id in all_subs:
                main_member = ctx.guild.get_member(main_id)
                sub_member = ctx.guild.get_member(sub_id)
                main_name = main_member.display_name if main_member else (await self.bot.fetch_user(main_id)).name
                sub_name = sub_member.display_name if sub_member else (await self.bot.fetch_user(sub_id)).name
                description_lines.append(f"**{main_name}** → {sub_name}")
            embed.description = "\n".join(description_lines)
            await ctx.send(embed=embed)
        except sqlite3.Error as e:
            await ctx.send(f"データベースエラーが発生しました: {e}")
        except discord.NotFound:
            await ctx.send("一覧の作成中に、登録されているIDのユーザーが見つかりませんでした。削除されたアカウントが登録されている可能性があります。")

    @register_sub_account.error
    @remove_sub_account.error
    async def sub_account_command_error(self, ctx, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("このコマンドの実行には管理者権限が必要です。")
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(f"引数が不足しています。\nコマンドの使い方は `{self.bot.command_prefix}help {ctx.command.name}` で確認してください。")
        elif isinstance(error, commands.BadArgument):
            await ctx.send("引数には有効なユーザーID（数字）を入力してください。")
        elif isinstance(error, commands.CommandInvokeError) and isinstance(error.original, discord.NotFound):
            await ctx.send(f"指定されたIDのユーザーが見つかりませんでした。IDが正しいか確認してください。")
        else:
            print(f"サブアカウントコマンドでエラーが発生しました: {error}")

async def setup(bot):
    """Cogをボットに登録するためのセットアップ関数"""
    try:
        await bot.add_cog(VoiceLoggerCog(bot))
    except ValueError as e:
        print(f"エラー: VoiceLoggerCogの読み込みに失敗しました。理由: {e}")
