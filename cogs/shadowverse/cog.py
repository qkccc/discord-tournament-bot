import discord
from discord.ext import commands
from discord import app_commands
import asyncio
import os
from yomitoku import DocumentAnalyzer

from .sv_constants import CLASS_NAMES
from .sv_db import init_database, get_user_channel_setting, save_records_to_db
from .sv_utils import extract_text_from_image, parse_replay_text, get_stats_summary, get_recent_matches
from .sv_ui import ManualRecordView, ControlPanelView, DeleteHistoryView

class ShadowverseCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ocr: DocumentAnalyzer | None = None
        self.model_load_task = asyncio.create_task(self._initialize_model())
        init_database()
        self.bot.add_view(ControlPanelView(self.bot))

    async def _initialize_model(self):
        """非同期でOCRモデルを読み込みます。"""
        loop = asyncio.get_running_loop()
        try:
            self.ocr = await loop.run_in_executor(None, lambda: DocumentAnalyzer(device='cpu'))
            print("Yomitoku model (Standard) loaded successfully.")
        except Exception as e:
            print(f"モデルの読み込み中にエラー: {e}")

    async def _send_result_embed_from_interaction(self, interaction: discord.Interaction, embed: discord.Embed, force_public: bool = False):
        """
        インタラクションに応じてEmbedを送信するヘルパー関数。
        force_publicがTrueの場合のみ、設定された通知チャンネルへの投稿を試みます。
        """
        target_channel_id = get_user_channel_setting(interaction.user.id)
        target_channel = self.bot.get_channel(target_channel_id) if target_channel_id else None

        if force_public and interaction.guild is not None and target_channel:
            try:
                await target_channel.send(embed=embed)
                await interaction.followup.send(f"✅ 結果を {target_channel.mention} に送信しました。", ephemeral=True)
            except discord.Forbidden:
                await interaction.followup.send(f"❌ 設定されたチャンネル {target_channel.mention} にメッセージを送信する権限がありません。代わりにここに表示します。", ephemeral=True)
                await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            # DMの場合は通常メッセージ、サーバー内の場合はephemeralステータスをdeferから引き継ぐ
            await interaction.followup.send(embed=embed)

    @commands.command(name="panel")
    async def deploy_panel(self, ctx: commands.Context):
        """コントロールパネルを設置します。"""
        embed = discord.Embed(title="⚔️ シャドウバース 戦績管理パネル ⚔️", description="下のボタンから各機能をご利用ください。\n\n**⚠️ まずはじめに、`⚙️ 通知チャンネル設定` ボタンから結果を投稿する個人チャンネルを設定してください。**", color=discord.Color.purple())
        await ctx.send(embed=embed, view=ControlPanelView(self.bot))
        try:
            await ctx.message.delete()
        except discord.Forbidden:
            pass

    @app_commands.command(name="record", description="ボタン操作で戦績を手動で登録します。")
    async def manual_record(self, interaction: discord.Interaction):
        view = ManualRecordView(author_id=interaction.user.id)
        await interaction.response.send_message(embed=view.create_embed(), view=view, ephemeral=True)

    @app_commands.command(name="replay", description="Shadowverseのリプレイ画像から戦績を記録します。")
    async def replay_record(self, interaction: discord.Interaction, image: discord.Attachment):
        if self.ocr is None:
            return await interaction.response.send_message("OCRモデル準備中です。しばらくお待ちください。", ephemeral=True)
        
        # DMでの利用時は通常の応答、サーバー内ではephemeralな応答に
        is_dm = interaction.guild is None
        await interaction.response.defer(ephemeral=not is_dm)

        if not image.content_type or not image.content_type.startswith('image/'):
            return await interaction.followup.send("画像ファイルを添付してください。")
        
        temp_image_path = f"temp_{interaction.id}.png"
        await image.save(temp_image_path)
        try:
            def processing_task():
                text_data = extract_text_from_image(self.ocr, temp_image_path)
                if not text_data: return "❌ 画像からテキストを読み取れませんでした。"
                all_records = parse_replay_text(text_data)
                if not all_records: return f"❌ 画像から戦績データを解析できませんでした。"
                
                saved_records, duplicate_count = save_records_to_db(interaction.user.id, all_records)
                
                parts = []
                if saved_records:
                    parts.append(f"✅ **{len(saved_records)}件**の新しい戦績を記録しました！")
                    details = [f"・`{r['match_time']}` **`{r['my_class']}`** vs `{r['opponent_class']}` - **{r['result']}**" for r in saved_records]
                    parts.extend(details)
                if duplicate_count > 0:
                    parts.append(f"ℹ️ 日時が重複する **{duplicate_count}件**の戦績はスキップされました。")
                
                return "\n".join(parts) if parts else "ℹ️ 新しく記録する戦績はありませんでした。"

            message = await asyncio.to_thread(processing_task)
            
            color = discord.Color.green()
            if "❌" in message: color = discord.Color.red()
            elif "ℹ️" in message and "✅" not in message: color = discord.Color.blue()
            
            embed = discord.Embed(title="リプレイ一括登録 結果", description=message, color=color)
            await self._send_result_embed_from_interaction(interaction, embed)

        finally:
            if os.path.exists(temp_image_path):
                os.remove(temp_image_path)

    @app_commands.command(name="stats", description="自分の戦績サマリーを表示します。")
    @app_commands.describe(period="集計期間", class_name="クラスを指定")
    @app_commands.choices(
        period=[
            app_commands.Choice(name="本日", value="today"), app_commands.Choice(name="昨日", value="yesterday"),
            app_commands.Choice(name="一週間", value="week"), app_commands.Choice(name="今月", value="month"),
            app_commands.Choice(name="全期間", value="all"),
        ],
        class_name=[app_commands.Choice(name=cn, value=cn) for cn in CLASS_NAMES]
    )
    async def show_stats(self, interaction: discord.Interaction, period: app_commands.Choice[str] = None, class_name: app_commands.Choice[str] = None):
        # DMでの利用時は通常の応答、サーバー内ではephemeralな応答に
        is_dm = interaction.guild is None
        await interaction.response.defer(thinking=True, ephemeral=not is_dm)
        
        selected_period = period.value if period else "today"
        selected_class = class_name.value if class_name else None
        embed = await asyncio.to_thread(get_stats_summary, interaction.user.id, selected_period, selected_class)
        await self._send_result_embed_from_interaction(interaction, embed, force_public=True)

    @app_commands.command(name="history", description="直近の戦績を指定した件数表示します。")
    @app_commands.describe(count="表示件数 (1-25)")
    async def show_history(self, interaction: discord.Interaction, count: app_commands.Range[int, 1, 25] = 5):
        await interaction.response.defer(thinking=True, ephemeral=True)
        
        embed, records = await asyncio.to_thread(get_recent_matches, interaction.user.id, count)

        if not records:
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        view = DeleteHistoryView(author_id=interaction.user.id, records=records, original_embed=embed)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowverseCog(bot))
