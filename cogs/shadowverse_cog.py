import discord
from discord.ext import commands
from discord import app_commands, ui, PartialEmoji, SelectOption, CategoryChannel
import asyncio
import shutil
import datetime
import traceback

import os
import re
import pandas as pd
import cv2
import sqlite3

# yomitokuの正しいクラスをインポート
from yomitoku import DocumentAnalyzer

# --- グローバル変数・定数 ---

DB_FILE = "shadowverse_data.db"
CLASS_NAMES = ["エルフ", "ロイヤル", "ウィッチ", "ドラゴン", "ナイトメア", "ビショップ", "ネメシス"]
TURN_ORDERS = ["先攻", "後攻", "不明"]
RESULTS = ["WIN", "LOSE"]
TARGET_CATEGORY_ID = 1003574017900417094 # 通知先として選択できるチャンネルが含まれるカテゴリID

CLASS_EMOJI_MAP = {
    "エルフ": (922142168473301082, "Class_Forestcraft"),
    "ロイヤル": (922142203323744296, "Class_Swordcraft"),
    "ウィッチ": (922142232595791953, "Class_Runecraft"),
    "ドラゴン": (922142264942284830, "Class_Dragoncraft"),
    "ナイトメア": (1410146572930519040, "Class_Abysscraft"),
    "ビショップ": (922142398073700433, "Class_Havencraft"),
    "ネメシス": (922142424380346399, "Class_Portalcraft")
}

# --- データ処理関数 ---

def extract_text_from_image(ocr_instance: DocumentAnalyzer, image_path: str) -> str | None:
    try:
        img = cv2.imread(image_path)
        if img is None: return None
        results, _, _ = ocr_instance(img)
        if not results: return None
        paragraphs_with_coords = [( (p.box[1] + p.box[3]) / 2, p.contents) for f in results.figures for p in f.paragraphs]
        paragraphs_with_coords.sort(key=lambda item: item[0])
        return '\n'.join([text for _, text in paragraphs_with_coords])
    except Exception as e:
        print(f"Yomitoku処理中にエラーが発生しました: {e}")
        return None

def parse_replay_text(text: str) -> list[dict]:
    matches = list(re.finditer(r"\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}", text))
    if not matches: return []
    results = []
    for i, current_match in enumerate(matches):
        start_pos = current_match.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        match_segment = text[start_pos:end_pos]
        all_found_classes = [cn for cn in CLASS_NAMES if cn in match_segment]
        unique_classes_in_order = sorted(list(set(all_found_classes)), key=lambda x: match_segment.find(x))
        if not unique_classes_in_order: continue
        my_class = "不明"; opponent_class = "不明"
        class_pattern_with_de = f"({'|'.join(CLASS_NAMES)})で"; anchor_match = re.search(class_pattern_with_de, match_segment)
        if anchor_match:
            my_class = anchor_match.group(1)
            for cls in unique_classes_in_order:
                if cls != my_class: opponent_class = cls; break
            if opponent_class == "不明": opponent_class = my_class
        else:
            if len(unique_classes_in_order) >= 2: my_class = unique_classes_in_order[0]; opponent_class = unique_classes_in_order[1]
            elif len(unique_classes_in_order) == 1: my_class = unique_classes_in_order[0]; opponent_class = unique_classes_in_order[0]
        if my_class == "不明": continue
        results.append({"match_time": current_match.group(0), "my_class": my_class, "opponent_class": opponent_class, "result": "WIN" if "WIN" in match_segment else "LOSE", "turn_order": "不明"})
    return results

