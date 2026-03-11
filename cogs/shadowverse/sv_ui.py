# cogs/shadowverse/sv_ui.py
import discord
from discord import ui, SelectOption, CategoryChannel, PartialEmoji
import datetime
import asyncio
import typing

from .sv_constants import (
    CLASS_NAMES,
    RESULTS,
    TURN_ORDERS,
    CLASS_EMOJI_MAP,
    TARGET_CATEGORY_ID,
)
from .sv_db import (
    save_records_to_db,
    save_user_class_archetype,
    get_user_class_archetypes,
    set_user_channel_setting,
    delete_match_record,
    delete_all_user_records,
    get_user_channel_setting,
    get_guild_season_start_date,
)
from .sv_utils import get_stats_summary, get_recent_matches

if typing.TYPE_CHECKING:
    from .main import ShadowverseCog


class ManualRecordView(ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180.0)
        self.author_id = author_id
        self.my_class = None
        self.my_archetype = None
        self.opponent_class = None
        self.opponent_archetype = None
        self.result = None
        self.turn_order = "不明"
        self.my_archetype_choices: list[str] = []
        self.opponent_archetype_choices: list[str] = []
        self.current_selection = "my_class"
        self.update_view()

    def update_view(self):
        self.clear_items()
        if self.current_selection == "my_class":
            self.add_class_buttons("my_class")
        elif self.current_selection == "my_archetype":
            self.add_archetype_buttons("my")
        elif self.current_selection == "opponent_class":
            self.add_class_buttons("opponent_class")
        elif self.current_selection == "opponent_archetype":
            self.add_archetype_buttons("opponent")
        elif self.current_selection == "result":
            self.add_choice_buttons(
                "result",
                RESULTS,
                [discord.ButtonStyle.success, discord.ButtonStyle.danger],
            )
        elif self.current_selection == "turn_order":
            self.add_choice_buttons(
                "turn_order",
                TURN_ORDERS,
                [
                    discord.ButtonStyle.primary,
                    discord.ButtonStyle.primary,
                    discord.ButtonStyle.secondary,
                ],
            )
        elif self.current_selection == "confirm":
            self.add_confirm_buttons()

    def add_class_buttons(self, selection_type: str):
        for class_name in CLASS_NAMES:
            emoji_info = CLASS_EMOJI_MAP.get(class_name)
            emoji = (
                PartialEmoji(name=emoji_info[1], id=emoji_info[0])
                if emoji_info
                else None
            )
            button = ui.Button(
                label=class_name,
                emoji=emoji,
                custom_id=f"manual_record_class:{selection_type}:{class_name}",
                style=discord.ButtonStyle.secondary,
            )
            button.callback = self.on_button_click
            self.add_item(button)

    def add_choice_buttons(self, selection_type: str, choices: list, styles: list):
        for i, choice in enumerate(choices):
            button = ui.Button(
                label=choice,
                custom_id=f"manual_record_choice:{selection_type}:{choice}",
                style=styles[i],
            )
            button.callback = self.on_button_click
            self.add_item(button)

    def add_archetype_buttons(self, target: str):
        choices = (
            self.my_archetype_choices
            if target == "my"
            else self.opponent_archetype_choices
        )
        for idx, archetype in enumerate(choices):
            button = ui.Button(
                label=archetype,
                style=discord.ButtonStyle.secondary,
                custom_id=f"manual_record_saved_archetype:{target}:{idx}",
            )
            button.callback = self.on_saved_archetype_button_click
            self.add_item(button)

        input_label = "アーキタイプを新規入力" if choices else "アーキタイプを入力"
        input_button = ui.Button(
            label=input_label,
            style=discord.ButtonStyle.primary,
            custom_id=f"manual_record_archetype:{target}:input",
        )
        input_button.callback = self.on_archetype_button_click
        self.add_item(input_button)

        skip_button = ui.Button(
            label="未入力で進む",
            style=discord.ButtonStyle.secondary,
            custom_id=f"manual_record_archetype:{target}:skip",
        )
        skip_button.callback = self.on_archetype_button_click
        self.add_item(skip_button)

    async def on_saved_archetype_button_click(self, interaction: discord.Interaction):
        _, target, index_text = interaction.data["custom_id"].split(":")
        index = int(index_text)
        choices = (
            self.my_archetype_choices
            if target == "my"
            else self.opponent_archetype_choices
        )
        if not (0 <= index < len(choices)):
            await interaction.response.send_message(
                "❌ アーキタイプ選択に失敗しました。もう一度選択してください。",
                ephemeral=True,
            )
            return

        selected = choices[index]
        if target == "my":
            self.my_archetype = selected
            self.current_selection = "opponent_class"
        else:
            self.opponent_archetype = selected
            self.current_selection = "result"

        self.update_view()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    async def on_archetype_button_click(self, interaction: discord.Interaction):
        _, target, action = interaction.data["custom_id"].split(":")
        if action == "skip":
            if target == "my":
                self.my_archetype = None
                self.current_selection = "opponent_class"
            else:
                self.opponent_archetype = None
                self.current_selection = "result"
            self.update_view()
            await interaction.response.edit_message(
                embed=self.create_embed(), view=self
            )
            return

        modal = ArchetypeInputModal(self, target=target)
        await interaction.response.send_modal(modal)

    async def _load_archetype_choices(self, target: str, class_name: str | None):
        if not class_name:
            if target == "my":
                self.my_archetype_choices = []
            else:
                self.opponent_archetype_choices = []
            return

        choices = await get_user_class_archetypes(self.author_id, class_name)
        if target == "my":
            self.my_archetype_choices = choices
        else:
            self.opponent_archetype_choices = choices

    def add_confirm_buttons(self):
        continue_button = ui.Button(
            label="登録して続ける",
            style=discord.ButtonStyle.primary,
            custom_id="manual_record_confirm:continue",
        )
        continue_button.callback = self.on_register
        self.add_item(continue_button)
        register_button = ui.Button(
            label="登録して終了",
            style=discord.ButtonStyle.success,
            custom_id="manual_record_confirm:final",
        )
        register_button.callback = self.on_register
        self.add_item(register_button)
        cancel_button = ui.Button(
            label="キャンセル",
            style=discord.ButtonStyle.danger,
            custom_id="manual_record_confirm:cancel",
        )
        cancel_button.callback = self.on_register
        self.add_item(cancel_button)

    async def on_button_click(self, interaction: discord.Interaction):
        custom_id_parts = interaction.data["custom_id"].split(":")
        selection_type, value = custom_id_parts[1], custom_id_parts[2]
        setattr(self, selection_type, value)
        if self.current_selection == "my_class":
            self.current_selection = "my_archetype"
            await self._load_archetype_choices("my", self.my_class)
        elif self.current_selection == "my_archetype":
            self.current_selection = "opponent_class"
        elif self.current_selection == "opponent_class":
            self.current_selection = "opponent_archetype"
            await self._load_archetype_choices("opponent", self.opponent_class)
        elif self.current_selection == "opponent_archetype":
            self.current_selection = "result"
        elif self.current_selection == "result":
            self.current_selection = "turn_order"
        elif self.current_selection == "turn_order":
            self.current_selection = "confirm"
        self.update_view()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)

    async def on_register(self, interaction: discord.Interaction):
        action = interaction.data["custom_id"].split(":")[1]
        if action == "cancel":
            await interaction.response.edit_message(
                content="登録をキャンセルしました。", embed=None, view=None
            )
            return
        record = {
            "match_time": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"),
            "my_class": self.my_class,
            "my_archetype": self.my_archetype,
            "opponent_class": self.opponent_class,
            "opponent_archetype": self.opponent_archetype,
            "result": self.result,
            "turn_order": self.turn_order,
        }
        try:
            # 修正: 非同期関数なので直接 await
            await save_records_to_db(self.author_id, [record])

            if action == "continue":
                self.opponent_class = None
                self.opponent_archetype = None
                self.result = None
                self.turn_order = "不明"
                self.current_selection = (
                    "opponent_class" if self.my_class else "my_class"
                )
                self.update_view()
                await interaction.response.edit_message(
                    content="✅ 1件登録しました。続けて次の対戦を入力してください。",
                    embed=self.create_embed(),
                    view=self,
                )
            elif action == "final":
                final_embed = self.create_embed()
                final_embed.title = "✅ 戦績を登録しました"
                final_embed.description = "新しい戦績が記録されました。"
                await interaction.response.edit_message(
                    content=None, embed=final_embed, view=None
                )
        except Exception as e:
            print(f"[/record] 手動登録の保存中にエラー: {e}")
            await interaction.response.edit_message(
                content="❌ 登録に失敗しました。", embed=None, view=None
            )

    def get_class_display(self, class_name: str | None) -> str:
        if not class_name:
            return "未選択"
        emoji_info = CLASS_EMOJI_MAP.get(class_name)
        return (
            f"{PartialEmoji(name=emoji_info[1], id=emoji_info[0])} **{class_name}**"
            if emoji_info
            else f"**{class_name}**"
        )

    def create_embed(self):
        prompts = {
            "my_class": "自分のクラスを選択してください",
            "my_archetype": "デッキのアーキタイプを入力してください（任意）",
            "opponent_class": "相手のクラスを選択してください",
            "opponent_archetype": "相手のデッキアーキタイプを入力してください（任意）",
            "result": "勝敗を選択してください",
            "turn_order": "先攻/後攻を選択してください",
            "confirm": "内容を確認して登録してください",
        }
        embed = discord.Embed(
            title="戦績手動登録", description=f"**➡️ {prompts[self.current_selection]}**"
        )
        embed.add_field(
            name="自分のクラス",
            value=self.get_class_display(self.my_class),
            inline=True,
        )
        embed.add_field(
            name="自分のアーキタイプ",
            value=f"**{self.my_archetype}**" if self.my_archetype else "未入力",
            inline=True,
        )
        embed.add_field(
            name="相手のクラス",
            value=self.get_class_display(self.opponent_class),
            inline=True,
        )
        embed.add_field(
            name="相手のアーキタイプ",
            value=f"**{self.opponent_archetype}**"
            if self.opponent_archetype
            else "未入力",
            inline=True,
        )
        embed.add_field(
            name="結果",
            value=f"**{self.result}**" if self.result else "未選択",
            inline=True,
        )
        embed.add_field(
            name="先攻/後攻",
            value=f"**{self.turn_order}**" if self.turn_order != "不明" else "未選択",
            inline=True,
        )
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "この操作はコマンドを実行した本人しか行えません。", ephemeral=True
            )
            return False
        return True


