# cogs/roundrobin/rr_handlers.py
import discord
import logging
from typing import TYPE_CHECKING

from . import rr_roles
from . import rr_logic

if TYPE_CHECKING:
    from .cog import RoundRobinCog

log = logging.getLogger(__name__)

async def handle_create_team(cog: "RoundRobinCog", interaction: discord.Interaction, team_name: str):
    guild_id = interaction.guild.id
    user = interaction.user

    if cog.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)):
        return await interaction.followup.send("エラー: 既に大会が進行中です。チームの作成はできません。", ephemeral=True)
    if cog.db.fetchone("SELECT 1 FROM rr_teams WHERE guild_id = ? AND name = ?", (guild_id, team_name)):
        return await interaction.followup.send(f"エラー: チーム名「{team_name}」は既に使用されています。", ephemeral=True)
    if cog.db.fetchone("SELECT 1 FROM rr_players WHERE guild_id = ? AND user_id = ?", (guild_id, user.id)):
        return await interaction.followup.send(f"エラー: あなたは既に別のチームに登録されています。", ephemeral=True)

    team_id = cog.db.execute("INSERT INTO rr_teams (guild_id, name) VALUES (?, ?)", (guild_id, team_name), return_lastrowid=True)
    cog.db.execute("INSERT INTO rr_players (user_id, guild_id, team_id, display_name, is_dummy, position) VALUES (?, ?, ?, ?, ?, ?)",
                    (user.id, guild_id, team_id, user.display_name, False, 1))
    await rr_roles.assign_roles_on_join(cog, user, team_id)
    await interaction.followup.send(f"✅ チーム「**{team_name}**」を作成しました！あなたがリーダーです。", ephemeral=True)
    await rr_logic.update_registration_panel(cog, guild_id)

