# cogs/tournament/views.py
import discord
from typing import List, Optional
from .models import Player, DummyPlayer
from typing import Union

# ==============================================================================
# シングルエリミネーショントーナメント用のView
# ==============================================================================
class ResultReportView(discord.ui.View):
    """
    試合結果を報告するためのView。
    custom_idにmatch_idを含めることで、どの試合の結果かを識別する。
    """
    def __init__(self, guild_id: int, match_id: str, p1: Union[Player, DummyPlayer], p2: Union[Player, DummyPlayer]):
        super().__init__(timeout=None)
        
        # custom_id に match_id を含める
        p1_button = discord.ui.Button(
            label=f"{p1.display_name} 勝利", 
            style=discord.ButtonStyle.green, 
            custom_id=f"se_win:{guild_id}:{match_id}:{p1.id}:{p2.id}"
        )
        p2_button = discord.ui.Button(
            label=f"{p2.display_name} 勝利", 
            style=discord.ButtonStyle.primary, 
            custom_id=f"se_win:{guild_id}:{match_id}:{p2.id}:{p1.id}"
        )

        p1_button.callback = self.button_callback
        p2_button.callback = self.button_callback
        
        self.add_item(p1_button)
        self.add_item(p2_button)

    async def button_callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("EventManager")
        if not cog:
            return await interaction.response.send_message("エラー: 大会機能がロードされていません。", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        
        parts = interaction.data['custom_id'].split(':')
        action, guild_id, match_id, winner_id, loser_id = parts
        
        if action == "se_win":
            await cog._handle_se_win_logic(interaction, int(guild_id), match_id, int(winner_id), int(loser_id))
        # スイスドロー用の "swiss_win" の処理は cog.py の _handle_win_logic 内で完結しているため、
        # このViewではSEトーナメント専用の処理のみ記述しています。

# ==============================================================================
# ▼▼▼ スイスドロー機能で必要なViewを追加 ▼▼▼
# ==============================================================================
class NextRoundView(discord.ui.View):
    """スイスドローで次のラウンドに進むためのボタンを持つView"""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="次ラウンドへ進む", style=discord.ButtonStyle.primary, custom_id="next_round")
    async def next_round_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        cog = interaction.client.get_cog("EventManager")
        if not cog:
            return await interaction.response.send_message("エラー: 大会機能がロードされていません。", ephemeral=True)
        
        button.disabled = True
        await interaction.message.edit(view=self)
        await cog._execute_next_round(interaction)

# ==============================================================================
# 募集やチーム分け、その他の機能で使われるView
# ==============================================================================