class ArchetypeInputModal(ui.Modal, title="アーキタイプ入力"):
    archetype = ui.TextInput(
        label="デッキのアーキタイプ",
        placeholder="例: 財宝 / 守護 / 連携",
        required=False,
        max_length=30,
    )

    def __init__(self, parent_view: ManualRecordView, target: str):
        super().__init__()
        self.parent_view = parent_view
        self.target = target
        if target == "my" and parent_view.my_archetype:
            self.archetype.default = parent_view.my_archetype
        elif target == "opponent" and parent_view.opponent_archetype:
            self.archetype.default = parent_view.opponent_archetype

    async def on_submit(self, interaction: discord.Interaction):
        value = str(self.archetype.value).strip()
        if self.target == "my":
            self.parent_view.my_archetype = value or None
            self.parent_view.current_selection = "opponent_class"
            if self.parent_view.my_class and value:
                await save_user_class_archetype(
                    self.parent_view.author_id, self.parent_view.my_class, value
                )
                await self.parent_view._load_archetype_choices(
                    "my", self.parent_view.my_class
                )
        else:
            self.parent_view.opponent_archetype = value or None
            self.parent_view.current_selection = "result"
            if self.parent_view.opponent_class and value:
                await save_user_class_archetype(
                    self.parent_view.author_id, self.parent_view.opponent_class, value
                )
                await self.parent_view._load_archetype_choices(
                    "opponent", self.parent_view.opponent_class
                )

        self.parent_view.update_view()
        await interaction.response.defer()
        await interaction.edit_original_response(
            embed=self.parent_view.create_embed(),
            view=self.parent_view,
        )


