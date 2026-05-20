import datetime
import json
from pathlib import Path

import discord
from discord.ext import commands, tasks


class SCLPosterAnnouncementCog(commands.Cog, name="SCLPosterAnnouncement"):
    JST = datetime.timezone(datetime.timedelta(hours=9))
    POSTER_CHANNEL_ID = 857079592354709534
    ANNOUNCE_TIME_JST = datetime.time(hour=20, minute=55, tzinfo=JST)
    SCHEDULE_FILE = Path(__file__).resolve().parents[2] / "data" / "scl_poster_schedule.json"

    def __init__(self, bot):
        self.bot = bot
        self.poster_announcement_task.start()

    def cog_unload(self):
        self.poster_announcement_task.cancel()

    def _load_today_schedule(self) -> dict | None:
        if not self.SCHEDULE_FILE.exists():
            return None

        try:
            with self.SCHEDULE_FILE.open("r", encoding="utf-8") as file:
                schedule_data = json.load(file)
        except (OSError, json.JSONDecodeError):
            return None

        today_key = datetime.datetime.now(self.JST).date().isoformat()
        today_schedule = schedule_data.get(today_key)
        return today_schedule if isinstance(today_schedule, dict) else None

    def _build_announcement_text(self, schedule: dict) -> str:
        round_text = schedule.get("round") or schedule.get("round_text")
        half_text = schedule.get("half") or schedule.get("half_text")
        opponent_team = schedule.get("opponent_team") or schedule.get("opponent")

        match_title = "".join(part for part in [half_text, round_text] if part)
        lines = ["@everyone", "SCLの試合が始まります！"]

        if match_title:
            lines.append(match_title)

        if opponent_team:
            lines.append(f"vs {opponent_team}")

        return "\n".join(lines)

    @tasks.loop(time=ANNOUNCE_TIME_JST)
    async def poster_announcement_task(self):
        schedule = self._load_today_schedule()
        if not schedule:
            return

        channel = self.bot.get_channel(self.POSTER_CHANNEL_ID)
        if not isinstance(channel, discord.TextChannel):
            return

        message = self._build_announcement_text(schedule)
        await channel.send(message, allowed_mentions=discord.AllowedMentions(everyone=True))

    @poster_announcement_task.before_loop
    async def before_poster_announcement_task(self):
        await self.bot.wait_until_ready()


async def setup(bot):
    await bot.add_cog(SCLPosterAnnouncementCog(bot))