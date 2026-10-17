# cogs/events/league/league_handlers.py
"""リーグ戦の対戦カード(1枚のメッセージ)の状態遷移(pending→承認待ち→確定)を処理する。"""
import logging
from typing import TYPE_CHECKING

import discord

from . import league_logic

if TYPE_CHECKING:
    from .cog import LeagueCog

log = logging.getLogger(__name__)


async def _get_channels(cog: "LeagueCog", guild_id: int, fallback: discord.TextChannel):
    """大会機能(event_settings)と共通のチャンネル設定を使う。"""
    event_manager_cog = cog.bot.get_cog("EventManager")
    if event_manager_cog:
        main_ch, match_ch = await event_manager_cog._get_channels(guild_id)
        return (main_ch or fallback), (match_ch or fallback)
    return fallback, fallback


async def handle_declare_winner(
    cog: "LeagueCog", interaction: discord.Interaction, message_id: int, declared_winner_id: int
):
    match = cog.db.fetchone(
        "SELECT match_id, status, player1_id, player2_id FROM league_matches WHERE message_id = ?",
        (message_id,),
    )
    if not match:
        return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
    if match["status"] != "pending":
        return await interaction.response.send_message("この対戦は既に報告済みです。", ephemeral=True)

    if interaction.user.id not in (match["player1_id"], match["player2_id"]) and not (
        interaction.user.guild_permissions and interaction.user.guild_permissions.manage_guild
    ):
        return await interaction.response.send_message("対戦者本人か管理者のみ結果を申告できます。", ephemeral=True)

    cog.db.execute(
        "UPDATE league_matches SET winner_id = ?, status = 'awaiting_approval', reporter_id = ? WHERE match_id = ?",
        (declared_winner_id, interaction.user.id, match["match_id"]),
    )

    winner = await league_logic.resolve_league_player(cog, interaction.guild.id, declared_winner_id)
    winner_name = winner.display_name if winner else "???"

    embed = discord.Embed(
        title="対戦結果の承認待ち",
        description=f"**{winner_name}** の勝利が申告されました。対戦相手は内容を確認して承認してください。",
        color=discord.Color.orange(),
    )
    from .league_views import LeagueApprovalView

    await interaction.response.edit_message(embed=embed, view=LeagueApprovalView(match["match_id"]))


async def handle_approve(cog: "LeagueCog", interaction: discord.Interaction, message_id: int):
    match = cog.db.fetchone(
        "SELECT match_id, group_id, status, player1_id, player2_id, winner_id, reporter_id "
        "FROM league_matches WHERE message_id = ?",
        (message_id,),
    )
    if not match:
        return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
    if match["status"] != "awaiting_approval":
        return await interaction.response.send_message("この対戦は承認待ち状態ではありません。", ephemeral=True)

    # 対戦者(player1/player2)のいずれか、かつ「申告した本人ではない」ことを要求する。
    # 管理者が代理で申告したケース(reporter_idが両選手のどちらでもない)では、
    # 選手のどちらが承認してもよい。管理者は自己申告分も含め常に承認できる。
    is_admin = bool(
        interaction.user.guild_permissions and interaction.user.guild_permissions.manage_guild
    )
    is_participant = interaction.user.id in (match["player1_id"], match["player2_id"])
    is_reporter = interaction.user.id == match["reporter_id"]
    if is_reporter and not is_admin:
        return await interaction.response.send_message(
            "❌ 申告した本人は承認できません。対戦相手か管理者による承認が必要です。", ephemeral=True
        )
    if not (is_participant or is_admin):
        return await interaction.response.send_message(
            "❌ 対戦相手本人か管理者のみが承認できます。", ephemeral=True
        )

    await interaction.response.defer()

    winner_id = match["winner_id"]
    loser_id = match["player2_id"] if winner_id == match["player1_id"] else match["player1_id"]
    guild_id = interaction.guild.id

    cog.db.execute(
        "UPDATE league_players SET wins = wins + 1 WHERE guild_id = ? AND user_id = ?",
        (guild_id, winner_id),
    )
    cog.db.execute(
        "UPDATE league_players SET losses = losses + 1 WHERE guild_id = ? AND user_id = ?",
        (guild_id, loser_id),
    )
    cog.db.execute(
        "UPDATE league_matches SET status = 'confirmed' WHERE match_id = ?", (match["match_id"],)
    )

    winner = await league_logic.resolve_league_player(cog, guild_id, winner_id)
    winner_name = winner.display_name if winner else "???"
    embed = discord.Embed(
        title="試合結果確定",
        description=f"✅ **{winner_name}** の勝利で確定しました。",
        color=discord.Color.green(),
    )
    from .league_views import LeagueCorrectView

    await interaction.edit_original_response(embed=embed, view=LeagueCorrectView(match["match_id"]))

    main_ch, match_ch = await _get_channels(cog, guild_id, interaction.channel)
    await league_logic.refresh_standings_image(cog, interaction.guild, main_ch)

    if await league_logic.all_matches_confirmed(cog, guild_id):
        from .league_views import LeaguePromoteView

        await main_ch.send(
            "🏁 全グループの試合が終了しました！ 上位進出者を決めてトーナメントへ移行できます。",
            view=LeaguePromoteView(),
        )


