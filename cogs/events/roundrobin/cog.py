# cogs/roundrobin/cog.py
import discord
from discord.ext import commands
import logging
import os
from dotenv import load_dotenv
import sqlite3
import functools # functoolsをインポート

from cogs.events.event_manager.database import DatabaseManager
from cogs.events.event_manager.models import DummyPlayer
from .rr_views import RegistrationView
from .rr_models import RR_Team
from . import rr_logic, rr_handlers, rr_roles

log = logging.getLogger(__name__)

class RoundRobinCog(commands.Cog, name="RoundRobin"):
    """チーム登録制の総当たり戦を管理するCog"""
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager()
        
        load_dotenv()
        # チャンネルは大会機能(!sd設定メイン/!sd設定対戦)と同じ event_settings を使う。
        # main = 結果発表・対戦表、match = 対戦カード・結果報告（スイスドローと同じ役割分担）。
        # 未設定時のフォールバックは EventManagerCogと共通の既定値。
        try: self.default_main_channel_id = int(os.getenv('DEFAULT_MAIN_CHANNEL_ID', 0))
        except (ValueError, TypeError): self.default_main_channel_id = 0; log.warning(".envのDEFAULT_MAIN_CHANNEL_IDが不正です。")
        try: self.default_match_channel_id = int(os.getenv('DEFAULT_MATCH_CHANNEL_ID', 0))
        except (ValueError, TypeError): self.default_match_channel_id = 0; log.warning(".envのDEFAULT_MATCH_CHANNEL_IDが不正です。")

        if not hasattr(bot, 'persistent_views_added_rr'):
            from .rr_views import ReportResultView, NextRoundViewRR, CorrectResultView
            bot.add_view(ReportResultView())
            bot.add_view(NextRoundViewRR())
            bot.add_view(CorrectResultView())
            bot.add_view(RegistrationView())
            bot.persistent_views_added_rr = True

        self.handle_create_team = functools.partial(rr_handlers.handle_create_team, self)
        self.handle_join_team = functools.partial(rr_handlers.handle_join_team, self)
        self.handle_leave_team = functools.partial(rr_handlers.handle_leave_team, self)
        self.handle_score_report = functools.partial(rr_handlers.handle_score_report, self)
        self.get_joinable_teams = functools.partial(rr_logic.get_joinable_teams, self)
        self._execute_next_round = functools.partial(rr_logic.execute_next_round, self)

    def _resolve_rr_channels(self, guild: discord.Guild, fallback: discord.TextChannel) -> tuple[discord.TextChannel, discord.TextChannel]:
        """(main_ch, match_ch) を返す。main=結果発表・対戦表、match=対戦カード・結果報告。event_settingsを大会機能と共通で使う。"""
        settings = self.db.fetchone(
            "SELECT main_channel_id, match_channel_id FROM event_settings WHERE guild_id = ?", (guild.id,)
        )
        main_id = settings['main_channel_id'] if settings and settings['main_channel_id'] else self.default_main_channel_id
        match_id = settings['match_channel_id'] if settings and settings['match_channel_id'] else self.default_match_channel_id

        main_ch = self.bot.get_channel(main_id) if main_id else None
        if not main_ch:
            if main_id: log.warning(f"設定されたメインチャンネル (ID: {main_id}) が見つかりません。")
            main_ch = fallback

        match_ch = self.bot.get_channel(match_id) if match_id else None
        if not match_ch:
            if match_id: log.warning(f"設定された対戦カードチャンネル (ID: {match_id}) が見つかりません。")
            match_ch = main_ch

        return main_ch, match_ch

    async def _create_team(self, guild_id: int, team_name: str, processed_members: list) -> tuple[bool, str]:
        """メンバーリストから1チームを作成する共通処理。!チーム登録 と、チーム分け結果からの一括登録で共用する。"""
        if not processed_members: return False, "チームには少なくとも1人のメンバーが必要です。"
        if self.db.fetchone("SELECT 1 FROM rr_teams WHERE guild_id = ? AND name = ?", (guild_id, team_name)): return False, f"チーム名「{team_name}」は既に使用されています。"
        if len(processed_members) != len(set(m.id for m in processed_members)): return False, "1つのチームに同じメンバーを複数登録することはできません。"
        for member in processed_members:
            if not isinstance(member, DummyPlayer):
                if self.db.fetchone("SELECT 1 FROM rr_players WHERE guild_id = ? AND user_id = ?", (guild_id, member.id)): return False, f"{member.display_name} さんは既に別のチームに登録されています。"

        team_id = None
        try:
            team_id = self.db.execute("INSERT INTO rr_teams (guild_id, name) VALUES (?, ?)", (guild_id, team_name), return_lastrowid=True)
            if not team_id: raise sqlite3.Error("チームIDの取得に失敗しました。")

            player_data_list = [(member.id, guild_id, team_id, member.display_name, isinstance(member, DummyPlayer), i + 1) for i, member in enumerate(processed_members)]
            self.db.execute("INSERT INTO rr_players (user_id, guild_id, team_id, display_name, is_dummy, position) VALUES (?, ?, ?, ?, ?, ?)", player_data_list)

            for member in processed_members:
                await rr_roles.assign_roles_on_join(self, member, team_id)

            return True, f"チーム「**{team_name}**」を登録しました！ ({len(processed_members)}人)\nメンバー: {', '.join(m.display_name for m in processed_members)}"
        except Exception as e:
            log.error(f"チーム登録中に予期せぬエラー: {e}", exc_info=True)
            if team_id: self.db.execute("DELETE FROM rr_teams WHERE team_id = ?", (team_id,))
            return False, "チーム登録中に予期せぬ問題が発生しました。"

    @commands.command(name="チーム登録", help="総当たり戦に参加するチームを登録します。")
    async def register_team(self, ctx: commands.Context, team_name: str, *members_input: str):
        guild_id = ctx.guild.id
        if self.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)): return await ctx.send("エラー: 既に大会が進行中です。チームの追加はできません。")

        processed_members = []
        for member_str in members_input:
            try: member = await commands.MemberConverter().convert(ctx, member_str); processed_members.append(member)
            except commands.MemberNotFound: processed_members.append(DummyPlayer(name=member_str))

        ok, msg = await self._create_team(guild_id, team_name, processed_members)
        await ctx.send(("✅ " if ok else "エラー: ") + msg)
        if ok:
            await rr_logic.update_registration_panel(self, guild_id)

    @commands.command(name="総当たりパネル", help="チーム登録用のパネルを表示します。")
    @commands.has_permissions(manage_guild=True)
    async def rr_panel(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        embed = await rr_logic.generate_registration_embed(self, guild_id)
        message = await ctx.send(embed=embed, view=RegistrationView())
        self.db.execute("INSERT OR IGNORE INTO rr_config (guild_id) VALUES (?)", (guild_id,))
        self.db.execute("UPDATE rr_config SET panel_message_id = ?, panel_channel_id = ? WHERE guild_id = ?", (message.id, message.channel.id, guild_id))
        try: await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound): pass

    @commands.command(name="チーム取消", help="指定したチームの登録を取り消します。")
    async def unregister_team(self, ctx: commands.Context, *, team_name: str):
        guild_id = ctx.guild.id
        team = self.db.fetchone("SELECT team_id, role_id FROM rr_teams WHERE guild_id = ? AND name = ?", (guild_id, team_name))
        if not team: return await ctx.send(f"エラー: チーム「{team_name}」が見つかりません。")
        if team['role_id']:
            role = ctx.guild.get_role(team['role_id'])
            if role:
                try: await role.delete(reason=f"チーム '{team_name}' が解散したため")
                except discord.Forbidden: log.warning(f"チームロール '{role.name}' の削除に失敗しました。権限がありません。")
                except Exception as e: log.error(f"チームロール削除中にエラー: {e}")
        self.db.execute("DELETE FROM rr_teams WHERE team_id = ?", (team['team_id'],))
        await ctx.send(f"🗑️ チーム「{team_name}」を削除しました。")
        await rr_logic.update_registration_panel(self, guild_id)

    # ▼▼▼ 修正: ダミープレイヤーの登録に対応 ▼▼▼
    @commands.command(name="チームメンバー追加", help="指定したチームに新しいメンバーを追加します。")
    async def add_team_member(self, ctx: commands.Context, team_name: str, *members_input: str):
        guild_id = ctx.guild.id
        if not members_input: return await ctx.send("エラー: 追加するメンバーを1人以上指定してください。")

        processed_members = []
        for member_str in members_input:
            try:
                member = await commands.MemberConverter().convert(ctx, member_str)
                processed_members.append(member)
            except commands.MemberNotFound:
                processed_members.append(DummyPlayer(name=member_str))

        if self.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)):
            return await ctx.send("エラー: 既に大会が進行中です。メンバーの追加はできません。")

        team = self.db.fetchone("SELECT team_id FROM rr_teams WHERE guild_id = ? AND name = ?", (guild_id, team_name))
        if not team: return await ctx.send(f"エラー: チーム「{team_name}」が見つかりません。")
        team_id = team['team_id']

        for member in processed_members:
            if not isinstance(member, DummyPlayer):
                if self.db.fetchone("SELECT 1 FROM rr_players WHERE guild_id = ? AND user_id = ?", (guild_id, member.id)):
                    return await ctx.send(f"エラー: {member.display_name} さんは既に別のチームに登録されています。")

        try:
            max_pos = self.db.fetchone("SELECT MAX(position) as max_p FROM rr_players WHERE team_id = ?", (team_id,))['max_p'] or 0
            player_data_list = []
            for i, member in enumerate(processed_members):
                player_data_list.append((member.id, guild_id, team_id, member.display_name, isinstance(member, DummyPlayer), max_pos + 1 + i))
            
            self.db.execute("INSERT INTO rr_players (user_id, guild_id, team_id, display_name, is_dummy, position) VALUES (?, ?, ?, ?, ?, ?)", player_data_list)
            
            for member in processed_members:
                await rr_roles.assign_roles_on_join(self, member, team_id)

            added_members_str = ', '.join(m.display_name for m in processed_members)
            await ctx.send(f"✅ チーム「**{team_name}**」に新しいメンバー ({added_members_str}) を追加しました！")
            await rr_logic.update_registration_panel(self, guild_id)
        except Exception as e:
            log.error(f"メンバー追加中に予期せぬエラー: {e}", exc_info=True)
            await ctx.send("エラー: メンバー追加中に予期せぬ問題が発生しました。")
    # ▲▲▲ 修正ここまで ▲▲▲

    async def _start_tournament_core(
        self, guild_id: int, main_channel: discord.TextChannel, match_channel: discord.TextChannel, order: str = 'random'
    ) -> tuple[bool, str]:
        """登録済みチームで総当たり戦を開始する共通処理。!総当たり開始 と、チーム分け結果からの一括開始で共用する。
        開始アナウンス・対戦表は main_channel、対戦カード・結果報告は match_channel に送る（スイスドローと同じ役割分担）。"""
        order = order.lower()
        if order not in ['random', 'fixed']: return False, "エラー: 対戦の順番は `random` または `fixed` で指定してください。"
        try:
            if self.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)): return False, "既に大会が進行中です。"
            teams_data = self.db.fetchall("SELECT team_id, name FROM rr_teams WHERE guild_id = ?", (guild_id,))
            if len(teams_data) < 2: return False, f"総当たり戦を開始するには2チーム以上の登録が必要です。(現在 {len(teams_data)} チーム)"

            self.db.execute("DELETE FROM rr_matches WHERE guild_id = ?", (guild_id,)); self.db.execute("DELETE FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
            config = self.db.fetchone("SELECT role_id FROM rr_config WHERE guild_id = ?", (guild_id,))
            role_id = config['role_id'] if config else None
            self.db.execute("INSERT INTO rr_tournaments (guild_id, is_active, current_round, member_order, participant_role_id) VALUES (?, ?, ?, ?, ?)", (guild_id, True, 1, order, role_id))

            teams = [RR_Team(id=t['team_id'], name=t['name']) for t in teams_data]
            schedule = rr_logic.generate_round_robin_schedule(teams)
            matches_to_insert = [(guild_id, r_num, t1.id, t2.id) for r_num, t1, t2 in schedule]
            if matches_to_insert: self.db.execute("INSERT INTO rr_matches (guild_id, round_num, team1_id, team2_id) VALUES (?, ?, ?, ?)", matches_to_insert)

            await main_channel.send(f"**⚔️ チーム総当たり戦、開始！ ⚔️**\n> メンバー表示順: **{order}**")
            await rr_logic.send_match_schedule(self, main_channel)
            if match_channel.id != main_channel.id:
                await main_channel.send(f"対戦カードを <#{match_channel.id}> に送信しました。")
            await rr_logic.send_round_match_cards(self, match_channel, 1)
            return True, "総当たり戦を開始しました。"
        except Exception as e:
            log.error(f"Guild {guild_id}: 総当たり戦開始処理中に致命的なエラーが発生: {e}", exc_info=True)
            return False, "❌ エラー: 大会の開始処理中に問題が発生しました。"

    @commands.command(name="総当たり開始", help="登録されたチームで総当たり戦を開始します。'random'か'fixed'で打順を指定できます。")
    async def start_tournament(self, ctx: commands.Context, order: str = 'random'):
        main_ch, match_ch = self._resolve_rr_channels(ctx.guild, ctx.channel)
        ok, msg = await self._start_tournament_core(ctx.guild.id, main_ch, match_ch, order)
        if not ok:
            await ctx.send(msg)

    async def start_from_teams(self, guild: discord.Guild, channel: discord.TextChannel, teams: dict) -> tuple[bool, str]:
        """チーム分け結果 ({チーム名: [メンバー...]}) から一括でチーム登録し、そのまま総当たり戦を開始する。"""
        guild_id = guild.id
        if self.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)):
            return False, "既に総当たり戦が進行中です。`!総当たり中止` で一度リセットしてください。"
        if self.db.fetchone("SELECT 1 FROM rr_teams WHERE guild_id = ?", (guild_id,)):
            return False, "既に登録済みのチームがあります。`!総当たり中止` で一度リセットしてから実行してください。"

        for team_name, members in teams.items():
            ok, msg = await self._create_team(guild_id, team_name, members)
            if not ok:
                return False, f"チーム「{team_name}」の登録に失敗しました: {msg}"

        await rr_logic.update_registration_panel(self, guild_id)
        main_ch, match_ch = self._resolve_rr_channels(guild, channel)
        return await self._start_tournament_core(guild_id, main_ch, match_ch, order='random')

    @commands.command(name="総当たり中止", help="進行中の総当たり戦やチーム登録を中止・リセットします。")
    @commands.has_permissions(manage_guild=True)
    async def cancel_rr_command(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        teams_with_roles = self.db.fetchall("SELECT role_id FROM rr_teams WHERE guild_id = ? AND role_id IS NOT NULL", (guild_id,))
        for team in teams_with_roles:
            role = ctx.guild.get_role(team['role_id'])
            if role:
                try: await role.delete(reason="総当たり戦が中止されたため")
                except discord.Forbidden: log.warning(f"ロール ID {team['role_id']} の削除に失敗しました。")
        config = self.db.fetchone("SELECT role_id FROM rr_config WHERE guild_id = ?", (guild_id,))
        if config and config['role_id']:
            role = ctx.guild.get_role(config['role_id'])
            if role:
                try: await role.delete(reason="総当たり戦が中止されたため")
                except discord.Forbidden: log.warning(f"参加者ロール ID {config['role_id']} の削除に失敗しました。")
        self.db.execute("DELETE FROM rr_matches WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM rr_players WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM rr_teams WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
        self.db.execute("UPDATE rr_config SET role_id = NULL WHERE guild_id = ?", (guild_id,))
        await ctx.send("🚨 総当たり戦の登録情報、進行状況、関連ロールをすべてリセットしました。")
        await rr_logic.update_registration_panel(self, guild_id)

    @commands.command(name="次節", help="総当たり戦の次の節を開始します。")
    async def next_round_command(self, ctx: commands.Context): await rr_logic.execute_next_round(self, ctx)

    @commands.command(name="順位", help="総当たり戦の現在の順位を表示します。")
    async def show_standings(self, ctx: commands.Context):
        if not self.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (ctx.guild.id,)): return await ctx.send("現在進行中の総当たり戦はありません。")
        main_ch, _ = self._resolve_rr_channels(ctx.guild, ctx.channel)
        await rr_logic.display_standings(self, main_ch)

async def setup(bot):
    await bot.add_cog(RoundRobinCog(bot))
