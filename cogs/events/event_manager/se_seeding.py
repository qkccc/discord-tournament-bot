# cogs/events/event_manager/se_seeding.py
"""
既に順位が決まっている参加者リストから、シングルエリミネーション(SE)トーナメントを
シード方式(1位と2位が決勝まで当たらない標準的な配置)で組み立てる。

`cog.py` の `_execute_bracket` は募集参加者をランダムシャッフルしてブラケットを組むが、
こちらはリーグ戦(総当たり)の結果など、既にランク付けされたリストをそのまま
強い順にシードするための入口。DB書き込みロジック自体は `_execute_bracket` と同じ形。
"""
import math
from typing import Any, Dict, List, Tuple

from .database import DatabaseManager


def compute_seed_slots(size: int) -> List[int]:
    """size(2のべき乗)のブラケットにおける、各スロットへ配置するシード番号の並びを返す。

    再帰的な2分割によって、シード1と2が決勝まで対戦しないように配置する。
    例: size=8 -> [1, 8, 4, 5, 2, 7, 3, 6]
    """
    seeds = [1]
    while len(seeds) < size:
        n = len(seeds) * 2
        seeds = [x for s in seeds for x in (s, n + 1 - s)]
    return seeds


def seed_players_into_bracket(
    db: DatabaseManager, guild_id: int, ranked_players: List[Dict[str, Any]]
) -> Tuple[int, int]:
    """ranked_players（[0]が1位、以降ランク順）を標準シードでSEブラケットに投入する。

    各要素は {"id": int, "name": str, "is_dummy": bool} の辞書。
    既存の se_tournaments/se_matches/event_players（このguildの分）は削除してから再構築する。
    戻り値: (参加人数, ラウンド数)
    """
    num_players = len(ranked_players)
    num_rounds = math.ceil(math.log2(max(2, num_players)))
    tourney_size = 2**num_rounds

    db.execute("DELETE FROM se_matches WHERE guild_id = ?", (guild_id,))
    db.execute("DELETE FROM se_tournaments WHERE guild_id = ?", (guild_id,))
    db.execute("DELETE FROM event_players WHERE guild_id = ?", (guild_id,))
    db.execute(
        "INSERT INTO se_tournaments (guild_id, is_active, num_players, num_rounds, bracket_message_id) VALUES (?, ?, ?, ?, ?)",
        (guild_id, True, num_players, num_rounds, None),
    )

    player_db_data = [
        (guild_id, p["id"], p["name"], bool(p.get("is_dummy", False)))
        for p in ranked_players
    ]
    if player_db_data:
        db.execute(
            "INSERT INTO event_players (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)",
            player_db_data,
        )

    # シード番号(1始まり) -> スロット位置 の並びを作り、人数を超えるシードは不戦勝(BYE)にする。
    slots = compute_seed_slots(tourney_size)
    seeding_list = []
    for seed_no in slots:
        if seed_no <= num_players:
            p = ranked_players[seed_no - 1]
            seeding_list.append({"id": p["id"], "name": p["name"]})
        else:
            seeding_list.append({"id": None, "name": "(不戦勝)", "is_bye": True})

    # ここから先（対戦データの組み立て・不戦勝の繰り上げ）は _execute_bracket と同じロジック。
    matches_to_insert = []
    next_round_source_matches = []
    for i in range(0, tourney_size, 2):
        p1_data, p2_data = seeding_list[i], seeding_list[i + 1]
        match_id = f"R1M{i // 2}"
        is_bye_match = p1_data.get("is_bye", False) or p2_data.get("is_bye", False)
        winner_id = (
            p2_data["id"]
            if p1_data.get("is_bye")
            else (p1_data["id"] if p2_data.get("is_bye") else None)
        )
        matches_to_insert.append(
            (
                guild_id,
                match_id,
                1,
                i // 2,
                p1_data["id"],
                p2_data["id"],
                None,
                None,
                winner_id,
                is_bye_match,
            )
        )
        next_round_source_matches.append(match_id)

    for r_num in range(2, num_rounds + 1):
        sources = list(next_round_source_matches)
        next_round_source_matches = []
        for i in range(0, len(sources), 2):
            p1_source, p2_source = sources[i], sources[i + 1]
            match_id = f"R{r_num}M{i // 2}"
            matches_to_insert.append(
                (
                    guild_id,
                    match_id,
                    r_num,
                    i // 2,
                    None,
                    None,
                    p1_source,
                    p2_source,
                    None,
                    False,
                )
            )
            next_round_source_matches.append(match_id)

    if matches_to_insert:
        db.execute(
            "INSERT INTO se_matches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            matches_to_insert,
        )

    for bye_match in db.fetchall(
        "SELECT match_id, winner_id FROM se_matches WHERE guild_id = ? AND round_num = 1 AND is_bye = 1",
        (guild_id,),
    ):
        next_match = db.fetchone(
            "SELECT match_id, player1_source_match_id FROM se_matches WHERE guild_id = ? AND (player1_source_match_id = ? OR player2_source_match_id = ?)",
            (guild_id, bye_match["match_id"], bye_match["match_id"]),
        )
        if next_match:
            column_to_update = (
                "player1_id"
                if next_match["player1_source_match_id"] == bye_match["match_id"]
                else "player2_id"
            )
            db.execute(
                f"UPDATE se_matches SET {column_to_update} = ? WHERE guild_id = ? AND match_id = ?",
                (bye_match["winner_id"], guild_id, next_match["match_id"]),
            )

    return num_players, num_rounds