class ChannelSelectView(ui.View):
    def __init__(self, channels: list[discord.TextChannel], author_id: int):
        super().__init__(timeout=120.0)
        self.author_id = author_id
        channel_chunks = [channels[i : i + 25] for i in range(0, len(channels), 25)]
        for i, chunk in enumerate(channel_chunks):
            if not chunk:
                continue
            options = [SelectOption(label=ch.name, value=str(ch.id)) for ch in chunk]
            placeholder = (
                f"通知先チャンネルを選択 ({chunk[0].name} ～ {chunk[-1].name})"
                if len(channel_chunks) > 1
                else "通知先にしたいチャンネルを選択してください..."
            )
            select_menu = ui.Select(
                placeholder=placeholder,
                options=options,
                custom_id=f"channel_select_menu_{i}",
            )
            select_menu.callback = self.on_select_submit
            self.add_item(select_menu)

    async def on_select_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_channel_id = int(interaction.data["values"][0])
        # 修正: 非同期関数なので直接 await
        await set_user_channel_setting(interaction.user.id, selected_channel_id)
        selected_channel = interaction.guild.get_channel(selected_channel_id)
        for item in self.children:
            item.disabled = True
        await interaction.edit_original_response(
            content=f"✅ 通知チャンネルを {selected_channel.mention} に設定しました。",
            view=self,
        )
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "この操作はコマンドを実行した本人しか行えません。", ephemeral=True
            )
            return False
        return True


