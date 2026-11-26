import discord
from discord.ext import commands
from discord import ui
import pandas as pd
import os
import asyncio
import sqlite3  # SQLiteを扱うためにインポート

# --- 定数 ---
DB_FILE = "data/main.db"


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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sv_user_settings (
                    user_id INTEGER PRIMARY KEY,
                    channel_id INTEGER NOT NULL
                )
            """)
            conn.commit()

    # --- 同期メソッド (裏側で実行) ---
    def _set_channel_sync(self, user_id: int, channel_id: int):
        with self._get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sv_user_settings (user_id, channel_id) VALUES (?, ?)",
                (user_id, channel_id),
            )

    def _get_channel_sync(self, user_id: int) -> int | None:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT channel_id FROM sv_user_settings WHERE user_id = ?", (user_id,)
            )
            result = cursor.fetchone()
            return result[0] if result else None

    def _clear_channel_sync(self, user_id: int):
        with self._get_connection() as conn:
            conn.execute("DELETE FROM sv_user_settings WHERE user_id = ?", (user_id,))

    def _get_all_channels_sync(self) -> list[tuple[int, int]]:
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT user_id, channel_id FROM sv_user_settings ORDER BY user_id"
            )
            return cursor.fetchall()

    # --- 非同期メソッド (Cogから呼び出す用) ---
    async def set_user_channel(self, user_id: int, channel_id: int):
        await asyncio.to_thread(self._set_channel_sync, user_id, channel_id)

    async def get_user_channel(self, user_id: int) -> int | None:
        return await asyncio.to_thread(self._get_channel_sync, user_id)

    async def clear_user_channel(self, user_id: int):
        await asyncio.to_thread(self._clear_channel_sync, user_id)

    async def get_all_channels(self) -> list[tuple[int, int]]:
        return await asyncio.to_thread(self._get_all_channels_sync)


# --- UIコンポーネント (View, Modalなど) ---
# 修正: 同フォルダ内のモジュールからインポートするように変更
try:
    from .sv_ui import ManualRecordView
    from .sv_utils import (
        generate_stats_summary_image,
        get_stats_summary,
        get_recent_matches,
    )
    from .sv_db import get_effective_season_start_date, get_user_grandmaster_at
except ImportError:

    class ManualRecordView(ui.View):
        pass

    def get_stats_summary(
        user_id, period, class_name=None, season_start_date=None, grandmaster_at=None
    ):
        return discord.Embed(title="Error")

    def get_recent_matches(user_id, count):
        return (discord.Embed(title="Error"), [])

    async def get_effective_season_start_date(guild_id):
        return None

    async def get_user_grandmaster_at(user_id):
        return None


# (以降のView, ModalもDBマネージャーを使うように修正済み)
class StatsPeriodSelectView(ui.View):
    def __init__(
        self, author_id: int, bot_instance: commands.Bot, db_manager: DatabaseManager
    ):
        super().__init__(timeout=60)
        self.author_id = author_id
        self.bot = bot_instance
        self.db_manager = db_manager

    @ui.select(
        placeholder="集計期間を選択してください...",
        options=[
            discord.SelectOption(label="今日", value="today"),
            discord.SelectOption(label="昨日", value="yesterday"),
            discord.SelectOption(label="3日間", value="3days"),
            discord.SelectOption(label="一週間", value="week"),
            discord.SelectOption(label="今期(設定日~)", value="season"),
            discord.SelectOption(label="グラマス後", value="grandmaster"),
            discord.SelectOption(label="全期間", value="all"),
        ],
    )
    async def select_period(self, interaction: discord.Interaction, select: ui.Select):
        selected_period = select.values[0]
        await interaction.response.edit_message(
            content="統計を生成しています...", view=None
        )
        season_start_date = await get_effective_season_start_date(interaction.guild_id)
        grandmaster_at = await get_user_grandmaster_at(interaction.user.id)
        stats_file = await asyncio.to_thread(
            generate_stats_summary_image,
            interaction.user.id,
            selected_period,
            None,
            season_start_date,
            grandmaster_at,
        )
        if stats_file is None:
            embed = await asyncio.to_thread(
                get_stats_summary,
                interaction.user.id,
                selected_period,
                None,
                season_start_date,
                grandmaster_at,
            )
        else:
            embed = None

        target_channel_id = await self.db_manager.get_user_channel(interaction.user.id)
        target_channel = (
            self.bot.get_channel(target_channel_id) if target_channel_id else None
        )
        if target_channel:
            try:
                send_kwargs = {}
                if embed is not None:
                    send_kwargs["embed"] = embed
                if stats_file is not None:
                    send_kwargs["file"] = stats_file
                await target_channel.send(**send_kwargs)
                await interaction.followup.send(
                    f"✅ 結果を {target_channel.mention} に送信しました。",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。",
                    ephemeral=True,
                )
        else:
            send_kwargs = {}
            if embed is not None:
                send_kwargs["embed"] = embed
            if stats_file is not None:
                send_kwargs["file"] = stats_file
            await interaction.followup.send(**send_kwargs)


class HistoryCountModal(ui.Modal, title="履歴の表示件数"):
    def __init__(self, bot_instance: commands.Bot, db_manager: DatabaseManager):
        super().__init__()
        self.bot = bot_instance
        self.db_manager = db_manager
        self.count_input = ui.TextInput(
            label="表示する件数を入力してください",
            placeholder="1～25の半角数字で入力...",
            min_length=1,
            max_length=2,
        )
        self.add_item(self.count_input)

    async def on_submit(self, interaction: discord.Interaction):
        import logging

        logger = logging.getLogger(__name__)
        try:
            if not self.count_input.value.isdigit() or not (
                1 <= int(self.count_input.value) <= 25
            ):
                return await interaction.response.send_message(
                    "❌ 1から25までの半角数字を入力してください。", ephemeral=True
                )
            count = int(self.count_input.value)

            await interaction.response.defer(ephemeral=True, thinking=True)

            embed, _ = await asyncio.to_thread(
                get_recent_matches, interaction.user.id, count
            )
            target_channel_id = await self.db_manager.get_user_channel(
                interaction.user.id
            )
            target_channel = (
                self.bot.get_channel(target_channel_id) if target_channel_id else None
            )

            if target_channel:
                try:
                    await target_channel.send(embed=embed)
                    await interaction.followup.send(
                        f"✅ 結果を {target_channel.mention} に送信しました。",
                        ephemeral=True,
                    )
                except discord.Forbidden:
                    await interaction.followup.send(
                        f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。",
                        ephemeral=True,
                    )
            else:
                await interaction.followup.send(embed=embed)
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            logger.error(f"[HistoryCountModal] Exception occurred:\n{tb}")
            try:
                await interaction.followup.send(
                    f"❌ エラーが発生しました: {e}", ephemeral=True
                )
            except Exception as follow_e:
                logger.error(f"[HistoryCountModal] followup send failed: {follow_e}")


PANEL_TITLE = "### ⚔️ シャドウバース 戦績管理パネル ⚔️"
PANEL_DESCRIPTION = "下の各ボタンから戦績の記録や確認ができます。"


class ShadowversePersistentPanel(ui.LayoutView):
    """Components V2 (Container) 版の永続パネル。custom_id は旧パネルと互換のため据え置き。"""

    def __init__(self, bot_instance: commands.Bot, db_manager: DatabaseManager):
        super().__init__(timeout=None)
        self.bot = bot_instance
        self.db_manager = db_manager

        stats_button = ui.Button(
            label="戦績表示", style=discord.ButtonStyle.primary, custom_id="sv_panel:stats"
        )
        stats_button.callback = self.stats

        history_button = ui.Button(
            label="直近履歴", style=discord.ButtonStyle.secondary, custom_id="sv_panel:history"
        )
        history_button.callback = self.history

        record_button = ui.Button(
            label="手動登録", style=discord.ButtonStyle.success, custom_id="sv_panel:record"
        )
        record_button.callback = self.record

        container = ui.Container(
            ui.TextDisplay(PANEL_TITLE),
            ui.TextDisplay(PANEL_DESCRIPTION),
            ui.Separator(),
            ui.ActionRow(stats_button, history_button, record_button),
            accent_colour=discord.Color.purple(),
        )
        self.add_item(container)

    async def record(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            embed=ManualRecordView(author_id=interaction.user.id).create_embed(),
            view=ManualRecordView(author_id=interaction.user.id),
            ephemeral=True,
        )

    async def stats(self, interaction: discord.Interaction):
        await interaction.response.send_message(
            "どの期間の戦績サマリーを表示しますか？",
            view=StatsPeriodSelectView(
                author_id=interaction.user.id,
                bot_instance=self.bot,
                db_manager=self.db_manager,
            ),
            ephemeral=True,
        )

    async def history(self, interaction: discord.Interaction):
        await interaction.response.send_modal(
            HistoryCountModal(bot_instance=self.bot, db_manager=self.db_manager)
        )


# --- メインのCogクラス ---
class ShadowversePanelCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db_manager = DatabaseManager()
        # --- パネルの永続View自動復元 ---
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sv_panel_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute("SELECT message_id, channel_id FROM sv_panel_messages")
            rows = cur.fetchall()
        finally:
            conn.close()
        for message_id, channel_id in rows:
            try:
                self.bot.add_view(
                    ShadowversePersistentPanel(bot, self.db_manager),
                    message_id=message_id,
                )
                print(f"[Panel復元] message_id={message_id} channel_id={channel_id}")
            except Exception as e:
                print(f"[Panel復元エラー] message_id={message_id} error={e}")
        # 新規設置用のViewも登録
        self.bot.add_view(ShadowversePersistentPanel(bot, self.db_manager))

    @commands.command(name="svパネル設置")
    @commands.has_permissions(manage_channels=True)
    async def create_sv_panel(self, ctx: commands.Context):
        """シャドウバース戦績管理用のボタンパネルを設置し、メッセージIDをDBに保存します。"""
        view = ShadowversePersistentPanel(self.bot, self.db_manager)
        message = await ctx.send(view=view)
        # --- メッセージIDをDBに保存 ---
        try:
            conn = sqlite3.connect(DB_FILE)
            cur = conn.cursor()
            cur.execute("""
                CREATE TABLE IF NOT EXISTS sv_panel_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            cur.execute(
                "INSERT INTO sv_panel_messages (message_id, channel_id) VALUES (?, ?)",
                (message.id, message.channel.id),
            )
            conn.commit()
        finally:
            conn.close()
        print(f"[Panel設置] message_id={message.id} channel_id={message.channel.id}")

    @commands.command(name="sv通知チャンネル設定")
    @commands.has_permissions(manage_channels=True)
    async def set_notification_channel_for_member(
        self, ctx: commands.Context, member: discord.Member, channel: discord.TextChannel
    ):
        """管理者用: 指定したメンバーの戦績通知チャンネルを設定します。"""
        await self.db_manager.set_user_channel(member.id, channel.id)
        await ctx.send(
            embed=discord.Embed(
                title="✅ 設定完了",
                description=f"{member.mention} の通知チャンネルを {channel.mention} に設定しました。",
                color=0x2ECC71,
            )
        )

    @commands.command(name="sv通知チャンネル一覧")
    @commands.has_permissions(manage_channels=True)
    async def list_notification_channels(self, ctx: commands.Context):
        """管理者用: 現在の通知チャンネル登録状況を一覧表示します。"""
        rows = await self.db_manager.get_all_channels()
        if not rows:
            return await ctx.send(
                embed=discord.Embed(
                    title="📋 通知チャンネル登録状況", description="登録なし", color=0x95A5A6
                )
            )
        lines = []
        for user_id, channel_id in rows:
            member = ctx.guild.get_member(user_id) if ctx.guild else None
            member_text = member.display_name if member else f"ID: {user_id}"
            channel = ctx.guild.get_channel(channel_id) if ctx.guild else None
            channel_text = channel.mention if channel else f"ID: {channel_id}（見つかりません）"
            lines.append(f"・{member_text} → {channel_text}")
        # Embedのdescription上限(4096文字)を超える場合は複数メッセージに分割
        chunks, current = [], ""
        for line in lines:
            if len(current) + len(line) + 1 > 4000:
                chunks.append(current)
                current = ""
            current += line + "\n"
        if current:
            chunks.append(current)
        for i, chunk in enumerate(chunks):
            title = "📋 通知チャンネル登録状況" + (f" ({i + 1}/{len(chunks)})" if len(chunks) > 1 else "")
            await ctx.send(embed=discord.Embed(title=title, description=chunk, color=0x3498DB))

    # --- `!svchannel` コマンド群をこのCogに統合 ---
    @commands.group(name="svchannel", invoke_without_command=True)
    async def svchannel(self, ctx: commands.Context):
        """個人通知チャンネルの管理コマンド"""
        await ctx.send(
            "**個人通知チャンネル管理コマンド**\n"
            "・通知チャンネルの設定は管理者が行います（`!sv通知チャンネル設定 <@メンバー> <#チャンネル>`）。\n\n"
            "`!svchannel view`: 現在設定されている通知先チャンネルを確認します。\n"
            "`!svchannel clear`: 通知先の設定を解除します。\n"
            "`!sv通知チャンネル一覧`: 管理者用、全員の登録状況を確認します。"
        )

    @svchannel.command(name="view")
    async def view_channel(self, ctx: commands.Context):
        """現在設定されている通知先チャンネルを確認します。"""
        channel_id = await self.db_manager.get_user_channel(ctx.author.id)
        if channel_id:
            target_channel = self.bot.get_channel(channel_id)
            if target_channel:
                await ctx.send(
                    f"ℹ️ あなたの通知チャンネルは **{target_channel.mention}** に設定されています。"
                )
            else:
                await ctx.send(
                    f"⚠️ あなたの通知チャンネル (ID: `{channel_id}`) が見つかりませんでした。"
                )
        else:
            await ctx.send("ℹ️ あなたの通知チャンネルはまだ設定されていません。")

    @svchannel.command(name="clear")
    async def clear_channel(self, ctx: commands.Context):
        """通知先チャンネルの設定を解除します。"""
        await self.db_manager.clear_user_channel(ctx.author.id)
        await ctx.send("✅ 通知チャンネルの設定を解除しました。")


async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowversePanelCog(bot))
