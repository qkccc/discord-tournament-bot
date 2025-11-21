# cogs/roundrobin/rr_models.py
from dataclasses import dataclass, field
from typing import List, Union
import discord
from cogs.events.event_manager.models import DummyPlayer

@dataclass
class RR_Team:
    """
    総当たり戦のチーム情報を保持するクラス。
    主に _generate_round_robin_schedule 関数内で一時的に使用される。
    """
    id: int
    name: str
    members: List[Union[discord.Member, DummyPlayer]] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
