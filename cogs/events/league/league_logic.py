# cogs/events/league/league_logic.py
"""個人戦リーグ戦(総当たり、複数グループ)の開始・進行・トーナメント移行ロジック。"""
import logging
import random
from typing import TYPE_CHECKING, List, Set, Tuple, Union

import discord

from cogs.events.event_manager.models import DummyPlayer
from cogs.events.roundrobin.rr_logic import generate_round_robin_schedule
from cogs.events.roundrobin.rr_models import RR_Team
from cogs.utils.ranking import rank_entries

from . import league_image

if TYPE_CHECKING:
    from .cog import LeagueCog

log = logging.getLogger(__name__)


def _split_into_groups(
    participants: List[Union[discord.Member, DummyPlayer]], num_groups: int
) -> List[List[Union[discord.Member, DummyPlayer]]]:
    """参加者をシャッフルしたうえで num_groups グループへほぼ均等に振り分ける
    （グループ間の人数差は最大1人）。"""
    shuffled = list(participants)
    random.shuffle(shuffled)
    groups: List[List] = [[] for _ in range(num_groups)]
    for i, p in enumerate(shuffled):
        groups[i % num_groups].append(p)
    return groups


async def start_league(
    cog: "LeagueCog",
    guild: discord.Guild,
    main_ch: discord.TextChannel,
    match_ch: discord.TextChannel,
    participants: Set[Union[discord.Member, DummyPlayer]],
    num_groups: int,
) -> Tuple[bool, str]:
    guild_id = guild.id

    async with cog.locks[guild_id]:
        if cog.db.fetchone(
            "SELECT 1 FROM league_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)
        ):
            return False, "既にリーグ戦が進行中です。`!リーグ中止` で一度リセットしてください。"

        if num_groups < 1 or num_groups > len(participants) // 2:
            return False, (
                f"グループ数が不正です。参加者{len(participants)}人に対して、"
                f"1〜{max(1, len(participants) // 2)}グループの範囲で指定してください"
                "（各グループ最低2人必要です）。"
            )

        # 前回の残骸があれば掃除してから作り直す
        cog.db.execute("DELETE FROM league_matches WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_players WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_groups WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_tournaments WHERE guild_id = ?", (guild_id,))
        cog.db.execute(
            "INSERT INTO league_tournaments (guild_id, is_active, num_groups) VALUES (?, ?, ?)",
            (guild_id, True, num_groups),
        )

        groups = _split_into_groups(list(participants), num_groups)

        for group_index, members in enumerate(groups, start=1):
            group_name = f"{group_index}組"
            group_id = cog.db.execute(
                "INSERT INTO league_groups (guild_id, group_index, group_name) VALUES (?, ?, ?)",
                (guild_id, group_index, group_name),
                return_lastrowid=True,
            )

            player_data = [
                (guild_id, p.id, group_id, p.display_name, isinstance(p, DummyPlayer))
                for p in members
            ]
            cog.db.execute(
                "INSERT INTO league_players (guild_id, user_id, group_id, display_name, is_dummy) VALUES (?, ?, ?, ?, ?)",
                player_data,
            )

            team_wrappers = [RR_Team(id=p.id, name=p.display_name) for p in members]
            schedule = generate_round_robin_schedule(team_wrappers)
            match_data = [
                (guild_id, group_id, round_num, t1.id, t2.id)
                for round_num, t1, t2 in schedule
            ]
            if match_data:
                cog.db.execute(
                    "INSERT INTO league_matches (guild_id, group_id, round_num, player1_id, player2_id) VALUES (?, ?, ?, ?, ?)",
                    match_data,
                )

        await send_league_match_cards(cog, match_ch, guild_id)
        await refresh_standings_image(cog, guild, main_ch)

        return True, f"{num_groups}グループでリーグ戦を開始しました（合計{len(participants)}名）。対戦カードを {match_ch.mention} に送信しました。"


