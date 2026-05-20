"""
スイスドロー機能用デバッグコマンド
トーナメント状態、ペアリング、結果などの確認・検証用
"""

import discord
from discord.ext import commands
import json
from .event_manager.database import DatabaseManager

db = DatabaseManager()


class SwissDebugCog(commands.Cog, name="SwissDebug"):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="swiss_status")
    @commands.has_permissions(administrator=True)
    async def swiss_status(self, ctx: commands.Context):
        """現在のスイスドロー大会状態を表示"""
        guild_id = ctx.guild.id

        # トーナメント情報
        tournament = db.fetchone(
            "SELECT * FROM swiss_tournaments WHERE guild_id = ?", (guild_id,)
        )

        if not tournament:
            await ctx.send("スイスドロー大会がアクティブではありません。")
            return

        embed = discord.Embed(title="スイスドロー大会状態", color=0x00AAFF)
        embed.add_field(
            name="進行状況",
            value=f"ラウンド: {tournament['round_num']} / {tournament['max_rounds']}",
            inline=False,
        )
        embed.add_field(
            name="ステータス",
            value="🟢 アクティブ" if tournament["is_active"] else "🔴 終了",
            inline=False,
        )

        # 参加者一覧
        players = db.fetchall(
            "SELECT user_id, display_name, score, wins, losses, matches_played FROM event_players WHERE guild_id = ? ORDER BY score DESC",
            (guild_id,),
        )

        if players:
            player_lines = []
            for p in players:
                player_lines.append(
                    f"・{p['display_name']}: {p['wins']}勝 {p['losses']}敗 (スコア: {p['score']}, 試合: {p['matches_played']})"
                )
            embed.add_field(
                name=f"参加者 ({len(players)}名)",
                value="\n".join(player_lines),
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="swiss_pairings")
    @commands.has_permissions(administrator=True)
    async def swiss_pairings(self, ctx: commands.Context):
        """現在のペアリングを表示"""
        guild_id = ctx.guild.id

        pairings = db.fetchall(
            "SELECT player1_id, player2_id FROM swiss_pairings WHERE guild_id = ?",
            (guild_id,),
        )

        if not pairings:
            await ctx.send("ペアリングがありません。")
            return

        embed = discord.Embed(title="現在のペアリング", color=0x00AA00)

        for i, pair in enumerate(pairings, 1):
            p1 = db.fetchone(
                "SELECT display_name FROM event_players WHERE guild_id = ? AND user_id = ?",
                (guild_id, pair["player1_id"]),
            )
            p2 = (
                db.fetchone(
                    "SELECT display_name FROM event_players WHERE guild_id = ? AND user_id = ?",
                    (guild_id, pair["player2_id"]),
                )
                if pair["player2_id"]
                else None
            )

            p1_name = p1["display_name"] if p1 else f"ID: {pair['player1_id']}"
            p2_name = (
                p2["display_name"]
                if p2
                else (f"ID: {pair['player2_id']}" if pair["player2_id"] else "Bye")
            )

            embed.add_field(
                name=f"対戦 {i}", value=f"{p1_name} vs {p2_name}", inline=False
            )

        await ctx.send(embed=embed)

    @commands.command(name="swiss_results")
    @commands.has_permissions(administrator=True)
    async def swiss_results(self, ctx: commands.Context, round_num: int | None = None):
        """スイスドロー結果を表示（全ラウンドまたは指定ラウンド）"""
        guild_id = ctx.guild.id

        if round_num is None:
            results = db.fetchall(
                "SELECT round_num, winner_id, loser_id FROM swiss_results WHERE guild_id = ? ORDER BY round_num DESC",
                (guild_id,),
            )
        else:
            results = db.fetchall(
                "SELECT round_num, winner_id, loser_id FROM swiss_results WHERE guild_id = ? AND round_num = ? ORDER BY round_num DESC",
                (guild_id, round_num),
            )

        if not results:
            await ctx.send("結果がありません。")
            return

        embed = discord.Embed(title="スイスドロー結果", color=0xAA00AA)

        current_round = None
        round_results = []

        for result in results:
            if current_round != result["round_num"]:
                if round_results:
                    embed.add_field(
                        name=f"{current_round}ラウンド",
                        value="\n".join(round_results),
                        inline=False,
                    )
                current_round = result["round_num"]
                round_results = []

            winner = db.fetchone(
                "SELECT display_name FROM event_players WHERE guild_id = ? AND user_id = ?",
                (guild_id, result["winner_id"]),
            )
            loser = db.fetchone(
                "SELECT display_name FROM event_players WHERE guild_id = ? AND user_id = ?",
                (guild_id, result["loser_id"]),
            )

            winner_name = (
                winner["display_name"] if winner else f"ID: {result['winner_id']}"
            )
            loser_name = loser["display_name"] if loser else f"ID: {result['loser_id']}"

            round_results.append(f"✅ {winner_name} が {loser_name} に勝利")

        if round_results:
            embed.add_field(
                name=f"{current_round}ラウンド",
                value="\n".join(round_results),
                inline=False,
            )

        await ctx.send(embed=embed)

    @commands.command(name="swiss_rankings")
    @commands.has_permissions(administrator=True)
    async def swiss_rankings(self, ctx: commands.Context):
        """スイスドロー現在順位表"""
        guild_id = ctx.guild.id

        players = db.fetchall(
            "SELECT display_name, score, wins, losses, matches_played FROM event_players WHERE guild_id = ? ORDER BY score DESC",
            (guild_id,),
        )

        if not players:
            await ctx.send("参加者がいません。")
            return

        embed = discord.Embed(title="スイスドロー順位表", color=0xFFAA00)

        ranking_lines = []
        for rank, p in enumerate(players, 1):
            ranking_lines.append(
                f"{rank}. {p['display_name']}: スコア {p['score']} ({p['wins']}勝 {p['losses']}敗)"
            )

        embed.description = "\n".join(ranking_lines)
        await ctx.send(embed=embed)

    @commands.command(name="swiss_export")
    @commands.has_permissions(administrator=True)
    async def swiss_export(self, ctx: commands.Context):
        """スイスドロー全データをJSON形式でエクスポート"""
        guild_id = ctx.guild.id

        # トーナメント情報
        tournament = db.fetchone(
            "SELECT * FROM swiss_tournaments WHERE guild_id = ?", (guild_id,)
        )

        # 参加者
        players = db.fetchall(
            "SELECT * FROM event_players WHERE guild_id = ?", (guild_id,)
        )

        # ペアリング
        pairings = db.fetchall(
            "SELECT * FROM swiss_pairings WHERE guild_id = ?", (guild_id,)
        )

        # 結果
        results = db.fetchall(
            "SELECT * FROM swiss_results WHERE guild_id = ?", (guild_id,)
        )

        data = {
            "tournament": dict(tournament) if tournament else None,
            "players": [dict(p) for p in players],
            "pairings": [dict(pair) for pair in pairings],
            "results": [dict(r) for r in results],
        }

        json_str = json.dumps(data, indent=2, ensure_ascii=False)

        # ファイルとして送信
        await ctx.send(
            f"スイスドロー全データ (ギルド ID: {guild_id})",
            file=discord.File(
                fp=__import__("io").BytesIO(json_str.encode("utf-8")),
                filename=f"swiss_export_{guild_id}.json",
            ),
        )

    @commands.command(name="swiss_verify")
    @commands.has_permissions(administrator=True)
    async def swiss_verify(self, ctx: commands.Context):
        """スイスドロー整合性チェック"""
        guild_id = ctx.guild.id

        embed = discord.Embed(title="スイスドロー整合性チェック", color=0x0000FF)
        checks = []

        # チェック1: 参加者のスコア計算
        players = db.fetchall(
            "SELECT user_id, display_name, score, wins, losses, matches_played FROM event_players WHERE guild_id = ?",
            (guild_id,),
        )

        issues = []
        for p in players:
            win_loss_sum = p["wins"] + p["losses"]
            if win_loss_sum != p["matches_played"]:
                issues.append(
                    f"⚠️ {p['display_name']}: matches_played({p['matches_played']}) != wins({p['wins']}) + losses({p['losses']})"
                )

        if issues:
            checks.append("❌ スコア計算エラー:\n" + "\n".join(issues))
        else:
            checks.append("✅ スコア計算: OK")

        # チェック2: ペアリングの有効性
        pairings = db.fetchall(
            "SELECT player1_id, player2_id FROM swiss_pairings WHERE guild_id = ?",
            (guild_id,),
        )

        pairing_issues = []
        for pair in pairings:
            if pair["player2_id"] and pair["player1_id"] == pair["player2_id"]:
                pairing_issues.append(
                    f"⚠️ 無効なペア: {pair['player1_id']} vs {pair['player1_id']}"
                )

        if pairing_issues:
            checks.append("❌ ペアリング エラー:\n" + "\n".join(pairing_issues))
        else:
            checks.append(f"✅ ペアリング: OK ({len(pairings)}組)")

        # チェック3: 結果の有効性
        results = db.fetchall(
            "SELECT round_num, winner_id, loser_id FROM swiss_results WHERE guild_id = ?",
            (guild_id,),
        )

        result_issues = []
        for r in results:
            if r["winner_id"] == r["loser_id"]:
                result_issues.append(
                    f"⚠️ 無効な結果(ラウンド{r['round_num']}): {r['winner_id']} vs {r['loser_id']}"
                )

        if result_issues:
            checks.append("❌ 結果 エラー:\n" + "\n".join(result_issues))
        else:
            checks.append(f"✅ 結果: OK ({len(results)}件)")

        embed.description = "\n".join(checks)
        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(SwissDebugCog(bot))
