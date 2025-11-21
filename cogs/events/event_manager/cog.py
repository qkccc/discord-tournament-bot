# cogs/tournament/cog.py
import discord
from discord.ext import commands
import os
import json
import logging
import random
import math
from dotenv import load_dotenv
from typing import Set, Dict, Optional, List, Tuple, Union
import asyncio
from collections import defaultdict

# --- ローカルモジュールのインポート ---
from .database import DatabaseManager
from .models import DummyPlayer, Player, SwissTournament
from .views import MainControlView, ResultReportView, NextRoundView, ConfirmCancelView, TeamSplitMethodView, SwissResultReportView
from .image_utils import create_bracket_image_from_db

log = logging.getLogger(__name__)

class EventManagerCog(commands.Cog, name="EventManager"):
    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager('data/tournaments.db')
        self.recruit_sessions: Dict[int, dict] = {}
        self.swiss_tournaments: Dict[int, SwissTournament] = {}
        self.locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

        load_dotenv()
        try:
            self.DEFAULT_MAIN_CHANNEL_ID = int(os.getenv('DEFAULT_MAIN_CHANNEL_ID', 0))
            self.DEFAULT_MATCH_CHANNEL_ID = int(os.getenv('DEFAULT_MATCH_CHANNEL_ID', 0))
        except (ValueError, TypeError):
            self.DEFAULT_MAIN_CHANNEL_ID = 0
            self.DEFAULT_MATCH_CHANNEL_ID = 0
            log.warning(".envファイル内のチャンネルIDが不正な値です。")

        if not hasattr(bot, 'persistent_views_added'):
            bot.add_view(MainControlView(self))
            bot.add_view(NextRoundView())
            bot.persistent_views_added = True
        
        # ▼▼▼ 変更点 ▼▼▼
        # Bot起動時に、アクティブなスイスドロー大会と「募集」をDBから復元するタスクを開始
        self.bot.loop.create_task(self._load_swiss_tournaments_from_db())
        self.bot.loop.create_task(self._load_active_recruitments())
        # ▲▲▲ 変更ここまで ▲▲▲

    # ==============================================================================
    # 既存のヘルパーメソッド、リスナー、コマンド（元のコードを完全に維持）
    # ==============================================================================

    # ▼▼▼ 変更点 ▼▼▼
    async def _load_active_recruitments(self):
        """データベースからアクティブな「募集」を復元する"""
        await self.bot.wait_until_ready()
        log.info("データベースからアクティブな募集セッションの復元を開始します...")
        sessions_data = self.db.fetchall("SELECT * FROM recruitment_sessions")

        for session_row in sessions_data:
            guild_id = session_row['guild_id']
            guild = self.bot.get_guild(guild_id)
            if not guild:
                log.warning(f"Guild ID {guild_id} が見つからないため、募集セッションを復元できませんでした。")
                continue

            participants_data = self.db.fetchall("SELECT * FROM recruitment_participants WHERE guild_id = ?", (guild_id,))
            participants_set = set()
            for p_row in participants_data:
                if p_row['is_dummy']:
                    dummy = DummyPlayer(p_row['display_name'])
                    dummy.id = p_row['user_id']
                    participants_set.add(dummy)
                else:
                    member = guild.get_member(p_row['user_id'])
                    if member:
                        participants_set.add(member)
                    else:
                        log.warning(f"参加者(ID: {p_row['user_id']})がサーバーに見つかりませんでした。")
            
            self.recruit_sessions[guild_id] = {
                "message_id": session_row['message_id'],
                "channel_id": session_row['channel_id'],
                "participants": participants_set
            }
            log.info(f"Guild ID {guild_id} の募集セッションを正常に復元しました。")
    # ▲▲▲ 変更ここまで ▲▲▲

    async def _load_swiss_tournaments_from_db(self):
        """データベースからアクティブな「スイスドロー」大会を復元する"""
        await self.bot.wait_until_ready()
        log.info("データベースからアクティブなスイスドロー大会の復元を開始します...")
        tournaments_data = self.db.fetchall("SELECT * FROM tournaments WHERE is_active = ?", (True,))

        for row in tournaments_data:
            guild_id, is_active, round_num, max_rounds = row['guild_id'], row['is_active'], row['round_num'], row['max_rounds']
            guild = self.bot.get_guild(guild_id)
            if not guild:
                log.warning(f"Guild ID {guild_id} のサーバーが見つからないため、スイスドロー大会を復元できませんでした。")
                continue

            tournament = SwissTournament(set())
            tournament.is_active = is_active
            tournament.round_num = round_num
            tournament.max_rounds = max_rounds

            # playersテーブルからスイスドロー用の情報を取得
            players_data = self.db.fetchall("SELECT * FROM players WHERE guild_id = ?", (guild_id,))
            for p_row in players_data:
                user_id, display_name, score, opponents_json, byes, wins, losses, matches_played, is_dummy = p_row['user_id'], p_row['display_name'], p_row['score'], p_row['opponents'], p_row['byes'], p_row['wins'], p_row['losses'], p_row['matches_played'], p_row['is_dummy']
                member_obj: Union[discord.Member, DummyPlayer]
                if is_dummy:
                    member_obj = DummyPlayer(display_name)
                    member_obj.id = user_id
                else:
                    member_obj = guild.get_member(user_id)
                
                if not member_obj:
                    log.warning(f"Player ID {user_id} ({display_name}) がサーバーに見つかりません。ダミーとして扱います。")
                    member_obj = DummyPlayer(f"{display_name} (不明)")
                    member_obj.id = user_id
                
                player_obj = Player(member_obj)
                player_obj.score, player_obj.byes, player_obj.wins, player_obj.losses, player_obj.matches_played = score, byes, wins, losses, matches_played
                player_obj.opponents = set(json.loads(opponents_json))
                tournament.players[user_id] = player_obj

            pairings_data = self.db.fetchall("SELECT player1_id, player2_id FROM current_pairings WHERE guild_id = ?", (guild_id,))
            for pair_row in pairings_data:
                p1 = tournament.get_player(pair_row['player1_id'])
                p2 = tournament.get_player(pair_row['player2_id']) if pair_row['player2_id'] else None
                if p1:
                    tournament.current_pairings.append((p1, p2))

            reports_data = self.db.fetchall("SELECT winner_id, loser_id FROM reported_matches WHERE guild_id = ? AND round_num = ?", (guild_id, round_num))
            for report_row in reports_data:
                tournament.reported_matches_this_round.append((report_row[0], report_row[1], report_row[0])) # Assuming winner_id is the third element for compatibility

            self.swiss_tournaments[guild_id] = tournament
            log.info(f"Guild ID {guild_id} のスイスドロー大会を正常に復元しました。")


    async def _update_recruitment_message(self, guild_id: int):
        session = self.recruit_sessions.get(guild_id)
        if not session: return
        try:
            channel = self.bot.get_channel(session['channel_id'])
            if not channel: return
            message = await channel.fetch_message(session['message_id'])
            participants = session['participants']
            p_list = '\n'.join(f"・ {p.display_name}" for p in sorted(list(participants), key=lambda x:x.display_name)) if participants else "(まだ誰もいません)"
            embed = message.embeds[0]
            embed.description = f"このメッセージに👍でリアクションして参加してください！\n\n**現在の参加者 ({len(participants)}人):**\n{p_list}"
            await message.edit(embed=embed)
        except (discord.NotFound, discord.Forbidden, AttributeError) as e:
            log.warning(f"募集メッセージの更新に失敗 (Guild: {guild_id}): {e}")
            self.recruit_sessions.pop(guild_id, None)

    async def _close_recruitment(self, interaction: discord.Interaction, new_content: str = "募集を締め切りました。") -> Set[Union[discord.Member, DummyPlayer]]:
        guild_id = interaction.guild.id
        session = self.recruit_sessions.pop(guild_id, None)
        if session:
            # ▼▼▼ 変更点 ▼▼▼
            # 募集終了時にDBから関連データを削除
            self.db.execute("DELETE FROM recruitment_sessions WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM recruitment_participants WHERE guild_id = ?", (guild_id,))
            # ▲▲▲ 変更ここまで ▲▲▲
            try:
                channel = self.bot.get_channel(session['channel_id'])
                if not channel: return set()
                message = await channel.fetch_message(session['message_id'])
                await message.edit(content=f"**--- {new_content} ---**", view=None, embed=None)
            except (discord.NotFound, discord.Forbidden): pass
            return session.get('participants', set())
        return set()

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id: return
        session = self.recruit_sessions.get(payload.guild_id)
        if not session or payload.message_id != session['message_id'] or str(payload.emoji) != '👍': return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        member = guild.get_member(payload.user_id)
        if member:
            session['participants'].add(member)
            # ▼▼▼ 変更点 ▼▼▼
            # DBに参加者情報を追加
            self.db.execute(
                "INSERT OR IGNORE INTO recruitment_participants (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)",
                (payload.guild_id, member.id, member.display_name, False)
            )
            # ▲▲▲ 変更ここまで ▲▲▲
            await self._update_recruitment_message(payload.guild_id)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if payload.user_id == self.bot.user.id: return
        session = self.recruit_sessions.get(payload.guild_id)
        if not session or payload.message_id != session['message_id'] or str(payload.emoji) != '👍': return
        guild = self.bot.get_guild(payload.guild_id)
        if not guild: return
        member = guild.get_member(payload.user_id)
        if member and member in session['participants']:
            session['participants'].remove(member)
            # ▼▼▼ 変更点 ▼▼▼
            # DBから参加者情報を削除
            self.db.execute(
                "DELETE FROM recruitment_participants WHERE guild_id = ? AND user_id = ?",
                (payload.guild_id, member.id)
            )
            # ▲▲▲ 変更ここまで ▲▲▲
            await self._update_recruitment_message(payload.guild_id)

    @commands.command(name='募集', aliases=['スイスドロー'], help='各種イベントの参加者募集を開始します')
    async def recruit(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        if guild_id in self.recruit_sessions: return await ctx.send("現在、既に参加者募集中です。")
        if self.db.fetchone("SELECT 1 FROM se_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)):
             return await ctx.send("現在進行中のトーナメントがあります。先に`!中止`コマンドで終了してください。")
        if guild_id in self.swiss_tournaments:
            return await ctx.send("現在進行中のスイスドロー大会があります。先に`!中止`コマンドで終了してください。")

        embed = discord.Embed(title="🏆 イベント参加者募集 🏆", description="このメッセージに👍でリアクションして参加してください！\n\n**現在の参加者 (0人):**\n(まだ誰もいません)", color=0x7289da)
        view = MainControlView(self)
        msg = await ctx.send(embed=embed, view=view)
        await msg.add_reaction("👍")
        
        # ▼▼▼ 変更点 ▼▼▼
        # メモリとDBの両方にセッション情報を保存
        self.recruit_sessions[ctx.guild.id] = {"message_id": msg.id, "channel_id": ctx.channel.id, "participants": set()}
        self.db.execute(
            "INSERT INTO recruitment_sessions (guild_id, message_id, channel_id) VALUES (?, ?, ?)",
            (ctx.guild.id, msg.id, ctx.channel.id)
        )
        # ▲▲▲ 変更ここまで ▲▲▲

    async def _get_channels(self, guild_id: int) -> Tuple[Optional[discord.TextChannel], Optional[discord.TextChannel]]:
        settings = self.db.fetchone("SELECT main_channel_id, match_channel_id FROM settings WHERE guild_id = ?", (guild_id,))
        main_ch_id = settings["main_channel_id"] if settings and settings["main_channel_id"] else self.DEFAULT_MAIN_CHANNEL_ID
        match_ch_id = settings["match_channel_id"] if settings and settings["match_channel_id"] else self.DEFAULT_MATCH_CHANNEL_ID
        main_ch = self.bot.get_channel(main_ch_id)
        match_ch = self.bot.get_channel(match_ch_id) if match_ch_id else main_ch
        return main_ch, match_ch
        
    @commands.command(name='sd設定メイン', help='アナウンス用チャンネルを設定します。')
    @commands.has_permissions(manage_guild=True)
    async def set_main_channel(self, ctx: commands.Context):
        self.db.execute("INSERT INTO settings (guild_id, main_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET main_channel_id = excluded.main_channel_id", (ctx.guild.id, ctx.channel.id))
        await ctx.send(f"✅ アナウンスチャンネルを `#{ctx.channel.name}` に設定しました。")

    @commands.command(name='sd設定対戦', help='対戦カード送信用チャンネルを設定します。')
    @commands.has_permissions(manage_guild=True)
    async def set_match_channel(self, ctx: commands.Context):
        self.db.execute("INSERT INTO settings (guild_id, match_channel_id) VALUES (?, ?) ON CONFLICT(guild_id) DO UPDATE SET match_channel_id = excluded.match_channel_id", (ctx.guild.id, ctx.channel.id))
        await ctx.send(f"✅ 対戦カード送信用チャンネルを `#{ctx.channel.name}` に設定しました。")
    
    @commands.command(name='追加', help='募集中のリストにダミーの参加者を追加します。例: !追加 太郎')
    @commands.has_permissions(manage_guild=True)
    async def add_dummy(self, ctx: commands.Context, *, name: str = None):
        session = self.recruit_sessions.get(ctx.guild.id)
        if not session: return await ctx.send("参加者募集中のイベントがありません。")

        # ▼▼▼ 変更点 ▼▼▼
        if name:
            # 名前が指定された場合
            # 既に同じ名前のダミーがいないかチェック
            if any(isinstance(p, DummyPlayer) and p.display_name == name for p in session['participants']):
                return await ctx.send(f"エラー: `{name}` という名前のダミー参加者は既に追加されています。", ephemeral=True)
            dummy_name = name

        else:
            # 名前が指定されない場合 (これまで通りの動作)
            dummy_name = f"ダミー{len([p for p in session['participants'] if isinstance(p, DummyPlayer)]) + 1}"
        
        dummy = DummyPlayer(dummy_name)
        session['participants'].add(dummy)
        # ▲▲▲▲▲▲▲▲▲▲▲▲

        # DBにダミー参加者を追加
        self.db.execute(
                "INSERT OR IGNORE INTO recruitment_participants (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)",
                (ctx.guild.id, dummy.id, dummy.display_name, True)
            )
        await self._update_recruitment_message(ctx.guild.id)
        await ctx.message.add_reaction("✅")

    @commands.command(name='削除', help='募集中のリストからダミーの参加者を削除します')
    @commands.has_permissions(manage_guild=True)
    async def remove_dummy(self, ctx: commands.Context, num_to_remove: int = 0):
        session = self.recruit_sessions.get(ctx.guild.id)
        if not session: return await ctx.send("参加者募集中のイベントがありません。")
        dummies = [p for p in session['participants'] if isinstance(p, DummyPlayer)]
        if not dummies: return await ctx.send("削除できるダミー参加者がいません。")
        count = len(dummies) if num_to_remove <= 0 else min(num_to_remove, len(dummies))
        
        dummies_to_remove = dummies[:count]
        for dummy in dummies_to_remove:
            session['participants'].remove(dummy)
            # ▼▼▼ 変更点 ▼▼▼
            # DBからダミー参加者を削除
            self.db.execute(
                "DELETE FROM recruitment_participants WHERE guild_id = ? AND user_id = ?",
                (ctx.guild.id, dummy.id)
            )
            # ▲▲▲ 変更ここまで ▲▲▲
        
        await self._update_recruitment_message(ctx.guild.id)
        await ctx.message.add_reaction("✅")

    async def _execute_leader_team_split(self, interaction: discord.Interaction, leaders: List[discord.Member]):
        participants = await self._close_recruitment(interaction, "チーム分けを実行しました！")
        if not participants: return await interaction.followup.send("参加者がいないため、開始できませんでした。", ephemeral=True)
        valid_leaders = [leader for leader in leaders if leader and leader in participants]
        if len(valid_leaders) != len(leaders): return await interaction.followup.send("指定されたリーダーの一部が参加者にいません。", ephemeral=True)
        members = list(participants - set(valid_leaders)); random.shuffle(members)
        teams = {leader: [leader] for leader in valid_leaders}
        for i, member in enumerate(members): teams[valid_leaders[i % len(valid_leaders)]].append(member)
        embed = discord.Embed(title="👑 リーダー制チーム分け結果", color=0xf1c40f)
        for l, mem in teams.items(): embed.add_field(name=f"**{l.display_name}**チーム ({len(mem)}人)", value='\n'.join(f"・{m.display_name}" for m in mem), inline=False)
        await interaction.channel.send(f"**チーム分け結果** (参加者: {len(participants)}人)", embed=embed)

    async def _execute_number_team_split(self, interaction: discord.Interaction, num_teams: int):
        participants = await self._close_recruitment(interaction, "チーム分けを実行しました！")
        if not participants: return await interaction.followup.send("参加者がいないため、開始できませんでした。", ephemeral=True)
        if len(participants) < num_teams: return await interaction.followup.send(f"参加者（{len(participants)}人）を{num_teams}チームに分けることはできません。", ephemeral=True)
        p_list = list(participants); random.shuffle(p_list)
        teams = [[] for _ in range(num_teams)]
        for i, p in enumerate(p_list): teams[i % num_teams].append(p)
        embed = discord.Embed(title="🎲 チーム分け結果", color=0x58d68d)
        for i, mem in enumerate(teams): embed.add_field(name=f"**チーム{i+1}** ({len(mem)}人)", value='\n'.join(f"・{m.display_name}" for m in mem), inline=False)
        await interaction.channel.send(f"**チーム分け結果** (参加者: {len(participants)}人)", embed=embed)

    # ==============================================================================
    # スイスドロー機能のメソッド (変更なし)
    # ==============================================================================
    async def _execute_start_swiss(self, interaction: discord.Interaction, rounds: int):
        """スイスドロー大会を開始する"""
        participants = await self._close_recruitment(interaction, "スイスドロー大会を開始しました！")
        if not participants:
            return await interaction.followup.send("参加者がいないため、開始できませんでした。", ephemeral=True)

        guild_id = interaction.guild.id
        tournament = SwissTournament(participants)
        tournament.max_rounds = rounds
        tournament.round_num = 1
        self.swiss_tournaments[guild_id] = tournament

        self.db.execute("DELETE FROM tournaments WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM players WHERE guild_id = ?", (guild_id,))
        self.db.execute("INSERT INTO tournaments (guild_id, is_active, round_num, max_rounds) VALUES (?, ?, ?, ?)", (guild_id, True, 1, rounds if rounds > 0 else 0))
        
        player_data = [(guild_id, p.id, p.display_name, isinstance(p.member, DummyPlayer), p.score, json.dumps(list(p.opponents)), p.byes, p.wins, p.losses, p.matches_played) for p in tournament.players.values()]
        if player_data: self.db.execute("INSERT INTO players (guild_id, user_id, display_name, is_dummy, score, opponents, byes, wins, losses, matches_played) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", player_data)

        start_msg = f"**スイスドロー大会を開始します！** (参加者: {len(participants)}人)"
        start_msg += f" 全{rounds}ラウンドです。" if rounds > 0 else " 全勝者が1人になるまで続きます。"
        
        main_ch, match_ch = await self._get_channels(guild_id)
        if not main_ch: main_ch = interaction.channel
        if not match_ch: match_ch = interaction.channel
        
        await main_ch.send(start_msg)
        await self.generate_and_send_pairings(interaction.guild, main_ch, match_ch)

    async def generate_and_send_pairings(self, guild: discord.Guild, main_channel: discord.TextChannel, match_channel: discord.TextChannel):
        """ペアリングを生成し、チャンネルに送信する"""
        tournament = self.swiss_tournaments.get(guild.id)
        if not tournament: return
        
        pairings = tournament.generate_pairings()
        
        self.db.execute("DELETE FROM current_pairings WHERE guild_id = ?", (guild.id,))
        if pairings:
            db_pairings = [(guild.id, p1.id, p2.id if p2 else None) for p1, p2 in pairings]
            self.db.execute("INSERT INTO current_pairings (guild_id, player1_id, player2_id) VALUES (?, ?, ?)", db_pairings)
        
        for p1, p2 in pairings:
            self.db.execute("UPDATE players SET opponents = ? WHERE guild_id = ? AND user_id = ?", (json.dumps(list(p1.opponents)), guild.id, p1.id))
            if p2: self.db.execute("UPDATE players SET opponents = ? WHERE guild_id = ? AND user_id = ?", (json.dumps(list(p2.opponents)), guild.id, p2.id))
            else: self.db.execute("UPDATE players SET byes = ?, wins = ?, score = ? WHERE guild_id = ? AND user_id = ?", (p1.byes, p1.wins, p1.score, guild.id, p1.id))

        sorted_pairings = sorted(pairings, key=lambda pair: pair[0].id if pair[1] is None else min(pair[0].id, pair[1].id))
        desc = '\n'.join(f"**試合 {i+1}**: {p1.display_name} ({p1.record}) ⚔️ {p2.display_name} ({p2.record})" if p2 else f"**不戦勝**: {p1.display_name} ({p1.record}) 🏆" for i, (p1, p2) in enumerate(sorted_pairings))
        embed = discord.Embed(title=f"ラウンド {tournament.round_num} 対戦表", description=desc or "対戦カードがありません。", color=discord.Color.gold())
        await main_channel.send(embed=embed)
        
        if main_channel != match_channel: await main_channel.send(f"対戦カードを <#{match_channel.id}> に送信しました。")
        
        for i, (p1, p2) in enumerate(sorted_pairings):
            if p2:
                embed = discord.Embed(title=f"試合 {i+1}：{p1.display_name} vs {p2.display_name}", description="対戦後、勝者が自身の勝利ボタンを押してください。", color=0x3498db)
                view = SwissResultReportView(guild.id, p1, p2)
                await match_channel.send(embed=embed, view=view)

    async def _handle_swiss_win_logic(self, interaction: discord.Interaction, guild_id: int, winner_id: int, loser_id: int):
        """スイスドローの勝利報告を処理する"""
        tournament = self.swiss_tournaments.get(guild_id)
        if not tournament or not tournament.is_active: return await interaction.followup.send("大会は進行中ではありません。", ephemeral=True)
        
        winner = tournament.get_player(winner_id); loser = tournament.get_player(loser_id)
        if not winner or not loser: return await interaction.followup.send("プレイヤーが見つかりません。", ephemeral=True)
        if tournament.is_match_reported(winner.id, loser.id): return await interaction.followup.send("この試合は既に結果が報告されています。", ephemeral=True)
        
        winner.score += 1.0; winner.wins += 1; winner.matches_played += 1
        loser.losses += 1; loser.matches_played += 1
        tournament.reported_matches_this_round.append((winner.id, loser.id, winner.id))
        
        self.db.execute("UPDATE players SET score=?, wins=?, matches_played=? WHERE guild_id=? AND user_id=?", (winner.score, winner.wins, winner.matches_played, guild_id, winner_id))
        self.db.execute("UPDATE players SET losses=?, matches_played=? WHERE guild_id=? AND user_id=?", (loser.losses, loser.matches_played, guild_id, loser_id))
        self.db.execute("INSERT INTO reported_matches (guild_id, round_num, winner_id, loser_id) VALUES (?, ?, ?, ?)", (guild_id, tournament.round_num, winner_id, loser_id))
        
        await interaction.followup.send(f"✅ {winner.display_name} が {loser.display_name} に勝利しました！", ephemeral=True)
        
        embed = discord.Embed(title=f"試合結果: {winner.display_name}の勝利！", color=discord.Color.green()); embed.set_footer(text="結果報告済み (修正可能)")
        view = SwissResultReportView(guild_id, winner, loser)
        for item in view.children:
            if isinstance(item, discord.ui.Button) and item.custom_id.startswith("swiss_win"): item.disabled = True
        await interaction.message.edit(embed=embed, view=view)
        
        actual_matches = len([p for p, opp in tournament.current_pairings if opp is not None])
        if len(tournament.reported_matches_this_round) >= actual_matches:
            view = NextRoundView()
            await interaction.channel.send("このラウンドの全試合結果が報告されました。\nボタンを押して次のラウンドへ進んでください。", view=view)

    async def _handle_swiss_undo_logic(self, interaction: discord.Interaction, guild_id: int, p1_id: int, p2_id: int):
        """スイスドローの試合結果報告を取り消す"""
        tournament = self.swiss_tournaments.get(guild_id)
        if not tournament or not tournament.is_active: return await interaction.followup.send("大会は進行中ではありません。", ephemeral=True)
        
        p1 = tournament.get_player(p1_id); p2 = tournament.get_player(p2_id)
        if not p1 or not p2: return await interaction.followup.send("プレイヤーが見つかりません。", ephemeral=True)
        
        report = next((r for r in tournament.reported_matches_this_round if {r[0], r[1]} == {p1.id, p2.id}), None)
        if not report: return await interaction.followup.send("この試合の結果はまだ報告されていません。", ephemeral=True)
        
        winner_id, loser_id = report[2], next(iter({p1.id, p2.id} - {report[2]}))
        tournament.reported_matches_this_round.remove(report)
        
        winner = tournament.get_player(winner_id); loser = tournament.get_player(loser_id)
        if winner: winner.score -= 1.0; winner.wins -= 1; winner.matches_played -= 1
        if loser: loser.losses -= 1; loser.matches_played -= 1
        
        self.db.execute("DELETE FROM reported_matches WHERE guild_id = ? AND round_num = ? AND winner_id = ? AND loser_id = ?", (guild_id, tournament.round_num, winner_id, loser_id))
        if winner: self.db.execute("UPDATE players SET score=?, wins=?, matches_played=? WHERE guild_id=? AND user_id=?", (winner.score, winner.wins, winner.matches_played, guild_id, winner.id))
        if loser: self.db.execute("UPDATE players SET losses=?, matches_played=? WHERE guild_id=? AND user_id=?", (loser.losses, loser.matches_played, guild_id, loser.id))
        
        await interaction.followup.send(f"✅ {p1.display_name}と{p2.display_name}の試合結果を取り消しました。", ephemeral=True)
        
        embed = discord.Embed(title=f"試合：{p1.display_name} vs {p2.display_name}", description="対戦後、勝者が自身の勝利ボタンを押してください。", color=discord.Color.blue())
        view = SwissResultReportView(guild_id, p1, p2)
        await interaction.message.edit(embed=embed, view=view)

    async def _execute_next_round(self, interaction: discord.Interaction):
        """次のラウンドへ進む処理"""
        await interaction.response.defer()
        guild_id = interaction.guild.id
        tournament = self.swiss_tournaments.get(guild_id)
        if not tournament: return

        actual_matches = len([p for p, opp in tournament.current_pairings if opp is not None])
        if len(tournament.reported_matches_this_round) < actual_matches: return await interaction.followup.send("まだ報告されていない試合があります。", ephemeral=True)

        main_ch, match_ch = await self._get_channels(guild_id)
        if not main_ch: main_ch = interaction.channel
        if not match_ch: match_ch = interaction.channel

        undefeated = [p for p in tournament.players.values() if p.losses == 0]
        final_round = (tournament.max_rounds > 0 and tournament.round_num >= tournament.max_rounds)
        one_winner = len(undefeated) == 1 and tournament.round_num > 0
        no_winners = len(undefeated) == 0 and tournament.players and tournament.round_num > 0

        if one_winner or no_winners or final_round:
            if one_winner: msg = f"🎉 **優勝者決定！** 🎉\n全勝者が **{undefeated[0].display_name}** さん1名となったため、大会は終了です！"
            elif no_winners: msg = "全勝者がいなくなりました。これにて大会は終了です！"
            else: msg = f"規定の {tournament.max_rounds} ラウンドが終了しました！"
            
            await main_ch.send(msg); await self._display_standings(interaction, final=True, channel=main_ch)
            
            self.db.execute("DELETE FROM tournaments WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM players WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM current_pairings WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM reported_matches WHERE guild_id = ?", (guild_id,))
            self.swiss_tournaments.pop(guild_id, None)
            return

        tournament.round_num += 1
        self.db.execute("UPDATE tournaments SET round_num = ? WHERE guild_id = ?", (tournament.round_num, guild_id))
        
        await main_ch.send(f"**第 {tournament.round_num} ラウンドを開始します！**")
        await self.generate_and_send_pairings(interaction.guild, main_ch, match_ch)

    async def _display_standings(self, interaction: discord.Interaction, final: bool = False, channel: Optional[discord.TextChannel] = None):
        """現在の順位を表示する"""
        target_channel = channel or interaction.channel
        tournament = self.swiss_tournaments.get(interaction.guild.id)
        if not tournament: return
        
        ranked_players = tournament.get_ranked_players()
        embed = discord.Embed(title=f"📊 スイスドロー大会 {'最終' if final else '現在'}順位", color=0xdaa520)
        standings = [f"{i+1}. **{p.display_name}** ({p.record}) - OMW%: {p.omw:.2f}" for i, p in enumerate(ranked_players)]
        embed.description = "\n".join(standings) if standings else "参加者がいません。"
        await target_channel.send(embed=embed)
        
    # ==============================================================================
    # トーナメント機能のメソッド (変更なし)
    # ==============================================================================
    
    async def _execute_bracket(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild_id = interaction.guild.id

        async with self.locks[guild_id]:
            participants_set = await self._close_recruitment(interaction, "トーナメントを開始しました！")
            if not participants_set: return await interaction.followup.send("参加者がいないため、開始できませんでした。", ephemeral=True)

            num_players = len(participants_set)
            num_rounds = math.ceil(math.log2(max(2, num_players)))
            tourney_size = 2 ** num_rounds
            num_byes = tourney_size - num_players

            self.db.execute("DELETE FROM se_matches WHERE guild_id = ?", (guild_id,)); self.db.execute("DELETE FROM se_tournaments WHERE guild_id = ?", (guild_id,)); self.db.execute("DELETE FROM players WHERE guild_id = ?", (guild_id,))
            self.db.execute("INSERT INTO se_tournaments (guild_id, is_active, num_players, num_rounds, bracket_message_id) VALUES (?, ?, ?, ?, ?)", (guild_id, True, num_players, num_rounds, None))

            player_db_data = [(guild_id, p.id, p.display_name, isinstance(p, DummyPlayer)) for p in participants_set]
            if player_db_data: self.db.execute("INSERT INTO players (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)", player_db_data)
            
            player_list_for_seeding = [{"id": p.id, "name": p.display_name, "member": p} for p in participants_set]
            byes = [{"id": None, "name": "(不戦勝)", "is_bye": True} for _ in range(num_byes)]
            seeding_list = player_list_for_seeding + byes
            random.shuffle(seeding_list)

            i = 0
            while i < tourney_size:
                if i + 1 < len(seeding_list):
                    p1 = seeding_list[i]; p2 = seeding_list[i+1]
                    if p1.get("is_bye") and p2.get("is_bye"):
                        swap_target_idx = next((j for j in range(i + 2, tourney_size) if not seeding_list[j].get("is_bye")), -1)
                        if swap_target_idx != -1:
                            seeding_list[i+1], seeding_list[swap_target_idx] = seeding_list[swap_target_idx], seeding_list[i+1]
                        else:
                            log.warning(f"Guild {guild_id}: 不戦勝ペアを解消できず再シャッフルします。"); random.shuffle(seeding_list); i = 0; continue
                i += 2

            matches_to_insert = []; next_round_source_matches = []
            for i in range(0, tourney_size, 2):
                p1_data, p2_data = seeding_list[i], seeding_list[i+1]; match_id = f"R1M{i//2}"
                is_bye_match = p1_data.get("is_bye", False) or p2_data.get("is_bye", False)
                winner_id = p2_data["id"] if p1_data.get("is_bye") else (p1_data["id"] if p2_data.get("is_bye") else None)
                matches_to_insert.append((guild_id, match_id, 1, i//2, p1_data["id"], p2_data["id"], None, None, winner_id, is_bye_match))
                next_round_source_matches.append(match_id)
            
            for r_num in range(2, num_rounds + 1):
                sources = list(next_round_source_matches); next_round_source_matches = []
                for i in range(0, len(sources), 2):
                    p1_source, p2_source = sources[i], sources[i+1]; match_id = f"R{r_num}M{i//2}"
                    matches_to_insert.append((guild_id, match_id, r_num, i//2, None, None, p1_source, p2_source, None, False))
                    next_round_source_matches.append(match_id)
            if matches_to_insert: self.db.execute("INSERT INTO se_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", matches_to_insert)

            for bye_match in self.db.fetchall("SELECT match_id, winner_id FROM se_matches WHERE guild_id = ? AND round_num = 1 AND is_bye = 1", (guild_id,)):
                next_match = self.db.fetchone("SELECT match_id, player1_source_match_id FROM se_matches WHERE guild_id = ? AND (player1_source_match_id = ? OR player2_source_match_id = ?)", (guild_id, bye_match['match_id'], bye_match['match_id']))
                if next_match:
                    column_to_update = "player1_id" if next_match['player1_source_match_id'] == bye_match['match_id'] else "player2_id"
                    self.db.execute(f"UPDATE se_matches SET {column_to_update} = ? WHERE guild_id = ? AND match_id = ?", (bye_match['winner_id'], guild_id, next_match['match_id']))

            await interaction.followup.send("トーナメント表を作成中...", ephemeral=True)
            image_file = await self.bot.loop.run_in_executor(None, create_bracket_image_from_db, guild_id, self.db)
            
            main_ch, match_ch = await self._get_channels(guild_id)
            if not main_ch: main_ch = interaction.channel
            if not match_ch: match_ch = interaction.channel
            
            if image_file: await main_ch.send(f"**トーナメント開始！** (参加者: {num_players}人)", file=image_file)
            else: await main_ch.send("トーナメント表の生成に失敗しました。")
            
            for match in self.db.fetchall("SELECT * FROM se_matches WHERE guild_id = ? AND round_num = 1 ORDER BY match_in_round", (guild_id,)):
                if not match["is_bye"]:
                    p1 = Player(next(p for p in participants_set if p.id == match["player1_id"])); p2 = Player(next(p for p in participants_set if p.id == match["player2_id"]))
                    embed = discord.Embed(title=f"【第{match['round_num']}回戦】 {p1.display_name} vs {p2.display_name}", description="対戦後、勝者が自身の勝利ボタンを押してください。"); view = ResultReportView(guild_id, match["match_id"], p1, p2)
                    await match_ch.send(embed=embed, view=view)
            
            if main_ch.id != match_ch.id: await main_ch.send(f"対戦カードを <#{match_ch.id}> に送信しました。")

    async def _handle_se_win_logic(self, interaction: discord.Interaction, guild_id: int, match_id: str, winner_id: int, loser_id: int):
        async with self.locks[guild_id]:
            try:
                if not self.db.fetchone("SELECT 1 FROM se_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)): return await interaction.followup.send("トーナメントは進行中ではありません。", ephemeral=True)
                current_match = self.db.fetchone("SELECT winner_id FROM se_matches WHERE guild_id = ? AND match_id = ?", (guild_id, match_id));
                if current_match and current_match["winner_id"] is not None: return await interaction.followup.send("この試合は既に結果が報告されています。", ephemeral=True)

                self.db.execute("UPDATE se_matches SET winner_id = ? WHERE guild_id = ? AND match_id = ?", (winner_id, guild_id, match_id)); await interaction.followup.send("勝利報告を受け付けました！", ephemeral=True)
                p_map = {p["user_id"]: p["display_name"] for p in self.db.fetchall("SELECT user_id, display_name FROM players WHERE guild_id = ?", (guild_id,))}
                embed = discord.Embed(title=f"結果: {p_map.get(winner_id, '不明')} の勝利！", color=discord.Color.green()); await interaction.message.edit(embed=embed, view=None)

                next_match = self.db.fetchone("SELECT * FROM se_matches WHERE guild_id = ? AND (player1_source_match_id = ? OR player2_source_match_id = ?)",(guild_id, match_id, match_id))
                main_ch, match_ch = await self._get_channels(guild_id)
                if not main_ch: main_ch = interaction.channel
                if not match_ch: match_ch = interaction.channel

                if next_match:
                    next_match_id = next_match["match_id"]
                    column = "player1_id" if next_match["player1_source_match_id"] == match_id else "player2_id"
                    self.db.execute(f"UPDATE se_matches SET {column} = ? WHERE guild_id = ? AND match_id = ?", (winner_id, guild_id, next_match_id))
                    updated_next_match = self.db.fetchone("SELECT * FROM se_matches WHERE guild_id = ? AND match_id = ?", (guild_id, next_match_id))
                    if updated_next_match["player1_id"] and updated_next_match["player2_id"]:
                        async def get_p_obj(pid):
                            d = self.db.fetchone("SELECT display_name, is_dummy FROM players WHERE guild_id = ? AND user_id = ?", (guild_id, pid))
                            if d['is_dummy']: obj = DummyPlayer(d['display_name']); obj.id=pid; return obj
                            return await self.bot.fetch_user(pid)
                        p1, p2 = await get_p_obj(updated_next_match["player1_id"]), await get_p_obj(updated_next_match["player2_id"])
                        embed = discord.Embed(title=f"【第{updated_next_match['round_num']}回戦】 {p1.display_name} vs {p2.display_name}", description="対戦の準備ができました。"); view = ResultReportView(guild_id, next_match_id, Player(p1), Player(p2))
                        await match_ch.send(embed=embed, view=view)
                        if main_ch.id != match_ch.id: await main_ch.send(f"新しい対戦カードが <#{match_ch.id}> に作成されました。")

                image_file = await self.bot.loop.run_in_executor(None, create_bracket_image_from_db, guild_id, self.db)
                if image_file:
                    tourney_info = self.db.fetchone("SELECT bracket_message_id FROM se_tournaments WHERE guild_id = ?", (guild_id,))
                    try:
                        msg_to_edit = await main_ch.fetch_message(tourney_info["bracket_message_id"])
                        await msg_to_edit.edit(content="**トーナメント表更新**", attachments=[image_file])
                    except (discord.NotFound, discord.Forbidden):
                        new_msg = await main_ch.send("**トーナメント表更新**", file=image_file)
                        self.db.execute("UPDATE se_tournaments SET bracket_message_id = ? WHERE guild_id = ?", (new_msg.id, guild_id))
                
                if not next_match:
                    winner_name = p_map.get(winner_id, '不明')
                    await main_ch.send(f"🎉 **優勝者決定！** 🎉\n**{winner_name}** さんの優勝です！おめでとうございます！")
                    self.db.execute("UPDATE se_tournaments SET is_active = 0 WHERE guild_id = ?", (guild_id,))
            except Exception as e:
                log.error(f"勝利処理中にエラーが発生(Guild: {guild_id}, Match: {match_id}): {e}", exc_info=True)
                await interaction.followup.send(f"エラーが発生しました。Botの管理者にご連絡ください。", ephemeral=True)

    @commands.command(name='中止', aliases=['sd中止'], help='進行中の募集や大会を強制的に中止します。')
    @commands.has_permissions(manage_guild=True)
    async def cancel_command(self, ctx: commands.Context):
        view = ConfirmCancelView(self)
        msg = await ctx.send("本当に現在の募集または大会を中止しますか？この操作は取り消せません。", view=view)
        view.message = msg

    async def _execute_cancel(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        if guild_id in self.recruit_sessions:
            await self._close_recruitment(interaction, "募集が中止されました。")
        if guild_id in self.swiss_tournaments:
            self.db.execute("DELETE FROM tournaments WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM current_pairings WHERE guild_id = ?", (guild_id,))
            self.db.execute("DELETE FROM reported_matches WHERE guild_id = ?", (guild_id,))
            self.swiss_tournaments.pop(guild_id, None)
        self.db.execute("DELETE FROM se_matches WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM se_tournaments WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM players WHERE guild_id = ?", (guild_id,))
        await interaction.message.edit(content="🚨 募集または大会を中止し、データをリセットしました。", view=None)

    @commands.command(name='ヘルプ3', aliases=['sdヘルプ'], help='大会・チーム分け機能の詳細なヘルプを表示します。')
    async def tournament_help(self, ctx: commands.Context):
        embed = discord.Embed(title="⚔️ 大会・チーム分け機能 詳細ヘルプ", description="イベントの開催から終了までの流れと、各機能の使い方を説明します。", color=0x4caf50)
        embed.add_field(name="【ステップ1】参加者を募集する", value="**1. `!募集` コマンドを実行します。**\n   - Botが参加者募集用のメッセージと操作パネルを投稿します。\n**2. 参加者は👍リアクションを押します。**\n   - 参加をキャンセルする場合はリアクションを外します。", inline=False)
        embed.add_field(name="【ステップ2】イベントを開始する", value="操作パネルのボタンを押して、希望のイベントを開始します。\n\n**< トーナメント / スイスドロー >**\n- ボタンを押すと大会が開始され、対戦カードが投稿されます。\n- 試合が終わったら、勝者が自分の勝利ボタンを押します。\n- トーナメントは勝者が次の試合へ、スイスドローは全員が次のラウンドへ進みます。", inline=False)
        embed.add_field(name="【その他コマンド】(要管理者権限)", value="**`!追加 [人数]`**: 募集中にダミー参加者を追加します。\n**`!削除 [人数]`**: 募集中にダミー参加者を削除します。\n**`!中止`**: 進行中の募集や大会を強制的に中止します。\n**`!sd設定メイン`**: 大会アナウンス用チャンネルを設定します。\n**`!sd設定対戦`**: **トーナメントとスイスドロー**の対戦カードを投稿するチャンネルを設定します。", inline=False)
        embed.set_footer(text="Botが再起動してもスイスドロー大会は自動で復元されます。")
        await ctx.send(embed=embed)

# CogをBotに登録するための必須の関数
async def setup(bot):
    await bot.add_cog(EventManagerCog(bot))
