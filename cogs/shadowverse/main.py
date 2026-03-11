# cogs/shadowverse/main.py
import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
import datetime
from yomitoku import DocumentAnalyzer

from .sv_constants import CLASS_NAMES
from .sv_db import (
    async_init_database,
    get_user_channel_setting,
    save_records_to_db,
    get_guild_season_start_date,
    set_guild_season_start_date,
)
from .sv_utils import (
    extract_text_from_image,
    parse_replay_text,
    get_stats_summary,
    get_recent_matches,
)
from .ocr_manager import OCRManager
from .sv_ui import ManualRecordView, ControlPanelView, DeleteHistoryView


class ShadowverseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ocr: DocumentAnalyzer | None = None
        self.model_load_task = asyncio.create_task(self._initialize_model())
        self.bot.add_view(ControlPanelView(self.bot))
        # OCR処理の排他制御用Lock（他ユーザーと並列実行を防ぐ）
        self.ocr_lock = asyncio.Lock()

        # --- 右クリックメニュー（コンテキストメニュー）の定義 ---
        # メッセージを右クリックした時に表示されるメニュー
        self.ctx_menu_replay = app_commands.ContextMenu(
            name="戦績画像を読み取る",
            callback=self.replay_context_menu,
        )
        # Botのツリーにメニューを登録
        self.bot.tree.add_command(self.ctx_menu_replay)

    async def cog_load(self):
        """Cog読み込み時に非同期でDBを初期化"""
        await async_init_database()

    async def cog_unload(self):
        """Cogがアンロードされる時にメニューを削除"""
        self.bot.tree.remove_command(
            self.ctx_menu_replay.name, type=self.ctx_menu_replay.type
        )

    async def _initialize_model(self):
        """非同期でOCRモデルを読み込みます。"""
        loop = asyncio.get_running_loop()
        try:
            self.ocr = await loop.run_in_executor(
                None, lambda: DocumentAnalyzer(device="cpu")
            )
            print("Yomitoku model (Standard) loaded successfully.")
        except Exception as e:
            print(f"モデルの読み込み中にエラー: {e}")

    async def _send_result_embed_from_interaction(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        force_public: bool = False,
    ):
        """
        インタラクションに応じてEmbedを送信するヘルパー関数。
        """
        target_channel_id = await get_user_channel_setting(interaction.user.id)
        target_channel = (
            self.bot.get_channel(target_channel_id) if target_channel_id else None
        )

        if force_public and interaction.guild is not None and target_channel:
            try:
                await target_channel.send(embed=embed)
                await interaction.followup.send(
                    f"✅ 結果を {target_channel.mention} に送信しました。",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。代わりにここに表示します。",
                    ephemeral=True,
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.followup.send(embed=embed)

    # --- 共通ロジック: 画像処理と登録 ---
    async def _execute_replay_processing(
        self, interaction: discord.Interaction, attachment: discord.Attachment
    ):
        """スラッシュコマンドと右クリックメニューで共通して使う画像処理ロジック"""
        if not attachment.content_type or not attachment.content_type.startswith(
            "image/"
        ):
            return await interaction.followup.send(
                "画像ファイルを添付してください。", ephemeral=True
            )

        temp_image_path = f"temp_{interaction.id}_{attachment.id}.png"
        await attachment.save(temp_image_path)

        try:
            # OCR 処理（重い処理）- OCRManager を使用
            # 排他制御：他ユーザーのOCR処理と並列実行しない
            async with self.ocr_lock:
                async def processing_task():
                    # OCR マネージャーでテキストを抽出（フェイルオーバー対応）
                    text_data = await OCRManager.extract_text_with_fallback(temp_image_path)
                    if not text_data:
                        return "❌ 画像からテキストを読み取れませんでした。", None
                    all_records = parse_replay_text(text_data)
                    if not all_records:
                        return f"❌ 画像から戦績データを解析できませんでした。", None
                    return None, all_records

                # 非同期で実行
                error_msg, all_records = await processing_task()

            if error_msg:
                embed = discord.Embed(
                    title="リプレイ一括登録 結果",
                    description=error_msg,
                    color=discord.Color.red(),
                )
                await self._send_result_embed_from_interaction(interaction, embed)
                return

            # DB保存
            saved_records, duplicate_count = await save_records_to_db(
                interaction.user.id, all_records
            )

            parts = []
            if saved_records:
                parts.append(
                    f"✅ **{len(saved_records)}件**の新しい戦績を記録しました！"
                )
                details = [
                    f"・`{r['match_time']}` **`{r['my_class']}`** vs `{r['opponent_class']}` - **{r['result']}**"
                    for r in saved_records
                ]
                parts.extend(details)
            if duplicate_count > 0:
                parts.append(
                    f"ℹ️ 日時が重複する **{duplicate_count}件**の戦績はスキップされました。"
                )

            message = (
                "\n".join(parts)
                if parts
                else "ℹ️ 新しく記録する戦績はありませんでした。"
            )

            color = discord.Color.green()
            if "ℹ️" in message and "✅" not in message:
                color = discord.Color.blue()

            embed = discord.Embed(
                title="リプレイ一括登録 結果", description=message, color=color
            )
            await self._send_result_embed_from_interaction(interaction, embed)

        finally:
            if os.path.exists(temp_image_path):
                os.remove(temp_image_path)

    # --- コマンド定義 ---

    @commands.command(name="panel")
    async def deploy_panel(self, ctx: commands.Context):
        """コントロールパネルを設置します。"""
        embed = discord.Embed(
            title="⚔️ シャドウバース 戦績管理パネル ⚔️",
            description="下のボタンから各機能をご利用ください。\n\n**⚠️ まずはじめに、`⚙️ 通知チャンネル設定` ボタンから結果を投稿する個人チャンネルを設定してください。**",
            color=discord.Color.purple(),
        )
        await ctx.send(embed=embed, view=ControlPanelView(self.bot))
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

    @app_commands.command(
        name="record", description="ボタン操作で戦績を手動で登録します。"
    )
    async def manual_record(self, interaction: discord.Interaction):
        view = ManualRecordView(author_id=interaction.user.id)
        await interaction.response.send_message(
            embed=view.create_embed(), view=view, ephemeral=True
        )

    @app_commands.command(
        name="replay", description="Shadowverseのリプレイ画像から戦績を記録します。"
    )
    @app_commands.describe(image="リプレイ画像（1枚のみ）")
    async def replay_record(
        self, interaction: discord.Interaction, image: discord.Attachment
    ):
        is_dm = interaction.guild is None
        await interaction.response.defer(ephemeral=not is_dm)
        if not image:
            await interaction.followup.send("画像ファイルを添付してください。", ephemeral=True)
            return
        await self._execute_replay_processing(interaction, image)

    # --- 右クリックメニューのコールバック ---
    async def replay_context_menu(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        if not message.attachments:
            await interaction.response.send_message(
                "❌ このメッセージには画像が添付されていません。", ephemeral=True
            )
            return
        is_dm = interaction.guild is None
        await interaction.response.defer(ephemeral=not is_dm)
        
        total_images = len(message.attachments)
        for idx, attachment in enumerate(message.attachments, 1):
            await self._execute_replay_processing(interaction, attachment)
            # 1枚ずつ順次処理
        
        # 複数画像の場合、全処理完了の合図を送信
        if total_images > 1:
            completion_embed = discord.Embed(
                title="✅ 全処理完了",
                description=f"全{total_images}枚の画像処理が完了しました。",
                color=discord.Color.green()
            )
            await self._send_result_embed_from_interaction(interaction, completion_embed)

    @app_commands.command(name="stats", description="自分の戦績サマリーを表示します。")
    @app_commands.describe(period="集計期間", class_name="クラスを指定")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="今日", value="today"),
            app_commands.Choice(name="昨日", value="yesterday"),
            app_commands.Choice(name="一週間", value="week"),
            app_commands.Choice(name="今期(設定日~)", value="season"),
            app_commands.Choice(name="全期間", value="all"),
        ],
        class_name=[app_commands.Choice(name=cn, value=cn) for cn in CLASS_NAMES],
    )
    async def show_stats(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[str] = None,
        class_name: app_commands.Choice[str] = None,
    ):
        is_dm = interaction.guild is None
        await interaction.response.defer(thinking=True, ephemeral=not is_dm)

        selected_period = period.value if period else "today"
        selected_class = class_name.value if class_name else None
        season_start_date = (
            await get_guild_season_start_date(interaction.guild_id)
            if interaction.guild_id
            else None
        )
        # 集計処理（Pandas使用）は同期的なので to_thread のまま
        embed = await asyncio.to_thread(
            get_stats_summary,
            interaction.user.id,
            selected_period,
            selected_class,
            season_start_date,
        )
        await self._send_result_embed_from_interaction(
            interaction, embed, force_public=True
        )

    @commands.command(name="season_start", help="今期の開始日を設定または確認します。")
    async def season_start_text(self, ctx: commands.Context, start_date: str | None = None):
        if ctx.guild is None:
            await ctx.send("このコマンドはサーバー内でのみ利用できます。")
            return

        if start_date is None:
            current = await get_guild_season_start_date(ctx.guild.id)
            if current:
                date_obj = datetime.datetime.strptime(current, "%Y-%m-%d").date()
                label = f"{date_obj.month}/{date_obj.day}"
                await ctx.send(f"現在の今期開始日は **{current} ({label})** です。")
            else:
                await ctx.send(
                    "今期開始日は未設定です（未設定時は 26日開始 で計算されます）。"
                )
            return

        if not ctx.author.guild_permissions.manage_guild:
            await ctx.send("この設定を変更するには「サーバー管理」権限が必要です。")
            return

        try:
            parsed_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        except Exception:
            await ctx.send("日付形式が不正です。`YYYY-MM-DD` で入力してください。")
            return

        await set_guild_season_start_date(ctx.guild.id, parsed_date.isoformat())
        await ctx.send(
            f"✅ 今期開始日を **{parsed_date.isoformat()} ({parsed_date.month}/{parsed_date.day})** に更新しました。"
        )

    @app_commands.command(
        name="season_start", description="今期の開始日を設定または確認します。"
    )
    @app_commands.describe(start_date="開始日 (YYYY-MM-DD形式、省略可)")
    async def season_start_slash(
        self, interaction: discord.Interaction, start_date: str | None = None
    ):
        if interaction.guild is None:
            await interaction.response.send_message(
                "このコマンドはサーバー内でのみ利用できます。", ephemeral=True
            )
            return

        if start_date is None:
            current = await get_guild_season_start_date(interaction.guild.id)
            if current:
                date_obj = datetime.datetime.strptime(current, "%Y-%m-%d").date()
                label = f"{date_obj.month}/{date_obj.day}"
                await interaction.response.send_message(
                    f"現在の今期開始日は **{current} ({label})** です。", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "今期開始日は未設定です（未設定時は 26日開始 で計算されます）。",
                    ephemeral=True,
                )
            return

        if not interaction.user.guild_permissions.manage_guild:
            await interaction.response.send_message(
                "この設定を変更するには「サーバー管理」権限が必要です。",
                ephemeral=True,
            )
            return

        try:
            parsed_date = datetime.datetime.strptime(start_date, "%Y-%m-%d").date()
        except Exception:
            await interaction.response.send_message(
                "日付形式が不正です。`YYYY-MM-DD` で入力してください。", ephemeral=True
            )
            return

        await set_guild_season_start_date(interaction.guild.id, parsed_date.isoformat())
        await interaction.response.send_message(
            f"✅ 今期開始日を **{parsed_date.isoformat()} ({parsed_date.month}/{parsed_date.day})** に更新しました。",
            ephemeral=True,
        )

    @app_commands.command(
        name="history", description="直近の戦績を指定した件数表示します。"
    )
    @app_commands.describe(count="表示件数 (1-25)")
    async def show_history(
        self,
        interaction: discord.Interaction,
        count: app_commands.Range[int, 1, 25] = 5,
    ):
        await interaction.response.defer(thinking=True, ephemeral=True)

        embed, records = await asyncio.to_thread(
            get_recent_matches, interaction.user.id, count
        )

        if not records:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        view = DeleteHistoryView(
            author_id=interaction.user.id, records=records, original_embed=embed
        )
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowverseCog(bot))