class ConfirmCancelView(discord.ui.View):
    """中止の最終確認を行うView"""
    def __init__(self, cog_instance):
        super().__init__(timeout=60)
        self.cog = cog_instance
        self.message: Optional[discord.Message] = None

    @discord.ui.button(label="はい、中止します", style=discord.ButtonStyle.danger)
    async def confirm_cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="中止しています...", view=self)
        await self.cog._execute_cancel(interaction)
        self.stop()

    @discord.ui.button(label="いいえ", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        for item in self.children:
            item.disabled = True
        await interaction.response.edit_message(content="中止をキャンセルしました。", view=self)
        self.stop()

    async def on_timeout(self):
        if self.message:
            for item in self.children:
                item.disabled = True
            await self.message.edit(content="タイムアウトしました。中止処理はキャンセルされました。", view=self)

class StartSwissModal(discord.ui.Modal, title='スイスドロー開始設定'):
    """スイスドローのラウンド数を入力するモーダル"""
    rounds_input = discord.ui.TextInput(label="最大ラウンド数 (任意)", placeholder="未入力の場合は全勝者が1人になるまで続行します。", required=False)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        rounds = 0
        if self.rounds_input.value and self.rounds_input.value.strip():
            try:
                rounds = int(self.rounds_input.value)
                if rounds < 0:
                    raise ValueError
            except (ValueError, TypeError):
                return await interaction.followup.send("ラウンド数は0以上の整数で入力してください。", ephemeral=True)
        # MainControlViewのインスタンス経由でcogのメソッドを呼び出す
        await self.view.cog._execute_start_swiss(interaction, rounds)


class TeamNumberModal(discord.ui.Modal, title='チーム数で分ける'):
    """チーム分けでチーム数を入力するモーダル"""
    num_teams_input = discord.ui.TextInput(label="チーム数", placeholder="例: 3", required=True)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        try:
            num_teams = int(self.num_teams_input.value)
            if num_teams <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return await interaction.followup.send("チーム数は1以上の半角数字で入力してください。", ephemeral=True)
        await self.view.cog._execute_number_team_split(interaction, num_teams)

class TeamLeaderSelectView(discord.ui.View):
    """チーム分けでリーダーを選択するView"""
    def __init__(self, cog_instance, participants: List[discord.Member]):
        super().__init__(timeout=300)
        self.cog = cog_instance

        options = [discord.SelectOption(label=p.display_name, value=str(p.id)) for p in participants]
        max_options = min(len(options), 25)

        self.leader_select = discord.ui.Select(
            placeholder="リーダーを選択してください...",
            min_values=1,
            max_values=max_options,
            options=options[:max_options]
        )
        self.leader_select.callback = self.select_callback
        self.add_item(self.leader_select)

        self.confirm_button = discord.ui.Button(label="確定", style=discord.ButtonStyle.success, disabled=True)
        self.confirm_button.callback = self.confirm_callback
        self.add_item(self.confirm_button)

    async def select_callback(self, interaction: discord.Interaction):
        self.confirm_button.disabled = False
        await interaction.response.edit_message(view=self)

    async def confirm_callback(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_ids = self.leader_select.values
        leaders = [interaction.guild.get_member(int(uid)) for uid in selected_ids]
        await self.cog._execute_leader_team_split(interaction, leaders)

class TeamSplitMethodView(discord.ui.View):
    """チーム分けの方法を選択するView"""
    def __init__(self, cog_instance):
        super().__init__(timeout=180)
        self.cog = cog_instance

    @discord.ui.button(label="リーダー制", style=discord.ButtonStyle.primary)
    async def by_leader(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.recruit_sessions.get(interaction.guild.id)
        if not session or not session['participants']:
            return await interaction.response.send_message("参加者がいません。", ephemeral=True)

        # このメッセージはエフェメラルなので interaction.message.edit() では編集できない
        # （通常のチャンネルメッセージ編集APIを叩いてしまい 404 Unknown Message になる）。
        # このinteraction自体の応答として edit_message するのが正しいやり方。
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(view=self)

        view = TeamLeaderSelectView(self.cog, list(session['participants']))
        await interaction.followup.send("ドロップダウンメニューでリーダーを選択し、「確定」を押してください。", view=view, ephemeral=True)

    @discord.ui.button(label="チーム数で分ける", style=discord.ButtonStyle.secondary)
    async def by_number(self, interaction: discord.Interaction, button: discord.ui.Button):
        # モーダル表示(send_modal)がこのinteractionへの応答そのものになるため、
        # 同じinteractionでこのメッセージを編集することはできない（ボタンの無効化は行わない）。
        modal = TeamNumberModal()
        modal.view = self
        await interaction.response.send_modal(modal)


class TeamRenameModal(discord.ui.Modal, title="チーム名を変更"):
    """TeamNamingView から呼び出される、1チーム分のチーム名変更モーダル"""
    def __init__(self, parent_view: "TeamNamingView", team_index: int):
        super().__init__()
        self.parent_view = parent_view
        self.team_index = team_index
        current_name = parent_view.teams[team_index]["name"]
        self.name_input = discord.ui.TextInput(
            label="新しいチーム名", default=current_name, max_length=80, required=True
        )
        self.add_item(self.name_input)

    async def on_submit(self, interaction: discord.Interaction):
        new_name = self.name_input.value.strip()
        if not new_name:
            return await interaction.response.send_message("チーム名を入力してください。", ephemeral=True)
        other_names = [t["name"] for i, t in enumerate(self.parent_view.teams) if i != self.team_index]
        if new_name in other_names:
            return await interaction.response.send_message(f"チーム名「{new_name}」は他のチームと重複しています。", ephemeral=True)
        self.parent_view.teams[self.team_index]["name"] = new_name
        self.parent_view._refresh_select()
        await interaction.response.edit_message(embed=self.parent_view._build_embed(), view=self.parent_view)


class TeamNamingView(discord.ui.View):
    """チーム分け結果のチーム名を確認・変更してから、チーム総当たり戦を開始するView"""
    def __init__(self, cog_instance, teams: list):
        super().__init__(timeout=600)
        self.cog = cog_instance
        self.teams = teams  # [{"name": str, "members": [discord.Member | DummyPlayer, ...]}, ...]

        self.team_select = discord.ui.Select(placeholder="チーム名を変更するチームを選択...")
        self.team_select.callback = self._on_select
        self.add_item(self.team_select)
        self._refresh_select()

        self.start_button = discord.ui.Button(
            label="⚔️ この内容で総当たり戦を開始", style=discord.ButtonStyle.success, row=1
        )
        self.start_button.callback = self._on_start
        self.add_item(self.start_button)

    def _refresh_select(self):
        self.team_select.options = [
            discord.SelectOption(label=t["name"], value=str(i), description=f"{len(t['members'])}人")
            for i, t in enumerate(self.teams)
        ][:25]

    def _build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="⚔️ チーム総当たり戦 開始前確認",
            description="プルダウンからチームを選ぶとチーム名を変更できます。よろしければ下のボタンで開始してください。",
            color=0xF1C40F,
        )
        for t in self.teams:
            embed.add_field(
                name=f"**{t['name']}** ({len(t['members'])}人)",
                value="\n".join(f"・{m.display_name}" for m in t["members"]),
                inline=False,
            )
        return embed

    async def _on_select(self, interaction: discord.Interaction):
        team_index = int(self.team_select.values[0])
        await interaction.response.send_modal(TeamRenameModal(self, team_index))

    async def _on_start(self, interaction: discord.Interaction):
        await interaction.response.defer()

        for item in self.children: item.disabled = True
        try:
            await interaction.edit_original_response(embed=self._build_embed(), view=self)
        except discord.HTTPException:
            pass

        rr_cog = self.cog.bot.get_cog("RoundRobin")
        if not rr_cog:
            return await interaction.followup.send("❌ チーム総当たり戦機能が読み込まれていません。", ephemeral=True)

        teams_dict = {t["name"]: t["members"] for t in self.teams}
        ok, msg = await rr_cog.start_from_teams(interaction.guild, interaction.channel, teams_dict)
        if not ok:
            await interaction.followup.send(f"❌ {msg}", ephemeral=True)


class TeamSplitToRoundRobinView(discord.ui.View):
    """チーム分け結果から、そのままチーム総当たり戦へシームレスに移行するためのView"""
    def __init__(self, cog_instance, teams: list):
        super().__init__(timeout=600)
        self.cog = cog_instance
        self.teams = teams  # [{"name": str, "members": [...]}, ...]

    @discord.ui.button(label="⚔️ この分け方で総当たり戦を開始", style=discord.ButtonStyle.success)
    async def start_rr(self, interaction: discord.Interaction, button: discord.ui.Button):
        rr_cog = self.cog.bot.get_cog("RoundRobin")
        if not rr_cog:
            return await interaction.response.send_message("❌ チーム総当たり戦機能が読み込まれていません。", ephemeral=True)

        naming_view = TeamNamingView(self.cog, [dict(t) for t in self.teams])
        await interaction.response.send_message(
            embed=naming_view._build_embed(), view=naming_view, ephemeral=True
        )

        for item in self.children: item.disabled = True
        try:
            await interaction.message.edit(view=self)
        except (discord.NotFound, discord.HTTPException):
            pass


class MainControlView(discord.ui.View):
    """募集中のメインコントロールパネル"""
    def __init__(self, cog_instance):
        super().__init__(timeout=None)
        self.cog = cog_instance

    async def _check_participants(self, interaction: discord.Interaction, min_players: int) -> bool:
        participants = self.cog.recruit_sessions.get(interaction.guild.id, {}).get('participants', set())
        if len(participants) < min_players:
            await interaction.response.send_message(f"参加者が{min_players}人未満のため、実行できません。", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="スイスドロー", style=discord.ButtonStyle.success, custom_id="main_swiss")
    async def start_swiss(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_participants(interaction, 2): return
        modal = StartSwissModal()
        modal.view = self # モーダルにviewインスタンスを渡す
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="トーナメント", style=discord.ButtonStyle.primary, custom_id="main_bracket")
    async def start_bracket(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_participants(interaction, 2): return
        await self.cog._execute_bracket(interaction)

    @discord.ui.button(label="チーム分け", style=discord.ButtonStyle.secondary, custom_id="main_teams")
    async def start_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not await self._check_participants(interaction, 1): return
        view = TeamSplitMethodView(self.cog)
        await interaction.response.send_message("チーム分けの方法を選択してください。", view=view, ephemeral=True)

    @discord.ui.button(label="募集を中止", style=discord.ButtonStyle.danger, custom_id="main_cancel_recruit")
    async def cancel_recruitment(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer()
        await self.cog._close_recruitment(interaction, "募集を中止しました。")

class SwissResultReportView(discord.ui.View):
    """スイスドローの結果報告・取り消しを行うためのView"""
    def __init__(self, guild_id: int, p1: Player, p2: Player):
        super().__init__(timeout=None)
        # custom_id を "swiss_win" と "swiss_undo" に設定
        p1_button = discord.ui.Button(label=f"{p1.display_name} 勝利", style=discord.ButtonStyle.green, custom_id=f"swiss_win:{guild_id}:{p1.id}:{p2.id}")
        p2_button = discord.ui.Button(label=f"{p2.display_name} 勝利", style=discord.ButtonStyle.red, custom_id=f"swiss_win:{guild_id}:{p2.id}:{p1.id}")
        undo_button = discord.ui.Button(label="結果取消", style=discord.ButtonStyle.grey, custom_id=f"swiss_undo:{guild_id}:{p1.id}:{p2.id}")

        p1_button.callback = self.button_callback
        p2_button.callback = self.button_callback
        undo_button.callback = self.button_callback

        self.add_item(p1_button)
        self.add_item(p2_button)
        self.add_item(undo_button)

    async def button_callback(self, interaction: discord.Interaction):
        cog = interaction.client.get_cog("EventManager")
        if not cog:
            return await interaction.response.send_message("エラー: 大会機能がロードされていません。", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        parts = interaction.data['custom_id'].split(':')
        action_type = parts[0]

        # Cogにあるそれぞれの処理を呼び出す
        if action_type == "swiss_win":
            guild_id, winner_id, loser_id = map(int, parts[1:])
            await cog._handle_swiss_win_logic(interaction, guild_id, winner_id, loser_id)
        elif action_type == "swiss_undo":
            guild_id, p1_id, p2_id = map(int, parts[1:])
            await cog._handle_swiss_undo_logic(interaction, guild_id, p1_id, p2_id)