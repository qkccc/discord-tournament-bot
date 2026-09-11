# cogs/audio/voice_logger.py
import discord
from discord.ext import commands, tasks
import datetime
import os
from dotenv import load_dotenv
from cogs.utils.db_handler import db

load_dotenv()

# ステータス種別: vc=VC参加, excused=欠席理由が承認済み, absent=どちらもなし（通知対象）
STATUS_LABELS = {"vc": "🎤 VC参加", "excused": "📝 欠席理由承認", "absent": "❌ 未対応"}

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
        await db.execute("""
            CREATE TABLE IF NOT EXISTS attendance_weekly (
                guild_id INTEGER NOT NULL,
                week_start TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                status TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                PRIMARY KEY (guild_id, week_start, user_id)
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

        report_lines = []
        for row in join_records:
            # 時間をフォーマット
            join_time_str = datetime.datetime.fromisoformat(row['join_time']).astimezone(self.JST).strftime('%H:%M')
            
            # メンバー名を取得（いなければID表示）
            member = guild.get_member(row['user_id'])
            if member:
                display_name = member.display_name
            else:
                display_name = f"ID: {row['user_id']}"
            
            # 行を作成
            report_lines.append(f"`{join_time_str:>5}` - {display_name}")
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
        """出欠チャンネルで、欠席理由の投稿に承認リアクションが付いた投稿者を「対応済み」として集める。"""
        attendance_channel = guild.get_channel(self.ATTENDANCE_CHANNEL_ID)
        if not attendance_channel: return None
        reacted_user_ids = set()
        try:
            async for message in attendance_channel.history(limit=None, after=since_utc):
                if message.reactions: reacted_user_ids.add(message.author.id)
            return reacted_user_ids
        except discord.Forbidden: return None

    async def _compute_weekly_attendance(
        self, guild: discord.Guild
    ) -> tuple[list[discord.Member], dict[int, str], datetime.date] | None:
        """対象ロールメンバーの今週の出欠を判定する。

        戻り値: (未対応メンバー一覧, {user_id: ステータス}, 集計週の開始日(JST, 日曜始まり))
        ステータスは "vc"（VC参加）/ "excused"（欠席理由が承認済み）/ "absent"（どちらもなし）。
        """
        target_role = discord.utils.get(guild.roles, name=self.TARGET_ROLE_NAME)
        if not target_role:
            return None
        now_jst = datetime.datetime.now(self.JST)
        days_since_sunday = (now_jst.weekday() + 1) % 7
        week_start_date = now_jst.date() - datetime.timedelta(days=days_since_sunday)
        since_utc = datetime.datetime.combine(week_start_date, datetime.time(5, 0), tzinfo=self.JST).astimezone(datetime.timezone.utc)
        users_in_vc = {member.id for channel in guild.voice_channels for member in channel.members}
        reacted_user_ids = await self.get_reacted_users_from_attendance(guild, since_utc)
        if reacted_user_ids is None:
            return None

        members_to_mention = []
        attendance: dict[int, str] = {}
        for member in target_role.members:
            if member.bot:
                continue

            # サブ垢ID一覧を取得
            rows = await db.fetchall("SELECT sub_user_id FROM user_sub_accounts WHERE main_user_id = ?", (member.id,))
            sub_account_ids = {row['sub_user_id'] for row in rows}

            # 本人またはサブ垢がVCにいればVC参加、いなくても欠席理由が承認済みなら excused
            if member.id in users_in_vc or not sub_account_ids.isdisjoint(users_in_vc):
                status = "vc"
            elif member.id in reacted_user_ids or not sub_account_ids.isdisjoint(reacted_user_ids):
                status = "excused"
            else:
                status = "absent"

            attendance[member.id] = status
            if status == "absent":
                members_to_mention.append(member)

        return members_to_mention, attendance, week_start_date

    async def _get_mention_targets(self, guild: discord.Guild) -> list[discord.Member]:
        result = await self._compute_weekly_attendance(guild)
        if result is None:
            return []
        members_to_mention, _, _ = result
        return members_to_mention

    async def _save_weekly_attendance(
        self, guild_id: int, week_start_date: datetime.date, attendance: dict[int, str]
    ):
        if not attendance:
            return
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = [
            (guild_id, week_start_date.isoformat(), user_id, status, now_iso)
            for user_id, status in attendance.items()
        ]
        await db.executemany(
            "INSERT OR REPLACE INTO attendance_weekly "
            "(guild_id, week_start, user_id, status, recorded_at) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    @tasks.loop(time=MENTION_TIME_UTC)
    async def weekly_mention_task(self):
        # 曜日判定を先に行う。スキップ判定をこれより前に置くと、土曜日以外の
        # 実行（このループは毎日13:05 UTCに走る）でフラグが先に消費されてしまい、
        # 肝心の土曜日にスキップが反映されないバグがあったため順序を修正。
        if datetime.datetime.now(datetime.timezone.utc).weekday() != 5: return
        alert_channel = self.bot.get_channel(self.ALERT_CHANNEL_ID)
        if not alert_channel: return

        if self.skip_weekly_mention:
            await alert_channel.send("今週の定例通知はスキップされました。")
            self.skip_weekly_mention = False
            return

        result = await self._compute_weekly_attendance(alert_channel.guild)
        if result is None: return
        members, attendance, week_start_date = result
        await self._save_weekly_attendance(alert_channel.guild.id, week_start_date, attendance)

        if members:
            mentions_str = " ".join([m.mention for m in members])
            await alert_channel.send(f"{mentions_str}\n\n**定例の時間です！**\nボイスチャンネルに集合してください。")

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

    @commands.command(name='出欠履歴')
    @commands.has_permissions(administrator=True)
    async def show_attendance_history(self, ctx, member: discord.Member = None, weeks: int = 8):
        member = member or ctx.author
        weeks = max(1, min(weeks, 52))
        rows = await db.fetchall(
            "SELECT week_start, status FROM attendance_weekly "
            "WHERE guild_id = ? AND user_id = ? ORDER BY week_start DESC LIMIT ?",
            (ctx.guild.id, member.id, weeks),
        )
        if not rows:
            return await ctx.send(embed=discord.Embed(
                title="出欠履歴", description=f"{member.display_name} の記録はまだありません。", color=0x95A5A6
            ))
        lines = [f"`{r['week_start']}` {STATUS_LABELS.get(r['status'], r['status'])}" for r in rows]
        handled_count = sum(1 for r in rows if r['status'] != 'absent')
        embed = discord.Embed(
            title=f"{member.display_name} の出欠履歴（直近{len(rows)}週）",
            description="\n".join(lines),
            color=0x3498DB,
        )
        embed.set_footer(text=f"対応率: {handled_count}/{len(rows)} ({handled_count / len(rows) * 100:.0f}%)")
        await ctx.send(embed=embed)

    @commands.command(name='出欠率')
    @commands.has_permissions(administrator=True)
    async def show_attendance_rate(self, ctx, weeks: int = 8):
        weeks = max(1, min(weeks, 52))
        target_role = discord.utils.get(ctx.guild.roles, name=self.TARGET_ROLE_NAME)
        if not target_role:
            return await ctx.send(embed=discord.Embed(title="❌ エラー", description="対象ロールが見つかりません。", color=0xE74C3C))

        week_rows = await db.fetchall(
            "SELECT DISTINCT week_start FROM attendance_weekly WHERE guild_id = ? ORDER BY week_start DESC LIMIT ?",
            (ctx.guild.id, weeks),
        )
        if not week_rows:
            return await ctx.send(embed=discord.Embed(title="出欠率", description="記録がまだありません。", color=0x95A5A6))
        week_starts = [r['week_start'] for r in week_rows]
        placeholders = ",".join("?" for _ in week_starts)

        entries = []
        for member in target_role.members:
            if member.bot:
                continue
            rows = await db.fetchall(
                f"SELECT status, COUNT(*) as cnt FROM attendance_weekly "
                f"WHERE guild_id = ? AND user_id = ? AND week_start IN ({placeholders}) "
                f"GROUP BY status",
                (ctx.guild.id, member.id, *week_starts),
            )
            if not rows:
                continue
            counts = {r['status']: r['cnt'] for r in rows}
            vc = counts.get('vc', 0)
            excused = counts.get('excused', 0)
            absent = counts.get('absent', 0)
            total = vc + excused + absent
            handled_rate = (vc + excused) / total * 100
            line = (
                f"{member.display_name}: 対応 {vc + excused}/{total}週 ({handled_rate:.0f}%) "
                f"｜🎤{vc} 📝{excused} ❌{absent}"
            )
            entries.append((handled_rate, line))

        if not entries:
            return await ctx.send(embed=discord.Embed(title="出欠率", description="記録がまだありません。", color=0x95A5A6))

        entries.sort(key=lambda x: x[0])
        embed = discord.Embed(
            title=f"出欠率（直近{len(week_starts)}週・対応率が低い順）",
            description="\n".join(text for _, text in entries),
            color=0x3498DB,
        )
        embed.set_footer(text="対応率 = (VC参加 + 欠席理由承認) / 集計週数")
        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(VoiceLoggerCog(bot))
