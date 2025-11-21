import discord
from discord.ext import commands
from discord import ui
import pandas as pd
import os
import asyncio
import sqlite3 # SQLiteを扱うためにインポート

# --- 定数 ---
DB_FILE = "user_data.db"  # データベースファイルの名前
TARGET_CATEGORY_ID = 1003574017900417094 # 通知先として選択できるチャンネルが含まれるカテゴリID

# --- データベース管理クラス ---
class DatabaseManager:
    """SQLiteデータベースを管理し、ユーザー設定を永続化するクラス"""
    def __init__(self, db_path=DB_FILE):
        self.db_path = db_path
        self._setup_database()

    def _get_connection(self) -> sqlite3.Connection:
        """DB接続を取得"""
        return sqlite3.connect(self.db_path)

    def _setup_database(self):
        """テーブルが存在しない場合に自動で作成する"""
        with self._get_connection() as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS sv_channel_settings (
                    user_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
            ''')
            conn.commit()

    # --- 同期メソッド (裏側で実行) ---
    def _set_channel_sync(self, user_id: int, channel_id: int):
        with self._get_connection() as conn:
            conn.execute("INSERT OR REPLACE INTO sv_channel_settings (user_id, channel_id) VALUES (?, ?)", (user_id, channel_id))

    def _get_channel_sync(self, user_id: int) -> int | None:
        with self._get_connection() as conn:
            cursor = conn.execute("SELECT channel_id FROM sv_channel_settings WHERE user_id = ?", (user_id,))
            result = cursor.fetchone()
            return result[0] if result else None

    def _clear_channel_sync(self, user_id: int):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM sv_channel_settings WHERE user_id = ?", (user_id,))

    # --- 非同期メソッド (Cogから呼び出す用) ---
    async def set_user_channel(self, user_id: int, channel_id: int):
        await asyncio.to_thread(self._set_channel_sync, user_id, channel_id)

    async def get_user_channel(self, user_id: int) -> int | None:
        return await asyncio.to_thread(self._get_channel_sync, user_id)

    async def clear_user_channel(self, user_id: int):
        await asyncio.to_thread(self._clear_channel_sync, user_id)


# --- UIコンポーネント (View, Modalなど) ---
# 元のCogからUIパーツをインポート
try:
    from .shadowverse_cog import (
        ManualRecordView, ConfirmDeleteView, get_stats_summary, get_recent_matches
    )
except ImportError:
    # 依存関係のエラーを防ぐためのフォールバック
    class ManualRecordView(ui.View): pass
    class ConfirmDeleteView(ui.View): pass
    def get_stats_summary(user_id, period): return discord.Embed(title="Error")
    def get_recent_matches(user_id, count): return discord.Embed(title="Error")


class ChannelSelectView(ui.View):
    def __init__(self, channels: list[discord.TextChannel], author_id: int, db_manager: DatabaseManager):
        super().__init__(timeout=120.0)
        self.author_id = author_id
        self.db_manager = db_manager
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in channels[:25]
        ]
        self.select_menu = ui.Select(placeholder="通知先にしたいチャンネルを選択してください...", options=options)
        self.select_menu.callback = self.on_select_submit
        self.add_item(self.select_menu)

    async def on_select_submit(self, interaction: discord.Interaction):
        await interaction.response.defer()
        selected_channel_id = int(interaction.data["values"][0])
        await self.db_manager.set_user_channel(interaction.user.id, selected_channel_id)
        selected_channel = interaction.guild.get_channel(selected_channel_id)
        for item in self.children: item.disabled = True
        await interaction.edit_original_response(
            content=f"✅ 通知チャンネルを **{selected_channel.mention}** に設定しました。", view=self
        )
        self.stop()
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("この操作はコマンドを実行した本人しか行えません。", ephemeral=True)
            return False
        return True

# (以降のView, ModalもDBマネージャーを使うように修正済み)
class StatsPeriodSelectView(ui.View):
    def __init__(self, author_id: int, bot_instance: commands.Bot, db_manager: DatabaseManager):
        super().__init__(timeout=60)
        self.author_id = author_id; self.bot = bot_instance; self.db_manager = db_manager
    @ui.select(placeholder="集計期間を選択してください...",options=[discord.SelectOption(label="本日", value="today"), discord.SelectOption(label="昨日", value="yesterday"), discord.SelectOption(label="一週間", value="week"), discord.SelectOption(label="全期間", value="all")])
    async def select_period(self, interaction: discord.Interaction, select: ui.Select):
        selected_period = select.values[0]
        await interaction.response.edit_message(content="統計を生成しています...", view=None)
        embed = await asyncio.to_thread(get_stats_summary, interaction.user.id, selected_period)
        target_channel_id = await self.db_manager.get_user_channel(interaction.user.id)
        target_channel = self.bot.get_channel(target_channel_id) if target_channel_id else None
        if target_channel:
            try:
                await target_channel.send(embed=embed)
                await interaction.followup.send(f"✅ 結果を {target_channel.mention} に送信しました。", ephemeral=True)
            except discord.Forbidden: await interaction.followup.send(f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。", ephemeral=True)
        else: await interaction.followup.send(embed=embed)

class HistoryCountModal(ui.Modal, title="履歴の表示件数"):
    def __init__(self, bot_instance: commands.Bot, db_manager: DatabaseManager):
        super().__init__(); self.bot = bot_instance; self.db_manager = db_manager
    count_input = ui.TextInput(label="表示する件数を入力してください", placeholder="1～25の半角数字で入力...", min_length=1, max_length=2)
    async def on_submit(self, interaction: discord.Interaction):
        if not self.count_input.value.isdigit() or not (1 <= int(self.count_input.value) <= 25): return await interaction.response.send_message("❌ 1から25までの半角数字を入力してください。", ephemeral=True)
        count = int(self.count_input.value)
        await interaction.response.defer(ephemeral=True, thinking=True)
        embed = await asyncio.to_thread(get_recent_matches, interaction.user.id, count)
        target_channel_id = await self.db_manager.get_user_channel(interaction.user.id)
        target_channel = self.bot.get_channel(target_channel_id) if target_channel_id else None
        if target_channel:
            try:
                await target_channel.send(embed=embed)
                await interaction.followup.send(f"✅ 結果を {target_channel.mention} に送信しました。", ephemeral=True)
            except discord.Forbidden: await interaction.followup.send(f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。", ephemeral=True)
        else: await interaction.followup.send(embed=embed)

class ShadowversePersistentPanel(ui.View):
    def __init__(self, bot_instance: commands.Bot, db_manager: DatabaseManager):
        super().__init__(timeout=None); self.bot = bot_instance; self.db_manager = db_manager
    @ui.button(label="手動登録", style=discord.ButtonStyle.success, custom_id="sv_panel:record")
    async def record(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message(embed=ManualRecordView(author_id=interaction.user.id).create_embed(), view=ManualRecordView(author_id=interaction.user.id), ephemeral=True)
    @ui.button(label="戦績表示", style=discord.ButtonStyle.primary, custom_id="sv_panel:stats")
    async def stats(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("どの期間の戦績サマリーを表示しますか？", view=StatsPeriodSelectView(author_id=interaction.user.id, bot_instance=self.bot, db_manager=self.db_manager), ephemeral=True)
    @ui.button(label="直近履歴", style=discord.ButtonStyle.secondary, custom_id="sv_panel:history")
    async def history(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_modal(HistoryCountModal(bot_instance=self.bot, db_manager=self.db_manager))
    @ui.button(label="⚙️ 通知チャンネル設定", style=discord.ButtonStyle.secondary, custom_id="sv_panel:set_channel", row=1)
    async def set_channel(self, interaction: discord.Interaction, button: ui.Button):
        category = self.bot.get_channel(TARGET_CATEGORY_ID)
        if not category or not isinstance(category, discord.CategoryChannel): return await interaction.response.send_message(f"❌ 対象カテゴリが見つかりません。", ephemeral=True)
        text_channels = category.text_channels
        if not text_channels: return await interaction.response.send_message(f"❌ カテゴリ内に選択可能なチャンネルがありません。", ephemeral=True)
        await interaction.response.send_message("通知先に設定したいチャンネルを以下から選択してください:", view=ChannelSelectView(channels=text_channels, author_id=interaction.user.id, db_manager=self.db_manager), ephemeral=True)
    @ui.button(label="全データ削除", style=discord.ButtonStyle.danger, custom_id="sv_panel:delete", row=1)
    async def delete(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.send_message("本当にあなたの全ての戦績データを削除しますか？", view=ConfirmDeleteView(author_id=interaction.user.id), ephemeral=True)


# --- メインのCogクラス ---
class ShadowversePanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Cog初期化時にDBマネージャーをインスタンス化
        self.db_manager = DatabaseManager()
        # Bot起動時に永続Viewを登録
        self.bot.add_view(ShadowversePersistentPanel(bot, self.db_manager))

    @commands.command(name="svパネル設置")
    @commands.has_permissions(manage_channels=True)
    async def create_sv_panel(self, ctx: commands.Context):
        """シャドウバース戦績管理用のボタンパネルを設置します。"""
        embed = discord.Embed(
            title="⚔️ シャドウバース 戦績管理パネル ⚔️",
            description=(
                "**⚠️ まずはじめに、`⚙️ 通知チャンネル設定` ボタンから結果を投稿する個人チャンネルを設定してください。**\n\n"
                "設定が完了したら、下の各ボタンから戦績の記録や確認ができます。\n\n"
                "📸 **リプレイ画像からの登録**は、引き続き `/replay` をご利用ください。"
            ),
            color=discord.Color.purple()
        )
        await ctx.send(embed=embed, view=ShadowversePersistentPanel(self.bot, self.db_manager))

    # --- `!svchannel` コマンド群をこのCogに統合 ---
    @commands.group(name="svchannel", invoke_without_command=True)
    async def svchannel(self, ctx: commands.Context):
        """個人通知チャンネルの管理コマンド"""
        await ctx.send(
            "**個人通知チャンネル管理コマンド**\n"
            "・通知チャンネルの設定は、戦績管理パネルの `⚙️ 通知チャンネル設定` ボタンから行えます。\n\n"
            "`!svchannel view`: 現在設定されている通知先チャンネルを確認します。\n"
            "`!svchannel clear`: 通知先の設定を解除します。"
        )

    @svchannel.command(name="view")
    async def view_channel(self, ctx: commands.Context):
        """現在設定されている通知先チャンネルを確認します。"""
        channel_id = await self.db_manager.get_user_channel(ctx.author.id)
        if channel_id:
            target_channel = self.bot.get_channel(channel_id)
            if target_channel:
                await ctx.send(f"ℹ️ あなたの通知チャンネルは **{target_channel.mention}** に設定されています。")
            else:
                await ctx.send(f"⚠️ あなたの通知チャンネル (ID: `{channel_id}`) が見つかりませんでした。")
        else:
            await ctx.send("ℹ️ あなたの通知チャンネルはまだ設定されていません。")

    @svchannel.command(name="clear")
    async def clear_channel(self, ctx: commands.Context):
        """通知先チャンネルの設定を解除します。"""
        await self.db_manager.clear_user_channel(ctx.author.id)
        await ctx.send("✅ 通知チャンネルの設定を解除しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowversePanelCog(bot))