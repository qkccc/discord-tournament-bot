# cogs/roundrobin/rr_logic.py
import discord
import logging
import io
from PIL import Image, ImageDraw, ImageFont
import functools
import random
from typing import List, Tuple, TYPE_CHECKING

from .rr_models import RR_Team

if TYPE_CHECKING:
    from .cog import RoundRobinCog

log = logging.getLogger(__name__)

def generate_round_robin_schedule(teams: List[RR_Team]) -> List[Tuple[int, RR_Team, RR_Team]]:
    schedule = []
    local_teams = list(teams)
    if len(local_teams) % 2 != 0:
        local_teams.append(RR_Team(id=None, name="BYE"))
    num_teams = len(local_teams)
    num_rounds = num_teams - 1
    for r in range(num_rounds):
        for i in range(num_teams // 2):
            team1 = local_teams[i]
            team2 = local_teams[num_teams - 1 - i]
            if team1.id is not None and team2.id is not None:
                schedule.append((r + 1, team1, team2))
        local_teams.insert(1, local_teams.pop())
    return schedule

def generate_schedule_image(teams_data, matches_data, current_round: int):
    cell_size = 100; top_header_height = 100; left_header_width = 250
    margin = 20; font_size_header = 24; font_size_cell = 30
    num_teams = len(teams_data)
    image_width = left_header_width + cell_size * num_teams + margin * 2
    image_height = top_header_height + cell_size * num_teams + margin * 2
    try:
        font_header_base = ImageFont.truetype("assets/meiryo.ttc", font_size_header, index=0)
        font_cell = ImageFont.truetype("assets/meiryo.ttc", font_size_cell, index=0)
    except IOError:
        log.error("フォントファイル 'meiryo.ttc' が見つかりません。")
        font_header_base = ImageFont.load_default(); font_cell = ImageFont.load_default()
    img = Image.new('RGB', (image_width, image_height), (255, 255, 255)); draw = ImageDraw.Draw(img)
    for i in range(num_teams + 1):
        x = margin + left_header_width + i * cell_size; draw.line([(x, margin), (x, image_height - margin)], fill=(192, 192, 192), width=2)
        y = margin + top_header_height + i * cell_size; draw.line([(margin, y), (image_width - margin, y)], fill=(192, 192, 192), width=2)
    for i, team in enumerate(teams_data):
        header_num_str = str(i + 1)
        text_bbox = draw.textbbox((0,0), header_num_str, font=font_header_base); text_width, text_height = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
        draw.text((margin + left_header_width + i * cell_size + (cell_size - text_width) / 2, margin + (top_header_height - text_height) / 2), header_num_str, font=font_header_base, fill=(0, 0, 0))
        team_name_str = f"{i + 1}. {team['name']}"
        current_font_size = font_size_header; temp_font = font_header_base
        while draw.textbbox((0,0), team_name_str, font=temp_font)[2] > left_header_width - 20 and current_font_size > 10:
            current_font_size -= 1
            try: temp_font = ImageFont.truetype("assets/meiryo.ttc", current_font_size, index=0)
            except IOError: temp_font = ImageFont.load_default()
        text_bbox = draw.textbbox((0,0), team_name_str, font=temp_font); text_height = text_bbox[3] - text_bbox[1]
        draw.text((margin + 10, margin + top_header_height + i * cell_size + (cell_size - text_height) / 2), team_name_str, font=temp_font, fill=(0, 0, 0))
    for r, row_team in enumerate(teams_data):
        for c, col_team in enumerate(teams_data):
            x_start = margin + left_header_width + c * cell_size; y_start = margin + top_header_height + r * cell_size
            if row_team['team_id'] == col_team['team_id']:
                draw.rectangle([x_start, y_start, x_start + cell_size, y_start + cell_size], fill=(220, 220, 220)); continue
            match = next((m for m in matches_data if {m['team1_id'], m['team2_id']} == {row_team['team_id'], col_team['team_id']}), None)
            cell_str = ""
            if match and match['round_num'] <= current_round:
                if match['status'] == 'reported':
                    score_row = match['team1_score'] if match['team1_id'] == row_team['team_id'] else match['team2_score']
                    score_col = match['team2_score'] if match['team1_id'] == row_team['team_id'] else match['team1_score']
                    cell_str = f"{score_row} - {score_col}"
                else: cell_str = "vs"
            text_bbox = draw.textbbox((0,0), cell_str, font=font_cell); text_width, text_height = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
            draw.text((x_start + (cell_size - text_width) / 2, y_start + (cell_size - text_height) / 2), cell_str, font=font_cell, fill=(0, 0, 0))
    buffer = io.BytesIO(); img.save(buffer, format='PNG'); buffer.seek(0); return buffer

async def generate_registration_embed(cog: "RoundRobinCog", guild_id: int) -> discord.Embed:
    """現在の登録状況からEmbedを生成する"""
    embed = discord.Embed(
        title="⚔️ 総当たり戦 参加登録 ⚔️",
        description="下のボタンからチームの作成、参加、脱退ができます。\n\n**現在の登録状況:**",
        color=discord.Color.blue()
    )
    teams = cog.db.fetchall("SELECT team_id, name FROM rr_teams WHERE guild_id = ? ORDER BY name", (guild_id,))
    if not teams:
        embed.description += "\nまだチームが登録されていません。"
        return embed
    team_list_str = ""
    total_players = 0
    for team in teams:
        players = cog.db.fetchall("SELECT display_name FROM rr_players WHERE team_id = ? ORDER BY position", (team['team_id'],))
        player_names = [p['display_name'] for p in players]
        num_players = len(player_names)
        total_players += num_players
        team_list_str += f"\n**{team['name']}** ({num_players}名)\n"
        if player_names:
            team_list_str += "└ " + ", ".join(player_names)
        else:
            team_list_str += "└ メンバーがいません"
    embed.description += f" ({len(teams)}チーム, {total_players}名参加中)"
    if len(embed.description) + len(team_list_str) > 4000:
        embed.add_field(name="登録チーム", value="チーム数が多すぎるため表示できません。", inline=False)
    else:
        embed.description += team_list_str
    return embed

async def update_registration_panel(cog: "RoundRobinCog", guild_id: int):
    """登録パネルを最新の情報に更新する"""
    config = cog.db.fetchone("SELECT panel_message_id, panel_channel_id FROM rr_config WHERE guild_id = ?", (guild_id,))
    if not config or not config['panel_message_id'] or not config['panel_channel_id']:
        return
    try:
        channel = await cog.bot.fetch_channel(config['panel_channel_id'])
        message = await channel.fetch_message(config['panel_message_id'])
        new_embed = await generate_registration_embed(cog, guild_id)
        await message.edit(embed=new_embed)
    except (discord.NotFound, discord.Forbidden) as e:
        log.warning(f"登録パネルの更新に失敗しました (Guild: {guild_id}): {e}")
        cog.db.execute("UPDATE rr_config SET panel_message_id = NULL, panel_channel_id = NULL WHERE guild_id = ?", (guild_id,))

async def display_standings(cog: "RoundRobinCog", channel: discord.TextChannel):
    guild_id = channel.guild.id
    teams_data = cog.db.fetchall("SELECT team_id, name, wins, losses, score_for FROM rr_teams WHERE guild_id = ?", (guild_id,))
    if not teams_data:
        embed = discord.Embed(title="🏆 順位表 🏆", description="まだチームが登録されていません。", color=discord.Color.purple())
        return await channel.send(embed=embed)
    matches_data = cog.db.fetchall("SELECT * FROM rr_matches WHERE guild_id = ? AND status = 'reported'", (guild_id,))
    match_results = {}
    for match in matches_data:
        key = tuple(sorted((match['team1_id'], match['team2_id'])))
        winner_id = None
        if match['team1_score'] > match['team2_score']: winner_id = match['team1_id']
        elif match['team2_score'] > match['team1_score']: winner_id = match['team2_id']
        match_results[key] = winner_id
    def compare_teams(team1, team2):
        if team1['wins'] != team2['wins']: return team2['wins'] - team1['wins']
        if team1['score_for'] != team2['score_for']: return team2['score_for'] - team1['score_for']
        key = tuple(sorted((team1['team_id'], team2['team_id'])))
        if key in match_results:
            winner_id = match_results[key]
            if winner_id == team1['team_id']: return -1
            if winner_id == team2['team_id']: return 1
        return 0
    teams_list = [dict(t) for t in teams_data]
    sorted_teams = sorted(teams_list, key=functools.cmp_to_key(compare_teams))
    embed = discord.Embed(title="🏆 順位表 🏆", color=discord.Color.purple())
    description = ""
    for i, team in enumerate(sorted_teams):
        description += f"**{i+1}位**: {team['name']} ({team['wins']}勝 {team['losses']}敗 / 総得点: {team['score_for']})\n"
    embed.description = description or "まだ試合結果がありません。"
    await channel.send(embed=embed)

async def send_match_schedule(cog: "RoundRobinCog", channel: discord.TextChannel, is_update: bool = False):
    guild_id = channel.guild.id
    teams_data = cog.db.fetchall("SELECT team_id, name FROM rr_teams WHERE guild_id = ? ORDER BY name", (guild_id,))
    matches_data = cog.db.fetchall("SELECT * FROM rr_matches WHERE guild_id = ?", (guild_id,))
    tourney_info = cog.db.fetchone("SELECT message_id, current_round FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
    if not tourney_info:
        log.warning(f"Guild {guild_id}: send_match_scheduleが呼び出されましたが、大会情報が見つかりません。")
        return
    if not teams_data:
        embed = discord.Embed(title="対戦表", description="登録されているチームがありません。", color=discord.Color.dark_teal())
        return await channel.send(embed=embed)
    current_round = tourney_info['current_round']
    image_buffer = generate_schedule_image(teams_data, matches_data, current_round)
    file = discord.File(fp=image_buffer, filename="schedule.png")
    embed = discord.Embed(title="対戦表", color=discord.Color.dark_teal()); embed.set_image(url="attachment://schedule.png")
    msg_id = tourney_info['message_id']
    if is_update and msg_id:
        try:
            msg = await channel.fetch_message(msg_id); await msg.edit(embed=embed, attachments=[file]); return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            log.warning(f"対戦表メッセージの編集に失敗({e})。新しいメッセージを送信します。"); msg_id = None
    msg = await channel.send(embed=embed, file=file)
    cog.db.execute("UPDATE rr_tournaments SET message_id = ? WHERE guild_id = ?", (msg.id, guild_id))

async def finish_tournament(cog: "RoundRobinCog", channel: discord.TextChannel):
    guild_id = channel.guild.id
    guild = channel.guild
    
    # --- 最終結果画像の生成と送信 ---
    teams_data = cog.db.fetchall("SELECT team_id, name FROM rr_teams WHERE guild_id = ? ORDER BY name", (guild_id,))
    matches_data = cog.db.fetchall("SELECT * FROM rr_matches WHERE guild_id = ?", (guild_id,))
    tourney_info = cog.db.fetchone("SELECT current_round FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
    
    if teams_data and tourney_info:
        current_round = tourney_info['current_round']
        image_buffer = generate_schedule_image(teams_data, matches_data, current_round)
        file = discord.File(fp=image_buffer, filename="schedule_final.png")
        
        # 最終順位の計算
        match_results = {}
        for match in matches_data:
            if match['status'] != 'reported': continue
            key = tuple(sorted((match['team1_id'], match['team2_id'])))
            winner_id = None
            if match['team1_score'] > match['team2_score']: winner_id = match['team1_id']
            elif match['team2_score'] > match['team1_score']: winner_id = match['team2_id']
            match_results[key] = winner_id

        teams_list = []
        for t in teams_data:
            # 最新の勝敗数を再取得（念のため）
            stats = cog.db.fetchone("SELECT wins, losses, score_for FROM rr_teams WHERE team_id = ?", (t['team_id'],))
            t_dict = dict(t)
            t_dict.update(stats)
            teams_list.append(t_dict)

        def compare_teams(team1, team2):
            if team1['wins'] != team2['wins']: return team2['wins'] - team1['wins']
            if team1['score_for'] != team2['score_for']: return team2['score_for'] - team1['score_for']
            key = tuple(sorted((team1['team_id'], team2['team_id'])))
            if key in match_results:
                winner_id = match_results[key]
                if winner_id == team1['team_id']: return -1
                if winner_id == team2['team_id']: return 1
            return 0

        sorted_teams = sorted(teams_list, key=functools.cmp_to_key(compare_teams))
        
        # ▼▼▼ 変更点: 優勝チームのメンバー表示機能 ▼▼▼
        winner_team = sorted_teams[0]
        winner_members = cog.db.fetchall("SELECT display_name FROM rr_players WHERE team_id = ?", (winner_team['team_id'],))
        member_names = ", ".join([m['display_name'] for m in winner_members])

        embed = discord.Embed(title="🏆 全試合終了！ 最終結果発表 🏆", color=discord.Color.gold())
        embed.set_image(url="attachment://schedule_final.png")
        
        embed.add_field(
            name=f"👑 優勝: {winner_team['name']}",
            value=f"**メンバー:** {member_names}\n**戦績:** {winner_team['wins']}勝 {winner_team['losses']}敗 (総得点: {winner_team['score_for']})",
            inline=False
        )
        
        # 2位以下の表示
        sub_standings = ""
        for i, team in enumerate(sorted_teams[1:], start=2):
            sub_standings += f"**{i}位**: {team['name']} ({team['wins']}勝 {team['losses']}敗)\n"
        if sub_standings:
            embed.add_field(name="順位表", value=sub_standings, inline=False)

        await channel.send(embed=embed, file=file)
        # ▲▲▲ 変更ここまで ▲▲▲

    # --- ロール削除と終了処理 ---
    teams_with_roles = cog.db.fetchall("SELECT role_id FROM rr_teams WHERE guild_id = ? AND role_id IS NOT NULL", (guild_id,))
    for team in teams_with_roles:
        role = guild.get_role(team['role_id'])
        if role: 
            try: await role.delete(reason="総当たり戦が終了したため")
            except: pass
            
    config = cog.db.fetchone("SELECT role_id FROM rr_config WHERE guild_id = ?", (guild_id,))
    if config and config['role_id']:
        role = guild.get_role(config['role_id'])
        if role: 
            try: await role.delete(reason="総当たり戦が終了したため")
            except: pass
            
    cog.db.execute("UPDATE rr_tournaments SET is_active = 0 WHERE guild_id = ?", (guild_id,))

async def execute_next_round(cog: "RoundRobinCog", ctx_or_interaction):
    if isinstance(ctx_or_interaction, discord.Interaction): await ctx_or_interaction.response.defer()
    guild_id = ctx_or_interaction.guild.id; target_channel = await cog._get_rr_channel(ctx_or_interaction)
    tourney_info = cog.db.fetchone("SELECT current_round FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
    if not tourney_info: return
    current_round = tourney_info['current_round']
    pending_count = cog.db.fetchone("SELECT COUNT(*) as count FROM rr_matches WHERE guild_id = ? AND round_num = ? AND status = 'pending'", (guild_id, current_round))['count']
    if pending_count > 0:
        msg = f"まだ第 {current_round} 節の試合が {pending_count} 件報告されていません。"
        if isinstance(ctx_or_interaction, discord.Interaction): return await ctx_or_interaction.followup.send(msg, ephemeral=True)
        else: return await ctx_or_interaction.send(msg)
    completed_matches = cog.db.fetchall("SELECT message_id FROM rr_matches WHERE guild_id = ? AND round_num = ?", (guild_id, current_round))
    for match in completed_matches:
        if match['message_id']:
            try:
                msg = await target_channel.fetch_message(match['message_id'])
                await msg.edit(view=None)
            except (discord.NotFound, discord.Forbidden): pass
    next_round = current_round + 1
    if not cog.db.fetchone("SELECT 1 FROM rr_matches WHERE guild_id = ? AND round_num = ?", (guild_id, next_round)):
        await finish_tournament(cog, target_channel)
    else:
        cog.db.execute("UPDATE rr_tournaments SET current_round = ? WHERE guild_id = ?", (next_round, guild_id))
        await send_match_schedule(cog, target_channel, is_update=False)
        await send_round_match_cards(cog, target_channel, next_round)

async def send_round_match_cards(cog: "RoundRobinCog", channel: discord.TextChannel, round_num: int):
    guild_id = channel.guild.id
    matches = cog.db.fetchall("SELECT * FROM rr_matches WHERE guild_id = ? AND round_num = ? AND status = 'pending'", (guild_id, round_num))
    if not matches: return
    from .rr_views import ReportResultView
    tourney_info = cog.db.fetchone("SELECT member_order, participant_role_id FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,))
    role_id = tourney_info['participant_role_id'] if tourney_info else None
    role_mention = f"<@&{role_id}>" if role_id else ""
    await channel.send(f"**--- 第 {round_num} 節 ---**\n{role_mention}")
    view = ReportResultView()
    for match in matches:
        team1_name = cog.db.fetchone("SELECT name FROM rr_teams WHERE team_id = ?", (match['team1_id'],))['name']
        team2_name = cog.db.fetchone("SELECT name FROM rr_teams WHERE team_id = ?", (match['team2_id'],))['name']
        member_order = tourney_info['member_order'] if tourney_info else 'random'
        team1_players_data = cog.db.fetchall("SELECT display_name FROM rr_players WHERE team_id = ? ORDER BY position ASC", (match['team1_id'],))
        team2_players_data = cog.db.fetchall("SELECT display_name FROM rr_players WHERE team_id = ? ORDER BY position ASC", (match['team2_id'],))
        team1_members = [p['display_name'] for p in team1_players_data]
        team2_members = [p['display_name'] for p in team2_players_data]
        if member_order == 'random':
            random.shuffle(team1_members)
            random.shuffle(team2_members)
        team1_members_str = "\n".join(team1_members) or "メンバーなし"
        team2_members_str = "\n".join(team2_members) or "メンバーなし"
        embed = discord.Embed(title=f"対戦: {team1_name} vs {team2_name}", description="対戦終了後、代表者が「結果報告」ボタンを押してください。", color=discord.Color.gold())
        embed.add_field(name=f"**{team1_name}**", value=team1_members_str, inline=True)
        embed.add_field(name=f"**{team2_name}**", value=team2_members_str, inline=True)
        msg = await channel.send(embed=embed, view=view)
        cog.db.execute("UPDATE rr_matches SET message_id = ? WHERE match_id = ?", (msg.id, match['match_id']))

async def get_joinable_teams(cog: "RoundRobinCog", guild_id: int) -> List[dict]:
    """参加可能なチームのリストを取得する"""
    teams = cog.db.fetchall("SELECT team_id, name FROM rr_teams WHERE guild_id = ?", (guild_id,))
    max_players_hard_limit = 5 
    joinable_teams = []
    for team in teams:
        count = cog.db.fetchone("SELECT COUNT(*) as c FROM rr_players WHERE team_id = ?", (team['team_id'],))['c']
        if count < max_players_hard_limit:
            joinable_teams.append(team)
    return joinable_teams