class StatsOptionsView(ui.View):
    def __init__(
        self, original_interaction: discord.Interaction, cog: "ShadowverseCog"
    ):
        super().__init__(timeout=120.0)
        self.original_interaction = original_interaction
        self.cog = cog
        self.period = "today"
        self.class_name = None
        self.period_select = ui.Select(
            placeholder="集計期間を選択...",
            options=[
                SelectOption(label="今日", value="today", default=True),
                SelectOption(label="昨日", value="yesterday"),
                SelectOption(label="一週間", value="week"),
                SelectOption(label="今期(設定日~)", value="season"),
                SelectOption(label="全期間", value="all"),
            ],
            custom_id="stats_opt_period",
        )
        self.period_select.callback = self.on_period_select
        self.add_item(self.period_select)
        class_options = [
            SelectOption(label="全てのクラス", value="all_classes", default=True)
        ] + [SelectOption(label=name, value=name) for name in CLASS_NAMES]
        self.class_select = ui.Select(
            placeholder="クラスを選択...",
            options=class_options,
            custom_id="stats_opt_class",
        )
        self.class_select.callback = self.on_class_select
        self.add_item(self.class_select)

    async def on_period_select(self, interaction: discord.Interaction):
        self.period = interaction.data["values"][0]
        for option in self.period_select.options:
            option.default = option.value == self.period
        await interaction.response.edit_message(view=self)

    async def on_class_select(self, interaction: discord.Interaction):
        value = interaction.data["values"][0]
        self.class_name = value if value != "all_classes" else None
        for option in self.class_select.options:
            option.default = option.value == value
        await interaction.response.edit_message(view=self)

    @ui.button(
        label="結果を表示",
        style=discord.ButtonStyle.success,
        custom_id="stats_opt_submit",
    )
    async def submit(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(thinking=True, ephemeral=True)
        season_start_date = (
            await get_guild_season_start_date(interaction.guild_id)
            if interaction.guild_id
            else None
        )
        # 統計計算はPandas使用のため to_thread のまま
        embed = await asyncio.to_thread(
            get_stats_summary,
            interaction.user.id,
            self.period,
            self.class_name,
            season_start_date,
        )
        await self.cog._send_result_embed_from_interaction(
            interaction, embed, force_public=True
        )
        await self.original_interaction.edit_original_response(
            content="結果を表示しました。", view=None
        )
        self.stop()


class DeleteHistoryView(ui.View):
    def __init__(
        self, author_id: int, records: list[dict], original_embed: discord.Embed
    ):
        super().__init__(timeout=180.0)
        self.author_id = author_id
        self.records = records
        self.original_embed = original_embed
        self.selected_match_time: str | None = None
        self._state = "selecting"
        self._update_components()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "この操作はコマンドを実行した本人しか行えません。", ephemeral=True
            )
            return False
        return True

    def _update_components(self):
        self.clear_items()
        if self._state == "selecting":
            options = []
            for record in self.records:
                short_time = record["match_time"][5:]
                my_archetype = record.get("my_archetype")
                my_archetype_text = f" [{my_archetype}]" if my_archetype else ""
                opponent_archetype = record.get("opponent_archetype")
                opponent_archetype_text = (
                    f" [{opponent_archetype}]" if opponent_archetype else ""
                )
                label = f"{short_time} {record['my_class']}{my_archetype_text} vs {record['opponent_class']}{opponent_archetype_text} ({record['result']})"
                options.append(SelectOption(label=label, value=record["match_time"]))

            select_menu = ui.Select(
                placeholder="削除したい対戦を選択...",
                options=options,
                custom_id="delete_history_select",
            )
            select_menu.callback = self.on_select
            self.add_item(select_menu)

            delete_button = ui.Button(
                label="選択した対戦を削除",
                style=discord.ButtonStyle.danger,
                custom_id="delete_history_initiate",
                disabled=False,  # 常に有効化（選択チェックはボタン内で実施）
            )
            delete_button.callback = self.on_initiate_delete
            self.add_item(delete_button)

        elif self._state == "confirming":
            confirm_button = ui.Button(
                label="はい、削除します",
                style=discord.ButtonStyle.danger,
                custom_id="delete_history_confirm",
            )
            confirm_button.callback = self.on_confirm_delete
            self.add_item(confirm_button)

            cancel_button = ui.Button(
                label="いいえ、キャンセル",
                style=discord.ButtonStyle.secondary,
                custom_id="delete_history_cancel",
            )
            cancel_button.callback = self.on_cancel_delete
            self.add_item(cancel_button)

    async def on_select(self, interaction: discord.Interaction):
        # 値を保存するだけで、UIは更新しない（選択状態を保持）
        self.selected_match_time = interaction.data["values"][0]
        await interaction.response.defer()

    async def on_initiate_delete(self, interaction: discord.Interaction):
        # 選択チェック
        if self.selected_match_time is None:
            await interaction.response.send_message(
                "❌ 削除したい対戦をプルダウンから選択してください。", ephemeral=True
            )
            return

        self._state = "confirming"
        self._update_components()
        confirm_embed = self.original_embed.copy()
        confirm_embed.color = discord.Color.red()
        selected_record_info = next(
            (r for r in self.records if r["match_time"] == self.selected_match_time),
            None,
        )
        info_text = f"`{self.selected_match_time}`"
        if selected_record_info:
            my_archetype = selected_record_info.get("my_archetype")
            my_archetype_text = f" [{my_archetype}]" if my_archetype else ""
            opponent_archetype = selected_record_info.get("opponent_archetype")
            opponent_archetype_text = (
                f" [{opponent_archetype}]" if opponent_archetype else ""
            )
            info_text = f"`{selected_record_info['match_time']}`\n{selected_record_info['my_class']}{my_archetype_text} vs {selected_record_info['opponent_class']}{opponent_archetype_text} ({selected_record_info['result']})"
        confirm_embed.description = (
            f"**以下の対戦記録を本当に削除しますか？**\n\n{info_text}"
        )
        await interaction.response.edit_message(embed=confirm_embed, view=self)

    async def on_confirm_delete(self, interaction: discord.Interaction):
        if self.selected_match_time is None:
            await interaction.response.edit_message(
                content="エラー: 削除対象が選択されていません。", embed=None, view=None
            )
            self.stop()
            return

        # 修正: 非同期関数なので直接 await
        deleted_count = await delete_match_record(
            self.author_id, self.selected_match_time
        )
        content = (
            f"✅ 対戦記録 (`{self.selected_match_time}`) を削除しました。"
            if deleted_count > 0
            else "❌ 削除に失敗したか、既にデータが存在しませんでした。"
        )
        await interaction.response.edit_message(content=content, embed=None, view=None)
        self.stop()

    async def on_cancel_delete(self, interaction: discord.Interaction):
        self.selected_match_time = None
        self._state = "selecting"
        self._update_components()
        await interaction.response.edit_message(embed=self.original_embed, view=self)


