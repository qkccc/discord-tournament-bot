# cogs/audio/voice_logger.py
import discord
from discord.ext import commands, tasks
import datetime
import os
from dotenv import load_dotenv
from cogs.utils.db_handler import db

load_dotenv()

class VoiceLoggerCog(commands.Cog):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    MENTION_TIME_UTC = datetime.time(hour=13, minute=5, tzinfo=datetime.timezone.utc)

    def __init__(self, bot):
        self.bot = bot
        self.TARGET_ROLE_NAME = os.getenv("TARGET_ROLE_NAME")
        self.ALERT_CHANNEL_ID = self._get_env_var_as_int("ALERT_CHANNEL_ID")
        self.ATTENDANCE_CHANNEL_ID = self._get_env_var_as_int("ATTENDANCE_CHANNEL_ID")
        self.VC_REPORT_TARGET_ID = self._get_env_var_as_int("VC_REPORT_TARGET_ID")
        self.VC_REPORT_DESTINATION_ID = self._get_env_var_as_int("VC_REPORT_DESTINATION_ID")

        if not all([self.TARGET_ROLE_NAME, self.ALERT_CHANNEL_ID, self.ATTENDANCE_CHANNEL_ID]):
            raise ValueError("必要な設定（ロール名やチャンネルID）が.envファイルに設定されていません。")

        self.active_vc_sessions = {}
        self.report_vc_start_time = None
        self.skip_weekly_mention = False
        self.weekly_mention_task.start()

    async def cog_load(self):
        """Cogロード時にデータベースを初期化する"""
        await self._setup_database()
        await self._cleanup_stale_sessions()
        await self._recover_report_vc_state()

    def _get_env_var_as_int(self, var_name: str) -> int | None:
        val_str = os.getenv(var_name)
        return int(val_str) if val_str and val_str.isdigit() else None

    async def _setup_database(self):
        """テーブル作成（非同期）"""
        await db.execute("""
            CREATE TABLE IF NOT EXISTS voice_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id INTEGER NOT NULL,
                channel_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                join_time TEXT NOT NULL,
                leave_time TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_sub_accounts (
                main_user_id INTEGER NOT NULL,
                sub_user_id INTEGER NOT NULL PRIMARY KEY
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS voice_guild_settings (
                guild_id INTEGER PRIMARY KEY,
                call_notification_channel_id INTEGER
            )
        """)

    async def _recover_report_vc_state(self):
        if not self.VC_REPORT_TARGET_ID: return
        target_vc = self.bot.get_channel(self.VC_REPORT_TARGET_ID)
        if target_vc and target_vc.members:
            member_ids = tuple(m.id for m in target_vc.members)
            placeholders = ','.join('?' for _ in member_ids)
            query = f"SELECT MIN(join_time) FROM voice_sessions WHERE channel_id = ? AND user_id IN ({placeholders}) AND leave_time IS NULL"
            result = await db.fetchone(query, (self.VC_REPORT_TARGET_ID, *member_ids))
            if result and result[0]:
                self.report_vc_start_time = datetime.datetime.fromisoformat(result[0])
                print(f"復元された通話開始時刻(UTC): {self.report_vc_start_time}")

    async def _cleanup_stale_sessions(self):
        stale_sessions = await db.fetchall("SELECT id, guild_id, user_id, channel_id FROM voice_sessions WHERE leave_time IS NULL")
        now_utc_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        sessions_to_close = []
        for row in stale_sessions:
            guild = self.bot.get_guild(row['guild_id'])
            if not guild:
                sessions_to_close.append((now_utc_iso, row['id']))
                continue
            member = guild.get_member(row['user_id'])
            if not member or not member.voice or member.voice.channel.id != row['channel_id']:
                sessions_to_close.append((now_utc_iso, row['id']))
        
        if sessions_to_close:
            await db.executemany("UPDATE voice_sessions SET leave_time = ? WHERE id = ?", sessions_to_close)
            print(f"{len(sessions_to_close)}件の古いセッションをクリーンアップしました。")

    def cog_unload(self):
        self.weekly_mention_task.cancel()

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        if member.bot: return
        now_utc = datetime.datetime.now(datetime.timezone.utc)
        
        # 退出処理
        if before.channel and before.channel != after.channel:
            await self._end_user_session(member, before.channel, now_utc)
            if not before.channel.members:
                start_time_utc = self.active_vc_sessions.pop(before.channel.id, None)
                if start_time_utc:
                    duration = now_utc - start_time_utc
                    # 通話終了ログは、レポート対象チャンネルの場合は出さないままにする（レポートと重複してうるさいため）
                    # もし終了ログも必要なら、ここのif文も外してください
                    if before.channel.id != self.VC_REPORT_TARGET_ID:
                        await self.send_call_end_notification(before.channel, duration, member.guild)
        
        # 入室処理
        if after.channel and after.channel != before.channel:
            if len(after.channel.members) == 1:
                # 【修正箇所】以前はここで「レポート対象チャンネルなら通知しない」判定がありましたが削除しました。
                # これにより、どのチャンネルでも最初の1人が入れば通知が飛びます。
                await self.send_call_start_notification(member, after.channel, now_utc)
                self.active_vc_sessions[after.channel.id] = now_utc
            
            await self._start_user_session(member, after.channel, now_utc)

        # レポート生成用の追跡ロジック（変更なし）
        if self.VC_REPORT_TARGET_ID:
            if after.channel and after.channel.id == self.VC_REPORT_TARGET_ID:
                if len(after.channel.members) == 1 and not self.report_vc_start_time:
                    self.report_vc_start_time = now_utc
            
            if before.channel and before.channel.id == self.VC_REPORT_TARGET_ID:
                if not before.channel.members and self.report_vc_start_time:
                    await self._generate_and_send_vc_report(guild=before.channel.guild, start_time_utc=self.report_vc_start_time, end_time_utc=now_utc)
                    self.report_vc_start_time = None

    async def _get_notification_channel_id(self, guild_id: int) -> int | None:
        result = await db.fetchone("SELECT call_notification_channel_id FROM voice_guild_settings WHERE guild_id = ?", (guild_id,))
        return result['call_notification_channel_id'] if result else None

    async def _generate_and_send_vc_report(self, guild: discord.Guild, start_time_utc: datetime.datetime, end_time_utc: datetime.datetime):
        if not self.VC_REPORT_DESTINATION_ID: return
        destination_thread = self.bot.get_channel(self.VC_REPORT_DESTINATION_ID)
        if not destination_thread: return
        target_vc = self.bot.get_channel(self.VC_REPORT_TARGET_ID)
        if not target_vc: return

        join_records = await db.fetchall("""
            SELECT user_id, MIN(join_time) as join_time
            FROM voice_sessions
            WHERE channel_id = ? AND join_time >= ? AND join_time < ?
            GROUP BY user_id ORDER BY MIN(join_time) ASC
        """, (self.VC_REPORT_TARGET_ID, start_time_utc.isoformat(), end_time_utc.isoformat()))
        
        if not join_records: return

        report_lines = [
            f"`{datetime.datetime.fromisoformat(row['join_time']).astimezone(self.JST).strftime('%H:%M'):>5}` - {(guild.get_member(row['user_id']) or f'ID: {row['user_id']}').display_name}"
            for row in join_records
        ]
        embed = discord.Embed(title=f"'{target_vc.name}' 入室レポート", description="\n".join(report_lines), color=0x3498DB)
        embed.set_footer(text=f"通話開始時刻: {start_time_utc.astimezone(self.JST).strftime('%Y/%m/%d %H:%M')}")
        await destination_thread.send(embed=embed)

    async def send_call_start_notification(self, member: discord.Member, channel: discord.VoiceChannel, start_time_utc: datetime.datetime):
        recruit_channel_id = await self._get_notification_channel_id(member.guild.id)
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
        recruit_channel_id = await self._get_notification_channel_id(guild.id)
        if not recruit_channel_id: return
        recruit_channel = self.bot.get_channel(recruit_channel_id)
        if not recruit_channel: return

        total_seconds = int(duration.total_seconds())
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        embed = discord.Embed(title="通話終了", color=0xE74C3C)
        embed.add_field(name="チャンネル", value=channel.name, inline=True)
        embed.add_field(name="通話時間", value=f"{hours:02}:{minutes:02}:{seconds:02}", inline=True)
        await recruit_channel.send(embed=embed)

    async def _start_user_session(self, member, channel, join_time_utc):
        await db.execute("INSERT INTO voice_sessions (guild_id, channel_id, user_id, join_time) VALUES (?, ?, ?, ?)",(member.guild.id, channel.id, member.id, join_time_utc.isoformat()))

    async def _end_user_session(self, member, channel, leave_time_utc):
        # 最新の未終了セッションを閉じる
        sub_query = "SELECT id FROM voice_sessions WHERE user_id = ? AND channel_id = ? AND leave_time IS NULL ORDER BY join_time DESC LIMIT 1"
        target_session = await db.fetchone(sub_query, (member.id, channel.id))
        if target_session:
            await db.execute("UPDATE voice_sessions SET leave_time = ? WHERE id = ?", (leave_time_utc.isoformat(), target_session['id']))

    async def is_sub_account_in_vc(self, main_user_id: int, users_in_vc: set) -> bool:
        rows = await db.fetchall("SELECT sub_user_id FROM user_sub_accounts WHERE main_user_id = ?", (main_user_id,))
        sub_account_ids = {row['sub_user_id'] for row in rows}
        return not sub_account_ids.isdisjoint(users_in_vc)

    async def get_reacted_users_from_attendance(self, guild: discord.Guild, since_utc: datetime.datetime) -> set | None:
        attendance_channel = guild.get_channel(self.ATTENDANCE_CHANNEL_ID)
        if not attendance_channel: return None
        reacted_user_ids = set()
        try:
            async for message in attendance_channel.history(limit=None, after=since_utc):
                if message.reactions: reacted_user_ids.add(message.author.id)
            return reacted_user_ids
        except discord.Forbidden: return None

    async def _get_mention_targets(self, guild: discord.Guild) -> list[discord.Member]:
        target_role = discord.utils.get(guild.roles, name=self.TARGET_ROLE_NAME)
        if not target_role: return []
        now_jst = datetime.datetime.now(self.JST)
        days_since_sunday = (now_jst.weekday() + 1) % 7
        last_sunday_date = now_jst.date() - datetime.timedelta(days=days_since_sunday)
        since_utc = datetime.datetime.combine(last_sunday_date, datetime.time(5, 0), tzinfo=self.JST).astimezone(datetime.timezone.utc)
        users_in_vc = {member.id for channel in guild.voice_channels for member in channel.members}
        reacted_user_ids = await self.get_reacted_users_from_attendance(guild, since_utc)
        if reacted_user_ids is None: return []
        
        members_to_mention = []
        for member in target_role.members:
            if member.bot: continue
            if member.id not in users_in_vc and member.id not in reacted_user_ids:
                if not await self.is_sub_account_in_vc(member.id, users_in_vc):
                    members_to_mention.append(member)
        return members_to_mention

    @tasks.loop(time=MENTION_TIME_UTC)
    async def weekly_mention_task(self):
        if self.skip_weekly_mention:
            alert_channel = self.bot.get_channel(self.ALERT_CHANNEL_ID)
            if alert_channel: await alert_channel.send("今週の定例通知はスキップされました。")
            self.skip_weekly_mention = False
            return

        if datetime.datetime.now(datetime.timezone.utc).weekday() != 5: return
        alert_channel = self.bot.get_channel(self.ALERT_CHANNEL_ID)
        if not alert_channel: return
        members = await self._get_mention_targets(alert_channel.guild)
        if members:
            await alert_channel.send(f"{' '.join([m.mention for m in members])}\n\n**定例の時間です！**\nボイスチャンネルに集合してください。")

    @weekly_mention_task.before_loop
    async def before_weekly_mention_task(self):
        await self.bot.wait_until_ready()

    # --- コマンド類 ---
    @commands.command(name='定例通知スキップ')
    @commands.has_permissions(administrator=True)
    async def skip_mention(self, ctx):
        self.skip_weekly_mention = True
        await ctx.send(embed=discord.Embed(title="✅ 設定完了", description="今週の定例通知はスキップされます。", color=0xF39C12))

    @commands.command(name='定例通知確認')
    @commands.has_permissions(administrator=True)
    async def check_mention_status(self, ctx):
        status = "スキップされます" if self.skip_weekly_mention else "実行されます"
        color = 0xF39C12 if self.skip_weekly_mention else 0x2ECC71
        await ctx.send(embed=discord.Embed(title="定例通知ステータス", description=f"次回の通知は{status}。", color=color))

    @commands.command(name='メンション対象確認')
    @commands.has_permissions(administrator=True)
    async def check_mention_targets(self, ctx):
        await ctx.defer()
        members = await self._get_mention_targets(ctx.guild)
        if not members: return await ctx.send(embed=discord.Embed(title="✅ 対象者なし", color=0x2ECC71))
        await ctx.send(embed=discord.Embed(title="🚨 対象者リスト", description="\n".join([f"・{m.display_name}" for m in members]), color=0xE67E22))

    @commands.command(name='通話通知設定')
    @commands.has_permissions(administrator=True)
    async def set_notification_channel(self, ctx, channel: discord.TextChannel):
        await db.execute("INSERT OR REPLACE INTO voice_guild_settings (guild_id, call_notification_channel_id) VALUES (?, ?)", (ctx.guild.id, channel.id))
        await ctx.send(embed=discord.Embed(title="✅ 設定完了", description=f"通知先を {channel.mention} に設定しました。", color=0x2ECC71))

    @commands.command(name='設定確認')
    @commands.has_permissions(administrator=True)
    async def show_settings(self, ctx):
        ch_id = await self._get_notification_channel_id(ctx.guild.id)
        ch = self.bot.get_channel(ch_id) if ch_id else None
        await ctx.send(embed=discord.Embed(title=f"'{ctx.guild.name}' の設定", description=f"通話通知: {ch.mention if ch else '未設定'}", color=0x3498DB))

    @commands.command(name='サブ垢登録')
    @commands.has_permissions(administrator=True)
    async def register_sub_account(self, ctx, main_id: int, sub_id: int):
        await db.execute("INSERT OR REPLACE INTO user_sub_accounts (main_user_id, sub_user_id) VALUES (?, ?)", (main_id, sub_id))
        await ctx.send(embed=discord.Embed(title="✅ 登録完了", description=f"メイン: `{main_id}`\nサブ: `{sub_id}`", color=0x2ECC71))

    @commands.command(name='サブ垢削除')
    @commands.has_permissions(administrator=True)
    async def remove_sub_account(self, ctx, sub_id: int):
        cursor = await db.execute("DELETE FROM user_sub_accounts WHERE sub_user_id = ?", (sub_id,))
        msg = "解除しました" if cursor.rowcount > 0 else "登録されていませんでした"
        await ctx.send(f"✅ {msg}")

    @commands.command(name='サブ垢一覧')
    async def list_sub_accounts(self, ctx):
        rows = await db.fetchall("SELECT main_user_id, sub_user_id FROM user_sub_accounts")
        if not rows: return await ctx.send("登録なし")
        lines = [f"{r['main_user_id']} → {r['sub_user_id']}" for r in rows]
        await ctx.send(embed=discord.Embed(title="サブアカウント一覧", description="\n".join(lines), color=0x3498DB))

async def setup(bot):
    await bot.add_cog(VoiceLoggerCog(bot))