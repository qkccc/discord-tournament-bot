# cogs/roundrobin/rr_views.py
import discord
import logging

log = logging.getLogger(__name__)

# ▼▼▼ 新規追加: 結果承認用のView ▼▼▼
class MatchResultApprovalView(discord.ui.View):
    """対戦結果の承認を行うView"""
    def __init__(self, message_id, match_id, team1_id, team2_id, team1_score, team2_score, reporter_team_id):
        super().__init__(timeout=None) # 永続化せずとも、Bot再起動で消えても再報告すれば良い運用とする
        self.message_id = message_id
        self.match_id = match_id
        self.team1_id = team1_id
        self.team2_id = team2_id
        self.team1_score = team1_score
        self.team2_score = team2_score
        self.reporter_team_id = reporter_team_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """ボタンを押せる人を制限する（対戦相手チーム or 管理者）"""
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return False

        # 報告者がTeam1なら、承認者はTeam2（逆も然り）
        opponent_team_id = self.team2_id if self.reporter_team_id == self.team1_id else self.team1_id
        
        # ユーザーが対戦相手チームに所属しているか確認
        is_opponent = cog.db.fetchone(
            "SELECT 1 FROM rr_players WHERE guild_id = ? AND user_id = ? AND team_id = ?", 
            (interaction.guild.id, interaction.user.id, opponent_team_id)
        )
        
        # 管理者権限があればOK
        is_admin = interaction.user.guild_permissions.manage_guild

        if is_opponent or is_admin:
            return True
        else:
            await interaction.response.send_message("❌ 対戦相手チームのメンバーのみが承認・却下できます。", ephemeral=True)
            return False

    @discord.ui.button(label="承認", style=discord.ButtonStyle.success)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return
        
        await interaction.response.defer()
        
        # 元のロジックを呼び出して結果を確定
        await cog.handle_score_report(
            interaction, self.message_id, self.match_id, 
            self.team1_id, self.team2_id, 
            self.team1_score, self.team2_score
        )
        
        # 承認メッセージを削除または更新
        await interaction.followup.edit_message(message_id=interaction.message.id, content=f"✅ 結果が承認されました！", view=None)

    @discord.ui.button(label="却下", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("結果報告を却下しました。入力内容を確認して、再度報告してください。", ephemeral=True)
        # 承認メッセージを削除
        await interaction.message.delete()
# ▲▲▲ 新規追加ここまで ▲▲▲


class ScoreReportModal(discord.ui.Modal, title='対戦結果報告'):
    def __init__(self, message_id: int, match_id: int, team1_id: int, team2_id: int, team1_name: str, team2_name: str):
        super().__init__()
        self.message_id = message_id
        self.match_id = match_id
        self.team1_id = team1_id
        self.team2_id = team2_id
        self.team1_name = team1_name
        self.team2_name = team2_name
        self.add_item(discord.ui.TextInput(label=f"{team1_name} の勝利数", placeholder="例: 3", required=True))
        self.add_item(discord.ui.TextInput(label=f"{team2_name} の勝利数", placeholder="例: 0", required=True))

    async def on_submit(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return await interaction.response.send_message("エラー: 機能がロードされていません。", ephemeral=True)
        
        try:
            t1_score = int(self.children[0].value)
            t2_score = int(self.children[1].value)
            if t1_score < 0 or t2_score < 0: raise ValueError()
        except (ValueError, TypeError):
            return await interaction.response.send_message("勝利数は0以上の半角数字で入力してください。", ephemeral=True)
        
        # 報告者のチームIDを取得
        reporter_team = cog.db.fetchone(
            "SELECT team_id FROM rr_players WHERE guild_id = ? AND user_id = ?", 
            (interaction.guild.id, interaction.user.id)
        )
        
        # 報告者がどちらのチームにも属していない場合（管理者など）は、承認スキップまたはTeam1扱いにするなど
        # ここでは便宜上、報告者が属するチームIDを渡すが、部外者の場合はTeam1扱いとして承認フローに乗せる
        reporter_team_id = reporter_team['team_id'] if reporter_team else self.team1_id

        # ▼▼▼ 変更点: いきなり保存せず、承認Viewを表示する ▼▼▼
        embed = discord.Embed(title="対戦結果の承認待ち", description="以下の結果が報告されました。対戦相手は内容を確認して承認してください。", color=discord.Color.orange())
        embed.add_field(name=self.team1_name, value=str(t1_score), inline=True)
        embed.add_field(name=self.team2_name, value=str(t2_score), inline=True)
        embed.set_footer(text="承認されると結果が確定します。")

        view = MatchResultApprovalView(
            self.message_id, self.match_id, 
            self.team1_id, self.team2_id, 
            t1_score, t2_score, 
            reporter_team_id
        )
        
        # 報告者にはEphemeralで見せない（全員に見えるようにして、相手チームにメンションを送るのがベストだが、今回はチャンネル送信）
        await interaction.response.send_message(embed=embed, view=view)
        # ▲▲▲ 変更ここまで ▲▲▲


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
            
            # 修正: Modalの引数が増えたため修正
            modal = ScoreReportModal(
                interaction.message.id, 
                match_data['match_id'], 
                match_data['team1_id'], 
                match_data['team2_id'], 
                match_data['team1_name'], 
                match_data['team2_name']
            )
            await interaction.response.send_modal(modal)
        except Exception as e:
            log.error(f"[RR] 結果報告ボタン処理中にエラー: {e}", exc_info=True)
            if not interaction.response.is_done(): await interaction.response.send_message("エラーが発生しました。", ephemeral=True)

# ... (CorrectResultView, NextRoundViewRR, CreateTeamModal, TeamSelect, TeamSelectView, RegistrationView は変更なし) ...
# 以下、既存コードをそのままコピーしてください

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
            # 訂正時もモーダル経由で承認フローへ（誤報告防止のため）
            modal = ScoreReportModal(
                interaction.message.id, 
                match_data['match_id'], 
                match_data['team1_id'], 
                match_data['team2_id'], 
                match_data['team1_name'], 
                match_data['team2_name']
            )
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

class CreateTeamModal(discord.ui.Modal, title='チーム作成'):
    team_name = discord.ui.TextInput(label="チーム名", placeholder="新しいチームの名前を入力してください", required=True, max_length=50)
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if cog: await cog.handle_create_team(interaction, self.team_name.value)

class TeamSelect(discord.ui.Select):
    def __init__(self, cog, teams):
        options = [discord.SelectOption(label=team['name'], value=str(team['team_id'])) for team in teams]
        if not options: options.append(discord.SelectOption(label="参加可能なチームがありません", value="disabled", default=True))
        super().__init__(placeholder="参加したいチームを選択してください...", min_values=1, max_values=1, options=options, disabled=(not teams))
        self.cog = cog
    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "disabled": return await interaction.response.edit_message(content="現在参加可能なチームはありません。", view=None)
        await interaction.response.defer(ephemeral=True, thinking=True)
        team_id = int(self.values[0])
        await self.cog.handle_join_team(interaction, team_id)
        await interaction.edit_original_response(content="処理が完了しました。", view=None)

class TeamSelectView(discord.ui.View):
    def __init__(self, cog, teams):
        super().__init__(timeout=180)
        self.add_item(TeamSelect(cog, teams))

class RegistrationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="チーム作成", style=discord.ButtonStyle.success, custom_id="rr_create_team")
    async def create_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(CreateTeamModal())
    @discord.ui.button(label="チームに参加", style=discord.ButtonStyle.primary, custom_id="rr_join_team")
    async def join_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if not cog: return await interaction.followup.send("エラー: 機能がロードされていません。", ephemeral=True)
        teams = await cog.get_joinable_teams(interaction.guild.id)
        if not teams: return await interaction.followup.send("現在参加できるチームがありません。", ephemeral=True)
        view = TeamSelectView(cog, teams)
        await interaction.followup.send("参加するチームを選んでください:", view=view, ephemeral=True)
    @discord.ui.button(label="チームから脱退", style=discord.ButtonStyle.danger, custom_id="rr_leave_team")
    async def leave_team(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        cog = interaction.client.get_cog("RoundRobin")
        if cog: await cog.handle_leave_team(interaction)