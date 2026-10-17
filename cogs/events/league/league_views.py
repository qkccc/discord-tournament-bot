# cogs/events/league/league_views.py
"""リーグ戦の対戦カード用View群。

すべて custom_id を固定文字列にし、Bot再起動を跨いでも動作するよう
bot.add_view() で一度だけグローバル登録する（roundrobinの ReportResultView 等と同じ方式）。
どの対戦のボタンかは custom_id ではなく message_id を使い、都度DBから引き直す
（ラベルにだけ実際のプレイヤー名を反映させたいため、custom_idにmatch_idは含めない）。
"""
import discord

from . import league_handlers, league_logic


class LeaguePendingView(discord.ui.View):
    """「対戦中」状態のView。2ボタンで勝者を申告する（BO1のためスコア入力は不要）。"""

    def __init__(
        self,
        match_id: int | None = None,
        p1_name: str = "プレイヤー1",
        p2_name: str = "プレイヤー2",
    ):
        super().__init__(timeout=None)
        self.match_id = match_id

        p1_button = discord.ui.Button(
            label=f"{p1_name} 勝利",
            style=discord.ButtonStyle.success,
            custom_id="league_win_p1",
        )
        p1_button.callback = self.on_p1_win
        self.add_item(p1_button)

        p2_button = discord.ui.Button(
            label=f"{p2_name} 勝利",
            style=discord.ButtonStyle.success,
            custom_id="league_win_p2",
        )
        p2_button.callback = self.on_p2_win
        self.add_item(p2_button)

    async def _declare(self, interaction: discord.Interaction, player_field: str):
        cog = interaction.client.get_cog("League")
        if not cog:
            return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        match = cog.db.fetchone(
            f"SELECT {player_field} as pid FROM league_matches WHERE message_id = ?",
            (interaction.message.id,),
        )
        if not match:
            return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
        await league_handlers.handle_declare_winner(
            cog, interaction, interaction.message.id, match["pid"]
        )

    async def on_p1_win(self, interaction: discord.Interaction):
        await self._declare(interaction, "player1_id")

    async def on_p2_win(self, interaction: discord.Interaction):
        await self._declare(interaction, "player2_id")


class LeagueApprovalView(discord.ui.View):
    """「承認待ち」状態のView。"""

    def __init__(self, match_id: int | None = None):
        super().__init__(timeout=None)
        self.match_id = match_id

    @discord.ui.button(label="承認", style=discord.ButtonStyle.success, custom_id="league_approve")
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("League")
        if not cog:
            return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        await league_handlers.handle_approve(cog, interaction, interaction.message.id)

    @discord.ui.button(label="却下", style=discord.ButtonStyle.danger, custom_id="league_reject")
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("League")
        if not cog:
            return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        await league_handlers.handle_reject(cog, interaction, interaction.message.id)


class LeagueCorrectView(discord.ui.View):
    """「確定」状態のView。"""

    def __init__(self, match_id: int | None = None):
        super().__init__(timeout=None)
        self.match_id = match_id

    @discord.ui.button(label="結果を訂正", style=discord.ButtonStyle.danger, custom_id="league_correct")
    async def correct(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("League")
        if not cog:
            return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        await league_handlers.handle_correct_start(cog, interaction, interaction.message.id)


class PromoteToTournamentModal(discord.ui.Modal, title="トーナメントへ移行"):
    num_advance_input = discord.ui.TextInput(
        label="各グループの上位進出人数", placeholder="例: 2"
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("League")
        if not cog:
            return await interaction.followup.send("エラー: 機能がロードされていません。", ephemeral=True)
        try:
            top_n = int(self.num_advance_input.value)
            if top_n <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.followup.send(
                "進出人数は1以上の半角数字で入力してください。", ephemeral=True
            )
        ok, msg = await league_logic.promote_top_n_to_tournament(cog, interaction, top_n)
        if not ok:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)


class LeaguePromoteView(discord.ui.View):
    """全グループ終了時に main_ch へ自動投稿されるView。"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="上位N名でトーナメントへ",
        style=discord.ButtonStyle.success,
        custom_id="league_promote",
    )
    async def promote(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(PromoteToTournamentModal())
