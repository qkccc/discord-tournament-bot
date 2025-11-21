# cogs/tournament/models.py
import discord
import random
from typing import Set, Dict, Optional, List, Tuple, Union

class DummyPlayer:
    """デバッグや人数調整用の偽プレイヤーを表すクラス"""
    def __init__(self, name: str):
        self.display_name = name
        self.id = random.randint(-1000000, -1)
    
    def __hash__(self):
        return hash(self.display_name)

    def __eq__(self, other):
        return isinstance(other, DummyPlayer) and self.display_name == other.display_name

class Player:
    """大会の参加者を表すクラス"""
    def __init__(self, member: Union[discord.Member, DummyPlayer]):
        self.member = member
        self.score: float = 0.0
        self.opponents: Set[int] = set()
        self.byes: int = 0
        self.wins: int = 0
        self.losses: int = 0
        self.matches_played: int = 0
        self.omw: float = 0.0 # Opponent's Match Win Percentage

    @property
    def display_name(self) -> str:
        return self.member.display_name

    @property
    def id(self) -> int:
        return self.member.id

    def add_opponent(self, opponent_id: int):
        self.opponents.add(opponent_id)

    @property
    def record(self) -> str:
        return f"{int(self.wins)}勝{int(self.losses)}敗"

    def calculate_omw(self, all_players: Dict[int, 'Player']) -> float:
        """対戦相手の勝率（OMW%）を計算する"""
        if not self.opponents:
            return 0.0
        
        total_opp_win_percentage = 0.0
        valid_opponents = 0
        
        for opp_id in self.opponents:
            opponent = all_players.get(opp_id)
            if opponent and opponent.matches_played > 0:
                # OMWは最低でも33.3%を保証するルールが多い
                opp_win_rate = max(0.333, opponent.score / opponent.matches_played)
                total_opp_win_percentage += opp_win_rate
                valid_opponents += 1
                
        return total_opp_win_percentage / valid_opponents if valid_opponents > 0 else 0.0

class SwissTournament:
    """スイスドロー大会の状態をメモリ上で管理するクラス"""
    def __init__(self, participants: Set[Union[discord.Member, DummyPlayer]]):
        self.is_active: bool = True
        self.round_num: int = 0
        self.max_rounds: int = 0
        self.players: Dict[int, Player] = {p.id: Player(p) for p in participants}
        self.current_pairings: List[Tuple[Player, Optional[Player]]] = []
        self.reported_matches_this_round: List[Tuple[int, int, int]] = [] # p1, p2, winner

    def get_player(self, member_id: int) -> Optional[Player]:
        return self.players.get(member_id)

    def get_ranked_players(self) -> List[Player]:
        """スコアとOMW%でプレイヤーをソートして返す"""
        for p in self.players.values():
            p.omw = p.calculate_omw(self.players)
        return sorted(self.players.values(), key=lambda p: (p.score, p.omw), reverse=True)

    def generate_pairings(self) -> List[Tuple[Player, Optional[Player]]]:
        self.current_pairings = []
        self.reported_matches_this_round = []
        unpaired_players = self.get_ranked_players()
        bye_player = None

        if len(unpaired_players) % 2 != 0:
            # 不戦勝（Bye）の権利がないプレイヤーを優先
            eligible_for_bye = sorted([p for p in unpaired_players if p.byes == 0], key=lambda p: p.score)
            bye_player = eligible_for_bye[0] if eligible_for_bye else unpaired_players[-1]
            unpaired_players.remove(bye_player)
            bye_player.byes += 1
            bye_player.wins += 1
            bye_player.score += 1.0

        score_groups = {}
        for p in unpaired_players:
            score_groups.setdefault(p.score, []).append(p)

        unpaired_floaters = []
        for score in sorted(score_groups.keys(), reverse=True):
            group = unpaired_floaters + score_groups[score]
            unpaired_floaters = []
            if len(group) % 2 != 0:
                unpaired_floaters.append(group.pop()) # 一番下のプレイヤーを次のグループへ
            random.shuffle(group)

            temp_group = list(group)
            while temp_group:
                p1 = temp_group.pop(0)
                # まだ対戦したことがない相手を探す
                best_match = next((p2 for p2 in temp_group if p2.id not in p1.opponents), None)
                if not best_match: # 全員と対戦済みの場合、仕方なくランダムに選ぶ
                    best_match = temp_group[0] if temp_group else None
                
                if best_match:
                    temp_group.remove(best_match)
                    self.current_pairings.append(tuple(sorted((p1, best_match), key=lambda x: x.id)))
                    p1.add_opponent(best_match.id)
                    best_match.add_opponent(p1.id)

        if bye_player:
            self.current_pairings.append((bye_player, None))

        return self.current_pairings

    def is_match_reported(self, p1_id: int, p2_id: int) -> bool:
        return any({r[0], r[1]} == {p1_id, p2_id} for r in self.reported_matches_this_round)