class ControlPanelView(ui.View):
    def __init__(self, bot: "ShadowverseCog.bot"):
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(
        label="手動登録",
        style=discord.ButtonStyle.success,
        custom_id="sv_panel:record",
        row=0,
    )
    async def record(self, interaction: discord.Interaction, button: ui.Button):
        view = ManualRecordView(author_id=interaction.user.id)
        await interaction.response.send_message(
            embed=view.create_embed(), view=view, ephemeral=True
        )

    @ui.button(
        label="戦績表示",
        style=discord.ButtonStyle.primary,
        custom_id="sv_panel:stats",
        row=0,
    )
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        cog = self.bot.get_cog("ShadowverseCog")
        if not cog:
            await interaction.response.send_message(
                "エラー: Cogが見つかりません。", ephemeral=True
            )
            return
        await interaction.response.send_message(
            "表示したい期間とクラスを選択してください。",
            view=StatsOptionsView(interaction, cog),
            ephemeral=True,
        )

    @ui.button(
        label="直近履歴",
        style=discord.ButtonStyle.primary,
        custom_id="sv_panel:history",
        row=0,
    )
    async def history(self, interaction: discord.Interaction, button: ui.Button):
        modal = ui.Modal(title="直近の履歴表示")
        count_input = ui.TextInput(
            label="表示する件数（1～25）", default="5", max_length=2
        )
        modal.add_item(count_input)

        async def modal_callback(modal_interaction: discord.Interaction):
            await modal_interaction.response.defer(thinking=True, ephemeral=True)
            try:
                count = int(str(count_input.value))
                if not 1 <= count <= 25:
                    raise ValueError
                # 履歴取得はPandas使用のため to_thread のまま
                embed, records = await asyncio.to_thread(
                    get_recent_matches, modal_interaction.user.id, count
                )
                if not records:
                    await modal_interaction.followup.send(embed=embed, ephemeral=True)
                    return
                view = DeleteHistoryView(
                    author_id=modal_interaction.user.id,
                    records=records,
                    original_embed=embed,
                )
                await modal_interaction.followup.send(
                    embed=embed, view=view, ephemeral=True
                )
            except (ValueError, TypeError):
                await modal_interaction.followup.send(
                    "❌ 1から25の有効な数値を入力してください。", ephemeral=True
                )

        modal.on_submit = modal_callback
        await interaction.response.send_modal(modal)

    @ui.button(
        label="通知チャンネル設定",
        style=discord.ButtonStyle.secondary,
        custom_id="sv_panel:set_channel",
        row=1,
    )
    async def set_channel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message(
                "この機能はサーバー内でのみ利用可能です。", ephemeral=True
            )
            return
        category = self.bot.get_channel(TARGET_CATEGORY_ID)
        if not isinstance(category, CategoryChannel):
            return await interaction.response.send_message(
                f"❌ 対象カテゴリ(ID: {TARGET_CATEGORY_ID})が見つかりません。",
                ephemeral=True,
            )
        text_channels = category.text_channels
        if not text_channels:
            return await interaction.response.send_message(
                f"❌ カテゴリ内に選択可能なチャンネルがありません。", ephemeral=True
            )
        await interaction.response.send_message(
            "通知先に設定したいチャンネルを以下から選択してください:",
            view=ChannelSelectView(
                channels=text_channels, author_id=interaction.user.id
            ),
            ephemeral=True,
        )

    @ui.button(
        label="全データ削除",
        style=discord.ButtonStyle.danger,
        custom_id="sv_panel:delete",
        row=1,
    )
    async def delete(self, interaction: discord.Interaction, button: ui.Button):
        confirm_view = ui.View(timeout=30.0)

        yes_button = ui.Button(
            label="はい、全て削除します", style=discord.ButtonStyle.danger
        )
        no_button = ui.Button(
            label="いいえ、キャンセル", style=discord.ButtonStyle.secondary
        )

        async def yes_callback(inner_interaction: discord.Interaction):
            if interaction.user.id != inner_interaction.user.id:
                await inner_interaction.response.send_message(
                    "この操作は本人しか行えません。", ephemeral=True
                )
                return

            await inner_interaction.response.edit_message(
                content="全データを削除中です...", view=None
            )
            try:
                # 修正: 非同期関数なので直接 await
                deleted_rows = await delete_all_user_records(inner_interaction.user.id)
                final_message = (
                    f"✅ あなたの戦績データ **{deleted_rows}件** をすべて削除しました。"
                    if deleted_rows > 0
                    else "ℹ️ あなたの戦績データは見つかりませんでした。"
                )
                await inner_interaction.edit_original_response(
                    content=final_message, view=None
                )
            except Exception as e:
                print(f"[/deletestats] 予期せぬエラー: {e}")
                await inner_interaction.edit_original_response(
                    content="❌ 削除処理中にエラーが発生しました。", view=None
                )

        async def no_callback(inner_interaction: discord.Interaction):
            if interaction.user.id != inner_interaction.user.id:
                await inner_interaction.response.send_message(
                    "この操作は本人しか行えません。", ephemeral=True
                )
                return
            await inner_interaction.response.edit_message(
                content="削除をキャンセルしました。", view=None
            )

        yes_button.callback = yes_callback
        no_button.callback = no_callback
        confirm_view.add_item(yes_button)
        confirm_view.add_item(no_button)

        await interaction.response.send_message(
            "本当にあなたの全ての戦績データを削除しますか？この操作は取り消せません。",
            view=confirm_view,
            ephemeral=True,
        )