async def handle_join_team(cog: "RoundRobinCog", interaction: discord.Interaction, team_id: int):
    guild_id = interaction.guild.id
    user = interaction.user

    if cog.db.fetchone("SELECT 1 FROM rr_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)):
        return await interaction.followup.send("エラー: 既に大会が進行中です。チームへの参加はできません。", ephemeral=True)
    if cog.db.fetchone("SELECT 1 FROM rr_players WHERE guild_id = ? AND user_id = ?", (guild_id, user.id)):
        return await interaction.followup.send(f"エラー: あなたは既に別のチームに登録されています。", ephemeral=True)
    
    team = cog.db.fetchone("SELECT name FROM rr_teams WHERE team_id = ?", (team_id,))
    if not team:
        return await interaction.followup.send("エラー: 選択されたチームが見つかりません。", ephemeral=True)

    max_pos = cog.db.fetchone("SELECT MAX(position) as max_p FROM rr_players WHERE team_id = ?", (team_id,))['max_p'] or 0
    
    cog.db.execute("INSERT INTO rr_players (user_id, guild_id, team_id, display_name, is_dummy, position) VALUES (?, ?, ?, ?, ?, ?)",
                    (user.id, guild_id, team_id, user.display_name, False, max_pos + 1))
    await rr_roles.assign_roles_on_join(cog, user, team_id)
    await interaction.followup.send(f"✅ チーム「**{team['name']}**」に参加しました！", ephemeral=True)
    await rr_logic.update_registration_panel(cog, guild_id)

async def handle_leave_team(cog: "RoundRobinCog", interaction: discord.Interaction):
    guild_id = interaction.guild.id
    user = interaction.user

    player_info = cog.db.fetchone("SELECT p.team_id, t.name FROM rr_players p JOIN rr_teams t ON p.team_id = t.team_id WHERE p.user_id = ? AND p.guild_id = ?", (user.id, guild_id))
    if not player_info:
        return await interaction.followup.send("エラー: あなたはどのチームにも参加していません。", ephemeral=True)

    team_id = player_info['team_id']
    team_name = player_info['name']
    
    await rr_roles.remove_roles_on_leave(cog, user, team_id)
    cog.db.execute("DELETE FROM rr_players WHERE user_id = ? AND guild_id = ?", (user.id, guild_id))

    remaining_players = cog.db.fetchone("SELECT COUNT(*) as c FROM rr_players WHERE team_id = ?", (team_id,))['c']
    if remaining_players == 0:
        team_data = cog.db.fetchone("SELECT role_id FROM rr_teams WHERE team_id = ?", (team_id,))
        if team_data and team_data['role_id']:
            role = interaction.guild.get_role(team_data['role_id'])
            if role:
                try:
                    await role.delete(reason=f"チーム '{team_name}' が解散したため")
                except discord.Forbidden:
                    log.warning(f"チームロール '{role.name}' の削除に失敗しました。")
        cog.db.execute("DELETE FROM rr_teams WHERE team_id = ?", (team_id,))
        await interaction.followup.send(f"👋 チーム「**{team_name}**」から脱退しました。チームはメンバーがいなくなったため解散・ロール削除されました。", ephemeral=True)
    else:
        await interaction.followup.send(f"👋 チーム「**{team_name}**」から脱退しました。", ephemeral=True)
    await rr_logic.update_registration_panel(cog, guild_id)

async def handle_score_report(cog: "RoundRobinCog", interaction: discord.Interaction, message_id: int, match_id: int, team1_id: int, team2_id: int, new_team1_score: int, new_team2_score: int):
    guild_id = interaction.guild.id
    target_channel = await cog._get_rr_channel(interaction)

    match_data = cog.db.fetchone("SELECT team1_score, team2_score FROM rr_matches WHERE match_id = ?", (match_id,))
    is_correction = match_data and match_data['team1_score'] is not None

    if is_correction:
        old_team1_score, old_team2_score = match_data['team1_score'], match_data['team2_score']
        cog.db.execute("UPDATE rr_teams SET score_for = score_for - ? WHERE team_id = ?", (old_team1_score, team1_id))
        cog.db.execute("UPDATE rr_teams SET score_for = score_for - ? WHERE team_id = ?", (old_team2_score, team2_id))
        old_winner, old_loser = (team1_id, team2_id) if old_team1_score > old_team2_score else ((team2_id, team1_id) if old_team2_score > old_team1_score else (None, None))
        if old_winner:
            cog.db.execute("UPDATE rr_teams SET wins = wins - 1 WHERE team_id = ?", (old_winner,))
            cog.db.execute("UPDATE rr_teams SET losses = losses - 1 WHERE team_id = ?", (old_loser,))

    cog.db.execute("UPDATE rr_teams SET score_for = score_for + ? WHERE team_id = ?", (new_team1_score, team1_id))
    cog.db.execute("UPDATE rr_teams SET score_for = score_for + ? WHERE team_id = ?", (new_team2_score, team2_id))
    new_winner, new_loser = (team1_id, team2_id) if new_team1_score > new_team2_score else ((team2_id, team1_id) if new_team2_score > new_team1_score else (None, None))
    if new_winner:
        cog.db.execute("UPDATE rr_teams SET wins = wins + 1 WHERE team_id = ?", (new_winner,))
        cog.db.execute("UPDATE rr_teams SET losses = losses + 1 WHERE team_id = ?", (new_loser,))

    cog.db.execute("UPDATE rr_matches SET team1_score = ?, team2_score = ?, status = 'reported' WHERE match_id = ?", (new_team1_score, new_team2_score, match_id))
    
    try:
        from .rr_views import CorrectResultView
        message = await target_channel.fetch_message(message_id)
        team1_name, team2_name = cog.db.fetchone("SELECT name FROM rr_teams WHERE team_id = ?", (team1_id,))['name'], cog.db.fetchone("SELECT name FROM rr_teams WHERE team_id = ?", (team2_id,))['name']
        
        embed = message.embeds[0]
        embed.title = f"試合結果: {team1_name} {new_team1_score} - {new_team2_score} {team2_name}"
        embed.description = f"勝利チーム: **{team1_name if new_winner == team1_id else team2_name}**" if new_winner else "引き分け"
        embed.color = discord.Color.green()
        
        await message.edit(embed=embed, view=CorrectResultView())
    except (discord.NotFound, discord.Forbidden) as e:
        log.error(f"結果報告メッセージの編集に失敗: {e}")

    await interaction.followup.send("結果を訂正しました！" if is_correction else "結果を記録しました！", ephemeral=True)
    await rr_logic.send_match_schedule(cog, target_channel, is_update=True)
    
    tourney_info = cog.db.fetchone("SELECT current_round FROM rr_tournaments WHERE guild_id = ?", (guild_id,))
    if not tourney_info: return
    current_round = tourney_info['current_round']
    pending_matches = cog.db.fetchone("SELECT COUNT(*) as count FROM rr_matches WHERE guild_id = ? AND round_num = ? AND status = 'pending'", (guild_id, current_round))['count']
    if pending_matches == 0:
        from .rr_views import NextRoundViewRR
        view = NextRoundViewRR()
        await target_channel.send(f"**第 {current_round} 節** の全試合結果が報告されました。", view=view)
