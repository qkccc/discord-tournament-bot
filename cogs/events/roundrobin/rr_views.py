# cogs/roundrobin/rr_views.py
import discord
import logging

log = logging.getLogger(__name__)

class ScoreReportModal(discord.ui.Modal, title='対戦結果報告'):
    def __init__(self, message_id: int, match_id: int, team1_id: int, team2_id: int, team1_name: str, team2_name: str):
        super().__init__()
        self.message_id = message_id
        self.match_id = match_id
        self.team1_id = team1_id
        self.team2_id = team2_id
        self.add_item(discord.ui.TextInput(label=f"{team1_name} の勝利数", placeholder="例: 3", required=True))
        self.add_item(discord.ui.TextInput(label=f"{team2_name} の勝利数", placeholder="例: 0", required=True))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        try:
            team1_score = int(self.children[0].value)
            team2_score = int(self.children[1].value)
            if team1_score < 0 or team2_score < 0: raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message("勝利数は0以上の半角数字で入力してください。", ephemeral=True)
        
        await interaction.response.defer(ephemeral=True)
        await cog.handle_score_report(interaction, self.message_id, self.match_id, self.team1_id, self.team2_id, team1_score, team2_score)

class ReportResultView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="結果報告", style=discord.ButtonStyle.primary, custom_id="persistent_rr_report_button")
    async def report_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        try:
            query = "SELECT m.match_id, m.team1_id, m.team2_id, t1.name as team1_name, t2.name as team2_name FROM rr_matches m JOIN rr_teams t1 ON m.team1_id = t1.team_id JOIN rr_teams t2 ON m.team2_id = t2.team_id WHERE m.message_id = ?"
            match_data = cog.db.fetchone(query, (interaction.message.id,))
            if not match_data: return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
            modal = ScoreReportModal(interaction.message.id, match_data['match_id'], match_data['team1_id'], match_data['team2_id'], match_data['team1_name'], match_data['team2_name'])
            await interaction.response.send_modal(modal)
        except Exception as e:
            log.error(f"[RR] 結果報告ボタン処理中にエラー: {e}", exc_info=True)
            if not interaction.response.is_done(): await interaction.response.send_message("エラーが発生しました。", ephemeral=True)

class CorrectResultView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="結果を訂正", style=discord.ButtonStyle.danger, custom_id="persistent_rr_correct_button")
    async def correct_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        try:
            query = "SELECT m.match_id, m.team1_id, m.team2_id, t1.name as team1_name, t2.name as team2_name FROM rr_matches m JOIN rr_teams t1 ON m.team1_id = t1.team_id JOIN rr_teams t2 ON m.team2_id = t2.team_id WHERE m.message_id = ?"
            match_data = cog.db.fetchone(query, (interaction.message.id,))
            if not match_data: return await interaction.response.send_message("エラー: 対戦カード情報が見つかりません。", ephemeral=True)
            modal = ScoreReportModal(interaction.message.id, match_data['match_id'], match_data['team1_id'], match_data['team2_id'], match_data['team1_name'], match_data['team2_name'])
            await interaction.response.send_modal(modal)
        except Exception as e:
            log.error(f"[RR] 結果訂正ボタン処理中にエラー: {e}", exc_info=True)
            if not interaction.response.is_done(): await interaction.response.send_message("エラーが発生しました。", ephemeral=True)

class NextRoundViewRR(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="次節へ進む", style=discord.ButtonStyle.success, custom_id="rr_next_round")
    async def next_round_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog:
            return await interaction.response.send_message("エラー: 総当たり戦機能がロードされていません。", ephemeral=True)
        
        button.disabled = True
        await interaction.message.edit(view=self)
        await cog._execute_next_round(interaction)

# ▼▼▼ 新規追加: チーム登録用のUIコンポーネント ▼▼▼
class CreateTeamModal(discord.ui.Modal, title='チーム作成'):
    """チーム名を入力するためのモーダル"""
    team_name = discord.ui.TextInput(
        label="チーム名",
        placeholder="新しいチームの名前を入力してください",
        required=True,
        max_length=50
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if cog:
            await cog.handle_create_team(interaction, self.team_name.value)

class TeamSelect(discord.ui.Select):
    """参加するチームを選択するためのドロップダウン"""
    def __init__(self, cog, teams):
        options = [discord.SelectOption(label=team['name'], value=str(team['team_id'])) for team in teams]
        if not options:
            options.append(discord.SelectOption(label="参加可能なチームがありません", value="disabled", default=True))
        
        super().__init__(placeholder="参加したいチームを選択してください...", min_values=1, max_values=1, options=options, disabled=(not teams))
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "disabled":
            return await interaction.response.edit_message(content="現在参加可能なチームはありません。", view=None)
        
        await interaction.response.defer(ephemeral=True, thinking=True)
        team_id = int(self.values[0])
        await self.cog.handle_join_team(interaction, team_id)
        await interaction.edit_original_response(content="処理が完了しました。", view=None)


class TeamSelectView(discord.ui.View):
    """TeamSelectドロップダウンを持つView"""
    def __init__(self, cog, teams):
        super().__init__(timeout=180)
        self.add_item(TeamSelect(cog, teams))

class RegistrationView(discord.ui.View):
    """チーム作成、参加、脱退のボタンを持つ永続View"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="チーム作成", style=discord.ButtonStyle.success, custom_id="rr_create_team")
    async def create_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateTeamModal())

    @discord.ui.button(label="チームに参加", style=discord.ButtonStyle.primary, custom_id="rr_join_team")
    async def join_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if not cog:
            return await interaction.followup.send("エラー: 機能がロードされていません。", ephemeral=True)
        
        teams = await cog.get_joinable_teams(interaction.guild.id)
        if not teams:
            return await interaction.followup.send("現在参加できるチームがありません。", ephemeral=True)
            
        view = TeamSelectView(cog, teams)
        await interaction.followup.send("参加するチームを選んでください:", view=view, ephemeral=True)

    @discord.ui.button(label="チームから脱退", style=discord.ButtonStyle.danger, custom_id="rr_leave_team")
    async def leave_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if cog:
            await cog.handle_leave_team(interaction)
# ▲▲▲ 新規追加ここまで ▲▲▲