async def resolve_league_player(cog: "LeagueCog", guild_id: int, user_id: int):
    """league_players から user_id を解決し、discord.Member か DummyPlayer を返す。"""
    row = cog.db.fetchone(
        "SELECT display_name, is_dummy FROM league_players WHERE guild_id = ? AND user_id = ?",
        (guild_id, user_id),
    )
    if not row:
        return None
    if row["is_dummy"]:
        dummy = DummyPlayer(row["display_name"])
        dummy.id = user_id
        return dummy
    guild = cog.bot.get_guild(guild_id)
    member = guild.get_member(user_id) if guild else None
    if member:
        return member
    try:
        return await cog.bot.fetch_user(user_id)
    except (discord.NotFound, discord.HTTPException):
        return None


def build_league_match_card_embed(cog: "LeagueCog", match_id: int) -> discord.Embed:
    """「対戦中」状態の対戦カードEmbedを構築する（結果却下時の差し戻しにも使う）。"""
    match = cog.db.fetchone(
        "SELECT group_id, player1_id, player2_id FROM league_matches WHERE match_id = ?",
        (match_id,),
    )
    group = cog.db.fetchone(
        "SELECT group_name FROM league_groups WHERE group_id = ?", (match["group_id"],)
    )
    p1_row = cog.db.fetchone(
        "SELECT display_name FROM league_players WHERE user_id = ? AND group_id = ?",
        (match["player1_id"], match["group_id"]),
    )
    p2_row = cog.db.fetchone(
        "SELECT display_name FROM league_players WHERE user_id = ? AND group_id = ?",
        (match["player2_id"], match["group_id"]),
    )
    p1_name = p1_row["display_name"] if p1_row else "???"
    p2_name = p2_row["display_name"] if p2_row else "???"

    embed = discord.Embed(
        title=f"【{group['group_name']}】対戦: {p1_name} vs {p2_name}",
        description="対戦終了後、勝者が自分の勝利ボタンを押して結果を申告してください。",
        color=discord.Color.gold(),
    )
    return embed


async def send_league_match_cards(cog: "LeagueCog", match_ch: discord.TextChannel, guild_id: int):
    from .league_views import LeaguePendingView

    matches = cog.db.fetchall(
        "SELECT match_id, group_id, player1_id, player2_id FROM league_matches "
        "WHERE guild_id = ? AND status = 'pending' ORDER BY group_id, round_num",
        (guild_id,),
    )
    for match in matches:
        p1 = await resolve_league_player(cog, guild_id, match["player1_id"])
        p2 = await resolve_league_player(cog, guild_id, match["player2_id"])
        if not p1 or not p2:
            log.warning(f"リーグ戦対戦カード送信をスキップ（プレイヤー未解決）: match_id={match['match_id']}")
            continue
        embed = build_league_match_card_embed(cog, match["match_id"])
        view = LeaguePendingView(match["match_id"], p1.display_name, p2.display_name)
        msg = await match_ch.send(embed=embed, view=view)
        cog.db.execute(
            "UPDATE league_matches SET message_id = ? WHERE match_id = ?",
            (msg.id, match["match_id"]),
        )


async def refresh_standings_image(
    cog: "LeagueCog", guild: discord.Guild, main_ch: discord.TextChannel
):
    """順位画像を生成し、main_chの既存メッセージを編集する（失敗時は新規送信してID保存し直す）。"""
    image_file = await cog.bot.loop.run_in_executor(
        None, league_image.generate_league_standings_image, guild.id, cog
    )
    if not image_file:
        return

    tournament = cog.db.fetchone(
        "SELECT standings_message_id, standings_channel_id FROM league_tournaments WHERE guild_id = ?",
        (guild.id,),
    )
    if tournament and tournament["standings_message_id"] and tournament["standings_channel_id"]:
        try:
            channel = cog.bot.get_channel(tournament["standings_channel_id"]) or main_ch
            message = await channel.fetch_message(tournament["standings_message_id"])
            await message.edit(attachments=[image_file])
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass

    msg = await main_ch.send(file=image_file)
    cog.db.execute(
        "UPDATE league_tournaments SET standings_message_id = ?, standings_channel_id = ? WHERE guild_id = ?",
        (msg.id, main_ch.id, guild.id),
    )


async def all_matches_confirmed(cog: "LeagueCog", guild_id: int) -> bool:
    pending = cog.db.fetchone(
        "SELECT 1 FROM league_matches WHERE guild_id = ? AND status != 'confirmed'", (guild_id,)
    )
    return pending is None