async def handle_reject(cog: "LeagueCog", interaction: discord.Interaction, message_id: int):
    match = cog.db.fetchone(
        "SELECT match_id, status, player1_id, player2_id, reporter_id FROM league_matches WHERE message_id = ?",
        (message_id,),
    )
    if not match:
        return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
    if match["status"] != "awaiting_approval":
        return await interaction.response.send_message("この対戦は承認待ち状態ではありません。", ephemeral=True)

    is_admin = bool(
        interaction.user.guild_permissions and interaction.user.guild_permissions.manage_guild
    )
    is_participant = interaction.user.id in (match["player1_id"], match["player2_id"])
    is_reporter = interaction.user.id == match["reporter_id"]
    if is_reporter and not is_admin:
        return await interaction.response.send_message(
            "❌ 申告した本人は却下できません。対戦相手か管理者による却下が必要です。", ephemeral=True
        )
    if not (is_participant or is_admin):
        return await interaction.response.send_message(
            "❌ 対戦相手本人か管理者のみが却下できます。", ephemeral=True
        )

    cog.db.execute(
        "UPDATE league_matches SET winner_id = NULL, status = 'pending', reporter_id = NULL WHERE match_id = ?",
        (match["match_id"],),
    )

    from .league_views import LeaguePendingView

    embed = league_logic.build_league_match_card_embed(cog, match["match_id"])
    p1 = await league_logic.resolve_league_player(cog, interaction.guild.id, match["player1_id"])
    p2 = await league_logic.resolve_league_player(cog, interaction.guild.id, match["player2_id"])
    view = LeaguePendingView(
        match["match_id"],
        p1.display_name if p1 else "???",
        p2.display_name if p2 else "???",
    )
    await interaction.response.edit_message(embed=embed, view=view)


async def handle_correct_start(cog: "LeagueCog", interaction: discord.Interaction, message_id: int):
    """確定済みの対戦結果を訂正する。既存の勝敗数を巻き戻してからpendingに差し戻す。"""
    match = cog.db.fetchone(
        "SELECT match_id, status, player1_id, player2_id, winner_id FROM league_matches WHERE message_id = ?",
        (message_id,),
    )
    if not match:
        return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
    if match["status"] != "confirmed":
        return await interaction.response.send_message("この対戦はまだ確定していません。", ephemeral=True)

    is_admin = bool(
        interaction.user.guild_permissions and interaction.user.guild_permissions.manage_guild
    )
    if interaction.user.id not in (match["player1_id"], match["player2_id"]) and not is_admin:
        return await interaction.response.send_message("対戦者本人か管理者のみ訂正できます。", ephemeral=True)

    guild_id = interaction.guild.id
    winner_id = match["winner_id"]
    loser_id = match["player2_id"] if winner_id == match["player1_id"] else match["player1_id"]

    cog.db.execute(
        "UPDATE league_players SET wins = wins - 1 WHERE guild_id = ? AND user_id = ?",
        (guild_id, winner_id),
    )
    cog.db.execute(
        "UPDATE league_players SET losses = losses - 1 WHERE guild_id = ? AND user_id = ?",
        (guild_id, loser_id),
    )
    cog.db.execute(
        "UPDATE league_matches SET winner_id = NULL, status = 'pending', reporter_id = NULL WHERE match_id = ?",
        (match["match_id"],),
    )

    from .league_views import LeaguePendingView

    embed = league_logic.build_league_match_card_embed(cog, match["match_id"])
    p1 = await league_logic.resolve_league_player(cog, guild_id, match["player1_id"])
    p2 = await league_logic.resolve_league_player(cog, guild_id, match["player2_id"])
    view = LeaguePendingView(
        match["match_id"],
        p1.display_name if p1 else "???",
        p2.display_name if p2 else "???",
    )
    await interaction.response.edit_message(embed=embed, view=view)

    main_ch, match_ch = await _get_channels(cog, guild_id, interaction.channel)
    await league_logic.refresh_standings_image(cog, interaction.guild, main_ch)
