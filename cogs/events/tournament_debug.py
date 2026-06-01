"""
大会用デバッグコマンド
ダミー参加者の追加、デバッグモード管理など
"""

import discord
from discord.ext import commands
from .event_manager.models import DummyPlayer
from .event_manager.database import DatabaseManager
from .debug_controller import debug_controller

db = DatabaseManager()


class TournamentDebugCog(commands.Cog, name="TournamentDebug"):
    """大会機能全体のデバッグコマンド"""

    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="debug_mode")
    @commands.has_permissions(administrator=True)
    async def toggle_debug_mode(self, ctx: commands.Context):
        """デバッグモードの ON/OFF を切り替える"""
        guild_id = ctx.guild.id
        is_enabled = not debug_controller.is_debug_enabled(guild_id)

        if is_enabled:
            debug_controller.enable_debug(guild_id, ctx.channel)
            desc = f"デバッグモード ON\n\nすべての大会コマンド出力は {ctx.channel.mention} に送信されます。"
        else:
            debug_controller.disable_debug(guild_id)
            desc = "デバッグモード OFF\n\n大会コマンド出力は通常のチャンネル設定に従います。"

        embed = discord.Embed(
            title="デバッグモード",
            description=desc,
            color=0x00FF00 if is_enabled else 0xFF0000,
        )
        await ctx.send(embed=embed)

    @commands.command(name="add_dummy")
    @commands.has_permissions(administrator=True)
    async def add_dummy_players(
        self, ctx: commands.Context, count: int, name_prefix: str = "Dummy"
    ):
        """ダミー参加者を追加"""
        if count <= 0 or count > 100:
            await ctx.send("参加者数は 1～100 の間で指定してください。")
            return

        guild_id = ctx.guild.id
        
        # イベントマネージャーCogを取得
        cog = self.bot.get_cog("EventManager")
        if not cog:
            await ctx.send("❌ イベントマネージャーCogが見つかりません。")
            return
        
        session = cog.recruit_sessions.get(guild_id)
        if not session:
            await ctx.send("❌ 参加者募集中のイベントがありません。")
            return

        dummy_players = []

        for i in range(count):
            dummy_name = f"{name_prefix}_{i + 1}"
            dummy = DummyPlayer(dummy_name)
            dummy_players.append((dummy_name, dummy.id))
            
            # 募集セッションに追加
            session["participants"].add(dummy)

            # DB に追加
            db.execute(
                "INSERT OR IGNORE INTO event_players (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)",
                (guild_id, dummy.id, dummy_name, True),
            )
            
            # event_participants にも追加
            db.execute(
                "INSERT OR IGNORE INTO event_participants (guild_id, user_id, display_name, is_dummy) VALUES (?, ?, ?, ?)",
                (guild_id, dummy.id, dummy_name, True),
            )

        embed = discord.Embed(
            title="ダミー参加者を追加",
            description=f"{count}人のダミー参加者を追加しました。",
            color=0x00AAFF,
        )
        for name, uid in dummy_players[:5]:
            embed.add_field(name="", value=f"・{name} (ID: {uid})", inline=False)

        if count > 5:
            embed.add_field(name="", value=f"・他 {count - 5}人", inline=False)

        await ctx.send(embed=embed)
        
        # 募集メッセージを更新
        await cog._update_recruitment_message(guild_id)

    @commands.command(name="clear_dummies")
    @commands.has_permissions(administrator=True)
    async def clear_dummy_players(self, ctx: commands.Context):
        """すべてのダミー参加者を削除"""
        guild_id = ctx.guild.id

        # イベントマネージャーCogを取得
        cog = self.bot.get_cog("EventManager")
        if not cog:
            await ctx.send("❌ イベントマネージャーCogが見つかりません。")
            return
        
        session = cog.recruit_sessions.get(guild_id)
        if not session:
            await ctx.send("❌ 参加者募集中のイベントがありません。")
            return

        # 募集セッションからダミーを削除
        dummies_to_remove = [p for p in session["participants"] if isinstance(p, DummyPlayer)]
        for dummy in dummies_to_remove:
            session["participants"].remove(dummy)

        # DB からダミーを削除
        dummies = db.fetchall(
            "SELECT user_id FROM event_players WHERE guild_id = ? AND is_dummy = ?",
            (guild_id, True),
        )

        count = len(dummies)

        for dummy in dummies:
            db.execute(
                "DELETE FROM event_players WHERE guild_id = ? AND user_id = ?",
                (guild_id, dummy["user_id"]),
            )
            db.execute(
                "DELETE FROM event_participants WHERE guild_id = ? AND user_id = ?",
                (guild_id, dummy["user_id"]),
            )

        embed = discord.Embed(
            title="ダミー参加者を削除",
            description=f"{count}人のダミー参加者を削除しました。",
            color=0xff6600,
        )
        await ctx.send(embed=embed)
        
        # 募集メッセージを更新
        await cog._update_recruitment_message(guild_id)

    @commands.command(name="list_participants")
    @commands.has_permissions(administrator=True)
    async def list_participants(self, ctx: commands.Context):
        """現在の参加者一覧を表示"""
        guild_id = ctx.guild.id

        players = db.fetchall(
            "SELECT display_name, is_dummy FROM event_players WHERE guild_id = ? ORDER BY is_dummy, display_name",
            (guild_id,),
        )

        if not players:
            await ctx.send("参加者がいません。")
            return

        embed = discord.Embed(title="参加者一覧", color=0x0099FF)

        real_players = [p for p in players if not p["is_dummy"]]
        dummy_players = [p for p in players if p["is_dummy"]]

        if real_players:
            real_names = "\n".join(
                [f"✅ {p['display_name']}" for p in real_players[:10]]
            )
            if len(real_players) > 10:
                real_names += f"\n・他 {len(real_players) - 10}人"
            embed.add_field(
                name=f"実プレイヤー ({len(real_players)}人)",
                value=real_names,
                inline=False,
            )

        if dummy_players:
            dummy_names = "\n".join(
                [f"🤖 {p['display_name']}" for p in dummy_players[:10]]
            )
            if len(dummy_players) > 10:
                dummy_names += f"\n・他 {len(dummy_players) - 10}人"
            embed.add_field(
                name=f"ダミー参加者 ({len(dummy_players)}人)",
                value=dummy_names,
                inline=False,
            )

        embed.add_field(name="合計", value=f"**{len(players)}人**", inline=False)

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(TournamentDebugCog(bot))