def _archive_league(cog: "LeagueCog", guild_id: int, flattened_ranked: List[dict]):
    """リーグ戦の最終結果を history_tournaments/history_rankings に保存する。"""
    try:
        winner_name = flattened_ranked[0]["name"] if flattened_ranked else "なし"
        tourney_id = cog.db.execute(
            "INSERT INTO history_tournaments (guild_id, name, type, winner_name) VALUES (?, ?, ?, ?)",
            (guild_id, "リーグ戦", "league", winner_name),
            return_lastrowid=True,
        )
        ranking_data = [
            (tourney_id, i + 1, p["name"], f"{p['source_group_name']} {p['group_rank']}位")
            for i, p in enumerate(flattened_ranked)
        ]
        if ranking_data:
            cog.db.execute(
                "INSERT INTO history_rankings (tournament_id, rank, name, info) VALUES (?, ?, ?, ?)",
                ranking_data,
            )
        log.info(f"リーグ戦データをアーカイブしました (ID: {tourney_id})")
    except Exception as e:
        log.error(f"リーグ戦アーカイブ中にエラー: {e}")


async def promote_top_n_to_tournament(
    cog: "LeagueCog", interaction: discord.Interaction, top_n_per_group: int
) -> Tuple[bool, str]:
    guild_id = interaction.guild.id

    async with cog.locks[guild_id]:
        if not cog.db.fetchone(
            "SELECT 1 FROM league_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)
        ):
            return False, "進行中のリーグ戦がありません。"

        remaining = cog.db.fetchone(
            "SELECT COUNT(*) as c FROM league_matches WHERE guild_id = ? AND status != 'confirmed'",
            (guild_id,),
        )
        if remaining and remaining["c"] > 0:
            return False, f"まだ確定していない試合が {remaining['c']} 件あります。全試合確定後に実行してください。"

        groups = cog.db.fetchall(
            "SELECT group_id, group_name FROM league_groups WHERE guild_id = ? ORDER BY group_index",
            (guild_id,),
        )

        flattened: List[dict] = []
        for group in groups:
            players = cog.db.fetchall(
                "SELECT user_id, display_name, wins, losses, is_dummy FROM league_players WHERE group_id = ?",
                (group["group_id"],),
            )
            matches = cog.db.fetchall(
                "SELECT player1_id, player2_id, winner_id FROM league_matches "
                "WHERE group_id = ? AND status = 'confirmed'",
                (group["group_id"],),
            )
            entries = [dict(p) for p in players]
            ranked = rank_entries(
                entries,
                [dict(m) for m in matches],
                id_key="user_id",
                wins_key="wins",
                opponent1_key="player1_id",
                opponent2_key="player2_id",
                winner_key="winner_id",
            )
            cut = ranked[: min(top_n_per_group, len(ranked))]
            for group_rank, p in enumerate(cut, start=1):
                flattened.append(
                    {
                        "id": p["user_id"],
                        "name": p["display_name"],
                        "is_dummy": bool(p["is_dummy"]),
                        "group_rank": group_rank,
                        "wins": p["wins"],
                        "source_group_name": group["group_name"],
                    }
                )

        if len(flattened) < 2:
            return False, "トーナメントを開始するには進出者が2名以上必要です。"

        flattened.sort(key=lambda p: (p["group_rank"], -p["wins"]))

        _archive_league(cog, guild_id, flattened)

        cog.db.execute("DELETE FROM league_matches WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_players WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_groups WHERE guild_id = ?", (guild_id,))
        cog.db.execute("DELETE FROM league_tournaments WHERE guild_id = ?", (guild_id,))

        event_manager_cog = cog.bot.get_cog("EventManager")
        if not event_manager_cog:
            return False, "❌ 大会機能(EventManager)が読み込まれていないため、トーナメントを開始できません。"

        ranked_players = [
            {"id": p["id"], "name": p["name"], "is_dummy": p["is_dummy"]} for p in flattened
        ]
        await event_manager_cog._execute_bracket_from_ranking(interaction, guild_id, ranked_players)
        return True, f"上位{len(flattened)}名でトーナメントを開始しました。"
