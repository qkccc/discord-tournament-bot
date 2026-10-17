# cogs/events/league/cog.py
import asyncio
import functools
import logging
from collections import defaultdict
from typing import Dict

from discord.ext import commands

from cogs.events.event_manager.database import DatabaseManager

from . import league_image, league_logic
from .league_views import (
    LeagueApprovalView,
    LeagueCorrectView,
    LeaguePendingView,
    LeaguePromoteView,
)

log = logging.getLogger(__name__)


class LeagueCog(commands.Cog, name="League"):
    """個人戦リーグ戦(総当たり、複数グループ)→順位付け→トーナメント移行を管理するCog"""

    def __init__(self, bot):
        self.bot = bot
        self.db = DatabaseManager()
        self.locks: Dict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

        if not hasattr(bot, "persistent_views_added_league"):
            bot.add_view(LeaguePendingView())
            bot.add_view(LeagueApprovalView())
            bot.add_view(LeagueCorrectView())
            bot.add_view(LeaguePromoteView())
            bot.persistent_views_added_league = True

        # league_logic側の関数をcogメソッドとして公開する（roundrobinと同じ流儀）
        self.start_league = functools.partial(league_logic.start_league, self)

    @commands.command(name="リーグ順位", help="現在のリーグ戦の順位画像を表示します。")
    async def show_league_standings(self, ctx: commands.Context):
        if not self.db.fetchone(
            "SELECT 1 FROM league_tournaments WHERE guild_id = ? AND is_active = 1",
            (ctx.guild.id,),
        ):
            return await ctx.send("現在進行中のリーグ戦はありません。")
        image_file = await self.bot.loop.run_in_executor(
            None, league_image.generate_league_standings_image, ctx.guild.id, self
        )
        if not image_file:
            return await ctx.send("順位画像の生成に失敗しました。")
        await ctx.send(file=image_file)

    @commands.command(name="リーグ中止", help="進行中のリーグ戦を中止・リセットします。")
    @commands.has_permissions(manage_guild=True)
    async def cancel_league(self, ctx: commands.Context):
        guild_id = ctx.guild.id
        self.db.execute("DELETE FROM league_matches WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM league_players WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM league_groups WHERE guild_id = ?", (guild_id,))
        self.db.execute("DELETE FROM league_tournaments WHERE guild_id = ?", (guild_id,))
        await ctx.send("🚨 リーグ戦の登録情報・進行状況をすべてリセットしました。")


async def setup(bot):
    await bot.add_cog(LeagueCog(bot))
