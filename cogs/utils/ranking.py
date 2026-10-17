# cogs/utils/ranking.py
"""
「勝数→(任意で)スコア→直接対決」で順位付けする、スキーマ非依存の共通ロジック。
チーム総当たり戦(roundrobin)の rr_logic.py にある compare_teams と同じ考え方を、
個人戦リーグ戦などでも使えるよう汎用化したもの。
"""
import functools
from typing import Callable, Dict, FrozenSet, List, Optional


def build_head_to_head_map(
    matches: List[dict],
    id1_key: str,
    id2_key: str,
    winner_key: str,
) -> Dict[FrozenSet[int], int]:
    """試合結果のリストから {frozenset({id1, id2}): winner_id} の対戦表を作る。
    winner_key が None（未報告/引き分け）の試合はスキップする。
    """
    result: Dict[FrozenSet[int], int] = {}
    for match in matches:
        winner_id = match.get(winner_key)
        if winner_id is None:
            continue
        id1, id2 = match.get(id1_key), match.get(id2_key)
        if id1 is None or id2 is None:
            continue
        result[frozenset((id1, id2))] = winner_id
    return result


def rank_entries(
    entries: List[dict],
    matches: List[dict],
    id_key: str,
    wins_key: str,
    opponent1_key: str,
    opponent2_key: str,
    winner_key: str,
    score_key: Optional[str] = None,
) -> List[dict]:
    """entries を「勝数(降順) → score_key指定時はそれも(降順) → 直接対決」で順位付けして返す。
    entries/matches は辞書のリストで、どんなテーブル由来でもよい。entries自体は変更しない。
    """
    head_to_head = build_head_to_head_map(matches, opponent1_key, opponent2_key, winner_key)

    def compare(a: dict, b: dict) -> int:
        if a[wins_key] != b[wins_key]:
            return b[wins_key] - a[wins_key]
        if score_key is not None and a.get(score_key, 0) != b.get(score_key, 0):
            return b.get(score_key, 0) - a.get(score_key, 0)
        key = frozenset((a[id_key], b[id_key]))
        winner_id = head_to_head.get(key)
        if winner_id == a[id_key]:
            return -1
        if winner_id == b[id_key]:
            return 1
        return 0

    return sorted(entries, key=functools.cmp_to_key(compare))