def save_stats_to_db(user_id: int, records: list[dict]) -> tuple[list[dict], int]:
    if not records: return [], 0
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    new_records_saved = []
    total_attempted = len(records)
    for record in records:
        try:
            cursor.execute("""
                INSERT OR IGNORE INTO matches (user_id, match_time, my_class, opponent_class, result, turn_order)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (user_id, record['match_time'], record['my_class'], record['opponent_class'], record['result'], record.get('turn_order', '不明')))
            if cursor.rowcount > 0: new_records_saved.append(record)
        except sqlite3.Error as e: print(f"データベース挿入エラー: {e}")
    conn.commit()
    conn.close()
    return new_records_saved, total_attempted - len(new_records_saved)

def get_stats_summary(user_id: int, period: str = "all", class_name: str | None = None) -> discord.Embed:
    conn = sqlite3.connect(DB_FILE)
    try: user_df = pd.read_sql_query("SELECT * FROM matches WHERE user_id = ?", conn, params=(user_id,))
    finally: conn.close()
    if user_df.empty: return discord.Embed(description="あなたの戦績データはまだありません。", color=discord.Color.orange())
    
    user_df['match_time'] = pd.to_datetime(user_df['match_time'], format='mixed')
    now = datetime.datetime.now(); today_business_date = (now - datetime.timedelta(hours=5)).date()
    
    period_text_map = {
        "today": "今日の",
        "yesterday": "昨日の",
        "week": "過去7日間の",
        "season": "今期の"
    }
    period_text = period_text_map.get(period, "あなたの")

    if period != "all":
        if period == "today":
            start_time = datetime.datetime.combine(today_business_date, datetime.time(5, 0))
            end_time = start_time + datetime.timedelta(days=1)
            user_df = user_df[(user_df['match_time'] >= start_time) & (user_df['match_time'] < end_time)]
        elif period == "yesterday":
            yesterday_business_date = today_business_date - datetime.timedelta(days=1)
            start_time = datetime.datetime.combine(yesterday_business_date, datetime.time(5, 0))
            end_time = start_time + datetime.timedelta(days=1)
            user_df = user_df[(user_df['match_time'] >= start_time) & (user_df['match_time'] < end_time)]
        elif period == "week":
            start_time = datetime.datetime.combine(today_business_date - datetime.timedelta(days=6), datetime.time(5, 0))
            end_time = datetime.datetime.combine(today_business_date, datetime.time(5, 0)) + datetime.timedelta(days=1)
            user_df = user_df[(user_df['match_time'] >= start_time) & (user_df['match_time'] < end_time)]
        elif period == "season":
            current_date = now.date()
            season_start_year = current_date.year
            # If today's date is before Aug 28, the season started last year.
            if current_date.month < 8 or (current_date.month == 8 and current_date.day < 28):
                season_start_year -= 1
            # The start time is 5 AM on Aug 28.
            start_time = datetime.datetime(season_start_year, 8, 28, 5, 0)
            user_df = user_df[user_df['match_time'] >= start_time]
    
    if user_df.empty: return discord.Embed(description=f"{period_text}戦績データはありません。", color=discord.Color.orange())
    
    if class_name:
        user_df = user_df[user_df['my_class'] == class_name]
        if user_df.empty:
            return discord.Embed(description=f"あなたが{class_name}を使用した{period_text}戦績データはありません。", color=discord.Color.orange())

    class_text = f"{class_name}の" if class_name else ""
    summary_text = "戦績" if period != "all" else "戦績サマリー"
    embed = discord.Embed(title=f"⚔️ {period_text}{class_text}{summary_text} ⚔️", color=discord.Color.gold())

    total_matches = len(user_df); win_count = len(user_df[user_df['result'] == 'WIN']); win_rate = (win_count / total_matches) * 100 if total_matches > 0 else 0
    summary_lines = [f"総合: {total_matches}戦 {win_count}勝 {total_matches - win_count}敗 (勝率: {win_rate:.1f}%)"]
    if 'turn_order' in user_df.columns:
        for order in ["先攻", "後攻"]:
            order_df = user_df[user_df['turn_order'] == order]
            if not order_df.empty:
                order_wins = (order_df['result'] == 'WIN').sum(); order_total = len(order_df); order_rate = (order_wins / order_total) * 100
                summary_lines.append(f"{order}: {order_wins}勝 {order_total - order_wins}敗 (勝率: {order_rate:.1f}%)")
    embed.add_field(name="📊 総合戦績", value=f"```\n" + "\n".join(summary_lines) + "\n```", inline=False)
    if not user_df.empty:
        if class_name:
            matchup_summary = user_df.groupby('opponent_class')['result'].apply(lambda x: f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)").to_string()
            my_class_info = CLASS_EMOJI_MAP.get(class_name)
            my_class_emoji = f"{discord.PartialEmoji(name=my_class_info[1], id=my_class_info[0])} " if my_class_info else ""
            embed.add_field(name=f"{my_class_emoji}対相手クラス成績 ({class_name})", value=f"```{matchup_summary}```", inline=False)
        else:
            class_summary = user_df.groupby('my_class')['result'].apply(lambda x: f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({ (x == 'WIN').sum() }勝 / {len(x)}戦)").to_string()
            embed.add_field(name="自分のクラス別勝率", value=f"```{class_summary}```", inline=False)
            played_classes = sorted(user_df['my_class'].unique())
            for my_class in played_classes:
                class_df = user_df[user_df['my_class'] == my_class];
                if class_df.empty: continue
                matchup_summary = class_df.groupby('opponent_class')['result'].apply(lambda x: f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)").to_string()
                my_class_info = CLASS_EMOJI_MAP.get(my_class); my_class_emoji = f"{discord.PartialEmoji(name=my_class_info[1], id=my_class_info[0])} " if my_class_info else ""
                embed.add_field(name=f"{my_class_emoji}対相手クラス成績 ({my_class})", value=f"```{matchup_summary}```", inline=False)
    return embed

def get_recent_matches(user_id: int, count: int) -> discord.Embed:
    conn = sqlite3.connect(DB_FILE)
    try: recent_df = pd.read_sql_query("SELECT * FROM matches WHERE user_id = ? ORDER BY match_time DESC LIMIT ?", conn, params=(user_id, count))
    finally: conn.close()
    if recent_df.empty: return discord.Embed(description="あなたの戦績データはまだありません。", color=discord.Color.orange())
    recent_df['match_time'] = pd.to_datetime(recent_df['match_time'], format='mixed')
    descriptions = []
    for row in recent_df.itertuples():
        result_emoji = "✅" if row.result == "WIN" else "❌"
        my_class_info = CLASS_EMOJI_MAP.get(row.my_class); my_class_emoji = f"{discord.PartialEmoji(name=my_class_info[1], id=my_class_info[0])}" if my_class_info else ""
        opp_class_info = CLASS_EMOJI_MAP.get(row.opponent_class); opp_class_emoji = f"{discord.PartialEmoji(name=opp_class_info[1], id=opp_class_info[0])}" if opp_class_info else ""
        turn_order_text = f"({row.turn_order})" if hasattr(row, 'turn_order') and row.turn_order != "不明" else ""
        match_time_str = row.match_time.strftime('%m/%d %H:%M')
        descriptions.append(f"{result_emoji} `{match_time_str}` {my_class_emoji} vs {opp_class_emoji} **{row.opponent_class}** {turn_order_text}")
    return discord.Embed(title=f"直近の戦績 ({len(recent_df)}件)", description="\n".join(descriptions), color=discord.Color.blue())

def get_help_embed():
    embed = discord.Embed(title="⚔️ シャドウバース戦績管理ヘルプ", description="戦績管理機能で利用できるコマンドやボタン操作の一覧です。", color=discord.Color.purple())
    embed.add_field(name="【推奨】パネルからの操作", value=("このパネルのボタンから、直感的にほとんどの機能を利用できます。\n・**手動登録**: ボタン操作で1戦ずつ戦績を記録します。\n・**戦績表示**: 期間とクラスを指定して、詳細な戦績サマリーを表示します。\n・**直近履歴**: 記録した最新の対戦履歴を表示します。\n・**通知チャンネル設定**: 戦績の表示先チャンネルを設定・変更します。\n・**全データ削除**: あなたの全データを削除します（要確認）。"), inline=False)
    embed.add_field(name="コマンドでの操作", value=("パネル操作に加えて、以下のコマンドも利用可能です。\n・**/replay [image]**: リプレイのスクリーンショットから戦績を一括登録します。\n・**/record**: ボタン操作と同じ手動登録を開始します。\n・**/stats [period] [class_name]**: 期間とクラスを指定して戦績サマリーを表示します。\n・**/history [count]**: 直近の対戦履歴を指定した件数表示します。\n・**!panel**: このパネルを再設置する際に使用します。"), inline=False)
    return embed

# --- UIコンポーネント ---

class ConfirmDeleteView(ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=30.0)
        self.value = None; self.author_id = author_id
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("この操作はコマンドを実行した本人しか行えません。", ephemeral=True)
            return False
        return True
    @ui.button(label="はい、削除します", style=discord.ButtonStyle.danger, custom_id="confirm_delete_yes")
    async def confirm(self, interaction: discord.Interaction, button: ui.Button):
        self.value = True; self.stop();
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="戦績を削除中です...", view=self)
    @ui.button(label="いいえ、キャンセル", style=discord.ButtonStyle.secondary, custom_id="confirm_delete_no")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        self.value = False; self.stop()
        for item in self.children: item.disabled = True
        await interaction.response.edit_message(content="削除をキャンセルしました。", view=self)

class ManualRecordView(ui.View):
    def __init__(self, author_id: int):
        super().__init__(timeout=180.0)
        self.author_id = author_id; self.my_class = None; self.opponent_class = None; self.result = None
        self.turn_order = "不明"; self.current_selection = "my_class"; self.update_view()
    def update_view(self):
        self.clear_items()
        if self.current_selection == "my_class": self.add_class_buttons("my_class")
        elif self.current_selection == "opponent_class": self.add_class_buttons("opponent_class")
        elif self.current_selection == "result": self.add_choice_buttons("result", RESULTS, [discord.ButtonStyle.success, discord.ButtonStyle.danger])
        elif self.current_selection == "turn_order": self.add_choice_buttons("turn_order", TURN_ORDERS, [discord.ButtonStyle.primary, discord.ButtonStyle.primary, discord.ButtonStyle.secondary])
        elif self.current_selection == "confirm": self.add_confirm_buttons()
    def add_class_buttons(self, selection_type: str):
        for class_name in CLASS_NAMES:
            emoji_info = CLASS_EMOJI_MAP.get(class_name); emoji = PartialEmoji(name=emoji_info[1], id=emoji_info[0]) if emoji_info else None
            button = ui.Button(label=class_name, emoji=emoji, custom_id=f"manual_record_class:{selection_type}:{class_name}", style=discord.ButtonStyle.secondary)
            button.callback = self.on_button_click; self.add_item(button)
    def add_choice_buttons(self, selection_type: str, choices: list, styles: list):
        for i, choice in enumerate(choices):
            button = ui.Button(label=choice, custom_id=f"manual_record_choice:{selection_type}:{choice}", style=styles[i])
            button.callback = self.on_button_click; self.add_item(button)
    def add_confirm_buttons(self):
        continue_button = ui.Button(label="登録して続ける", style=discord.ButtonStyle.primary, custom_id="manual_record_confirm:continue"); continue_button.callback = self.on_register; self.add_item(continue_button)
        register_button = ui.Button(label="登録して終了", style=discord.ButtonStyle.success, custom_id="manual_record_confirm:final"); register_button.callback = self.on_register; self.add_item(register_button)
        cancel_button = ui.Button(label="キャンセル", style=discord.ButtonStyle.danger, custom_id="manual_record_confirm:cancel"); cancel_button.callback = self.on_register; self.add_item(cancel_button)
    async def on_button_click(self, interaction: discord.Interaction):
        custom_id_parts = interaction.data["custom_id"].split(":")
        selection_type, value = custom_id_parts[1], custom_id_parts[2]
        setattr(self, selection_type, value)
        if self.current_selection == "my_class": self.current_selection = "opponent_class"
        elif self.current_selection == "opponent_class": self.current_selection = "result"
        elif self.current_selection == "result": self.current_selection = "turn_order"
        elif self.current_selection == "turn_order": self.current_selection = "confirm"
        self.update_view()
        await interaction.response.edit_message(embed=self.create_embed(), view=self)
    async def on_register(self, interaction: discord.Interaction):
        action = interaction.data["custom_id"].split(":")[1]
        if action == "cancel": await interaction.response.edit_message(content="登録をキャンセルしました。", embed=None, view=None); return
        record = {"match_time": datetime.datetime.now().strftime("%Y/%m/%d %H:%M"), "my_class": self.my_class, "opponent_class": self.opponent_class, "result": self.result, "turn_order": self.turn_order}
        try:
            await asyncio.to_thread(save_stats_to_db, self.author_id, [record])
            if action == "continue":
                self.my_class = None; self.opponent_class = None; self.result = None; self.turn_order = "不明"; self.current_selection = "my_class"
                self.update_view()
                await interaction.response.edit_message(content="✅ 1件登録しました。続けて次の対戦を入力してください。", embed=self.create_embed(), view=self)
            elif action == "final":
                final_embed = self.create_embed(); final_embed.title = "✅ 戦績を登録しました"; final_embed.description = "新しい戦績が記録されました。"
                await interaction.response.edit_message(content=None, embed=final_embed, view=None)
        except Exception as e: print(f"[/record] 手動登録の保存中にエラー: {e}"); await interaction.response.edit_message(content="❌ 登録に失敗しました。", embed=None, view=None)
    def get_class_display(self, class_name: str | None) -> str:
        if not class_name: return "未選択"
        emoji_info = CLASS_EMOJI_MAP.get(class_name)
        return f"{PartialEmoji(name=emoji_info[1], id=emoji_info[0])} **{class_name}**" if emoji_info else f"**{class_name}**"
    def create_embed(self):
        prompts = {"my_class": "自分のクラスを選択してください", "opponent_class": "相手のクラスを選択してください", "result": "勝敗を選択してください", "turn_order": "先攻/後攻を選択してください", "confirm": "内容を確認して登録してください"}
        embed = discord.Embed(title="戦績手動登録", description=f"**➡️ {prompts[self.current_selection]}**")
        embed.add_field(name="自分のクラス", value=self.get_class_display(self.my_class), inline=True); embed.add_field(name="相手のクラス", value=self.get_class_display(self.opponent_class), inline=True)
        embed.add_field(name="結果", value=f"**{self.result}**" if self.result else "未選択", inline=True); embed.add_field(name="先攻/後攻", value=f"**{self.turn_order}**" if self.turn_order != "不明" else "未選択", inline=True)
        return embed
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("この操作はコマンドを実行した本人しか行えません。", ephemeral=True); return False
        return True

class ChannelSelectView(ui.View):
    def __init__(self, channels: list[discord.TextChannel], author_id: int):
        super().__init__(timeout=120.0)
        self.author_id = author_id

        # チャンネルリストを25個ずつのチャンクに分割
        channel_chunks = [channels[i:i + 25] for i in range(0, len(channels), 25)]

        for i, chunk in enumerate(channel_chunks):
            if not chunk: continue
            
            options = [SelectOption(label=ch.name, value=str(ch.id)) for ch in chunk]
            
            # プレースホルダーのテキストを決定
            if len(channel_chunks) > 1:
                placeholder = f"通知先チャンネルを選択 ({chunk[0].name} ～ {chunk[-1].name})"
            else:
                placeholder = "通知先にしたいチャンネルを選択してください..."
            
            select_menu = ui.Select(
                placeholder=placeholder,
                options=options,
                custom_id=f"channel_select_menu_{i}" # 各メニューにユニークなIDを付与
            )
            select_menu.callback = self.on_select_submit
            self.add_item(select_menu)

    async def on_select_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_channel_id = int(interaction.data["values"][0])
        set_user_channel_setting(interaction.user.id, selected_channel_id)
        selected_channel = interaction.guild.get_channel(selected_channel_id)
        
        # View内のすべてのコンポーネントを無効化
        for item in self.children:
            item.disabled = True
            
        await interaction.edit_original_response(content=f"✅ 通知チャンネルを {selected_channel.mention} に設定しました。", view=self)
        self.stop()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("この操作はコマンドを実行した本人しか行えません。", ephemeral=True)
            return False
        return True

class StatsOptionsView(ui.View):
    def __init__(self, original_interaction: discord.Interaction, cog: "ShadowverseCog"):
        super().__init__(timeout=120.0)
        self.original_interaction = original_interaction
        self.cog = cog
        self.period = "today"
        self.class_name = None
        self.period_select = ui.Select(
            placeholder="集計期間を選択...",
            options=[
                SelectOption(label="本日", value="today", default=True),
                SelectOption(label="昨日", value="yesterday"),
                SelectOption(label="一週間", value="week"),
                SelectOption(label="今期", value="season"),
                SelectOption(label="全期間", value="all")
            ],
            custom_id="stats_opt_period"
        )
        self.period_select.callback = self.on_period_select
        self.add_item(self.period_select)
        class_options = [SelectOption(label="全てのクラス", value="all_classes", default=True)] + \
                        [SelectOption(label=name, value=name) for name in CLASS_NAMES]
        self.class_select = ui.Select(
            placeholder="クラスを選択...",
            options=class_options,
            custom_id="stats_opt_class"
        )
        self.class_select.callback = self.on_class_select
        self.add_item(self.class_select)

    async def on_period_select(self, interaction: discord.Interaction):
        self.period = interaction.data['values'][0]
        for option in self.period_select.options:
            option.default = option.value == self.period
        await interaction.response.edit_message(view=self)


    async def on_class_select(self, interaction: discord.Interaction):
        value = interaction.data['values'][0]
        self.class_name = value if value != "all_classes" else None
        for option in self.class_select.options:
            option.default = option.value == value
        await interaction.response.edit_message(view=self)
    
    @ui.button(label="結果を表示", style=discord.ButtonStyle.success, custom_id="stats_opt_submit")
    async def submit(self, interaction: discord.Interaction, button: ui.Button):
        # ボタンが押されたインタラクションへの応答をephemeralにする
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        embed = await asyncio.to_thread(get_stats_summary, interaction.user.id, self.period, self.class_name)
        await self.cog._send_result_embed_from_interaction(interaction, embed)

        # 元のインタラクション（選択肢）のメッセージを編集して終了を通知
        await self.original_interaction.edit_original_response(content="結果を表示しました。", view=None)

        self.stop()

class ControlPanelView(ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=None)
        self.bot = bot

    @ui.button(label="手動登録", style=discord.ButtonStyle.success, custom_id="sv_panel:record", row=0)
    async def record(self, interaction: discord.Interaction, button: ui.Button):
        view = ManualRecordView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

    # ### 変更点: ephemeralを強制的にTrueに設定 ###
    @ui.button(label="戦績表示", style=discord.ButtonStyle.primary, custom_id="sv_panel:stats", row=0)
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        # パネル上のボタンはサーバー内での使用を想定
        if interaction.guild is None:
            await interaction.response.send_message("このボタンはサーバー内でのみ利用可能です。DMでは `/stats` コマンドをご利用ください。", ephemeral=True)
            return

        cog = self.bot.get_cog("ShadowverseCog")
        if not cog:
            await interaction.response.send_message("エラー: Cogが見つかりません。", ephemeral=True)
            return
        
        # 選択肢のメッセージをephemeralで送信
        await interaction.response.send_message("表示したい期間とクラスを選択してください。", view=StatsOptionsView(interaction, cog), ephemeral=True)

    @ui.button(label="直近履歴", style=discord.ButtonStyle.primary, custom_id="sv_panel:history", row=0)
    async def history(self, interaction: discord.Interaction, button: ui.Button):
        modal = ui.Modal(title="直近の履歴表示")
        count_input = ui.TextInput(label="表示する件数（1～25）", default="5", max_length=2)
        modal.add_item(count_input)
        async def modal_callback(modal_interaction: discord.Interaction):
            await modal_interaction.response.defer(thinking=True, ephemeral=True)
            try:
                count = int(str(count_input.value))
                if not 1 <= count <= 25: raise ValueError
                embed = await asyncio.to_thread(get_recent_matches, modal_interaction.user.id, count)
                cog = self.bot.get_cog("ShadowverseCog")
                await cog._send_result_embed_from_interaction(modal_interaction, embed)
            except (ValueError, TypeError):
                await modal_interaction.followup.send("❌ 1から25の有効な数値を入力してください。", ephemeral=True)
        modal.on_submit = modal_callback
        await interaction.response.send_modal(modal)
    
    @ui.button(label="通知チャンネル設定", style=discord.ButtonStyle.secondary, custom_id="sv_panel:set_channel", row=1)
    async def set_channel(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.guild is None:
            await interaction.response.send_message("この機能はサーバー内でのみ利用可能です。", ephemeral=True)
            return
        category = self.bot.get_channel(TARGET_CATEGORY_ID)
        if not isinstance(category, CategoryChannel): return await interaction.response.send_message(f"❌ 対象カテゴリ(ID: {TARGET_CATEGORY_ID})が見つかりません。", ephemeral=True)
        text_channels = category.text_channels
        if not text_channels: return await interaction.response.send_message(f"❌ カテゴリ内に選択可能なチャンネルがありません。", ephemeral=True)
        await interaction.response.send_message("通知先に設定したいチャンネルを以下から選択してください:", view=ChannelSelectView(channels=text_channels, author_id=interaction.user.id), ephemeral=True)

    @ui.button(label="全データ削除", style=discord.ButtonStyle.danger, custom_id="sv_panel:delete", row=1)
    async def delete(self, interaction: discord.Interaction, button: ui.Button):
        view = ConfirmDeleteView(author_id=interaction.user.id)
        await interaction.response.send_message("本当にあなたの全ての戦績データを削除しますか？", view=view, ephemeral=True)
        await view.wait()
        if view.value is True:
            try:
                conn = sqlite3.connect(DB_FILE)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM matches WHERE user_id = ?", (interaction.user.id,))
                deleted_rows = cursor.rowcount
                conn.commit()
                conn.close()
                final_message = f"✅ あなたの戦績データ **{deleted_rows}件** をすべて削除しました。" if deleted_rows > 0 else "ℹ️ あなたの戦績データは見つかりませんでした。"
                await interaction.edit_original_response(content=final_message, view=None)
            except Exception as e:
                print(f"[/deletestats] 予期せぬエラー: {e}")
                await interaction.edit_original_response(content="❌ 削除処理中にエラーが発生しました。", view=None)

def _init_database():
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                user_id INTEGER NOT NULL, match_time TEXT NOT NULL, my_class TEXT,
                opponent_class TEXT, result TEXT, turn_order TEXT,
                PRIMARY KEY (user_id, match_time))""")
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id INTEGER PRIMARY KEY, channel_id INTEGER NOT NULL)""")
        conn.commit()

def set_user_channel_setting(user_id: int, channel_id: int):
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute("INSERT OR REPLACE INTO user_settings (user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))

def get_user_channel_setting(user_id: int) -> int | None:
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute("SELECT channel_id FROM user_settings WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        return result[0] if result else None

# --- Discord Cogクラス ---
class ShadowverseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ocr: DocumentAnalyzer | None = None
        self.model_load_task = asyncio.create_task(self._initialize_model())
        _init_database()
        self.bot.add_view(ControlPanelView(bot))

    async def _initialize_model(self):
        loop = asyncio.get_running_loop()
        try: self.ocr = await loop.run_in_executor(None, lambda: DocumentAnalyzer(configs={"lite": True}, device='cpu'))
        except Exception as e: print(f"モデルの読み込み中にエラー: {e}")

    async def _send_result_embed_from_interaction(self, interaction: discord.Interaction, embed: discord.Embed):
        if interaction.guild is None:
            await interaction.followup.send(embed=embed)
            return

        target_channel_id = get_user_channel_setting(interaction.user.id)
        target_channel = self.bot.get_channel(target_channel_id) if target_channel_id else None

        if target_channel:
            try:
                await target_channel.send(embed=embed)
                await interaction.followup.send(f"✅ 結果を {target_channel.mention} に送信しました。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。代わりにここに表示します。", ephemeral=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed, ephemeral=True)


    @commands.command(name="panel")
    async def deploy_panel(self, ctx: commands.Context):
        embed = discord.Embed(title="⚔️ シャドウバース 戦績管理パネル ⚔️", description="下のボタンから各機能をご利用ください。\n\n**⚠️ まずはじめに、`⚙️ 通知チャンネル設定` ボタンから結果を投稿する個人チャンネルを設定してください。**", color=discord.Color.purple())
        await ctx.send(embed=embed, view=ControlPanelView(self.bot))
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

    @deploy_panel.error
    async def deploy_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        print(f"!panelコマンドでエラー: {error}")
        await ctx.send(f"パネル設置中に予期せぬエラーが発生しました: {error}")

    @app_commands.command(name="record", description="ボタン操作で戦績を手動で登録します。")
    async def manual_record(self, interaction: discord.Interaction):
        view = ManualRecordView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

    @app_commands.command(name="replay", description="Shadowverseのリプレイ画像から戦績を記録します。")
    async def replay_record(self, interaction: discord.Interaction, image: discord.Attachment):
        if self.ocr is None: return await interaction.response.send_message("OCRモデル準備中です。しばらくお待ちください。", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        if not image.content_type or not image.content_type.startswith('image/'):
            return await interaction.followup.send("画像ファイルを添付してください。", ephemeral=True)
        temp_image_path = f"temp_{interaction.id}.png"; await image.save(temp_image_path)
        try:
            def processing_task():
                text_data = extract_text_from_image(self.ocr, temp_image_path)
                if not text_data: return "❌ 画像からテキストを読み取れませんでした。", 0
                all_records = parse_replay_text(text_data)
                if not all_records: return f"❌ 画像から戦績データを解析できませんでした。\n\n**【デバッグ用】**\n```{text_data[:1500]}```", 0
                saved_records, duplicate_count = save_stats_to_db(interaction.user.id, all_records)
                parts = []
                if saved_records: parts.append(f"✅ **{len(saved_records)}件**の新しい戦績を記録しました！"); parts.extend([f"・`{r['match_time']}` **`{r['my_class']}`** vs `{r['opponent_class']}` - **{r['result']}**" for r in saved_records])
                if duplicate_count > 0: parts.append(f"ℹ️ 日時が重複する **{duplicate_count}件**の戦績はスキップされました。")
                return "\n".join(parts) if parts else "ℹ️ 新しく記録する戦績はありませんでした。", len(saved_records)
            message, saved_count = await asyncio.to_thread(processing_task)
            
            if interaction.guild is None:
                await interaction.followup.send(message)
                return

            target_channel_id = get_user_channel_setting(interaction.user.id)
            target_channel = self.bot.get_channel(target_channel_id) if target_channel_id else None

            if saved_count > 0 and target_channel:
                try:
                    await target_channel.send(f"{interaction.user.mention}さんがリプレイから戦績を登録しました:\n{message}")
                    await interaction.followup.send(f"✅ 結果を {target_channel.mention} に送信しました。")
                except discord.Forbidden:
                    await interaction.followup.send(f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。メッセージはここに表示します。\n\n{message}")
            else:
                 await interaction.followup.send(message)

        finally:
            if os.path.exists(temp_image_path): os.remove(temp_image_path)

    @app_commands.command(name="stats", description="自分の戦績サマリーを表示します。")
    @app_commands.describe(
        period="集計期間を選択します（デフォルトは本日）。",
        class_name="クラスを指定して、そのクラスの戦績のみ表示します。"
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="本日", value="today"),
            app_commands.Choice(name="昨日", value="yesterday"),
            app_commands.Choice(name="一週間", value="week"),
            app_commands.Choice(name="今期", value="season"),
            app_commands.Choice(name="全期間", value="all"),
        ],
        class_name=[app_commands.Choice(name=cn, value=cn) for cn in CLASS_NAMES]
    )
    async def show_stats(self, interaction: discord.Interaction, period: app_commands.Choice[str] = None, class_name: app_commands.Choice[str] = None):
        is_ephemeral = interaction.guild is not None
        await interaction.response.defer(thinking=True, ephemeral=is_ephemeral)
        selected_period = period.value if period else "today"
        selected_class = class_name.value if class_name else None
        embed = await asyncio.to_thread(get_stats_summary, interaction.user.id, selected_period, selected_class)
        await self._send_result_embed_from_interaction(interaction, embed)

    @app_commands.command(name="history", description="直近の戦績を指定した件数表示します。")
    @app_commands.describe(count="表示する件数を指定します（1～25件）。")
    async def show_history(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25] = 5):
        is_ephemeral = interaction.guild is not None
        await interaction.response.defer(thinking=True, ephemeral=is_ephemeral)
        embed = await asyncio.to_thread(get_recent_matches, interaction.user.id, count)
        await self._send_result_embed_from_interaction(interaction, embed)

async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowverseCog(bot))
