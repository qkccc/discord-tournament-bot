import discord
from discord.ext import commands
from discord.ui import View, Button, button
import os

# .envファイルから読み込む環境変数名
ROOM_MATCH_CHANNEL_ID_ENV = "ROOM_MATCH_CHANNEL_ID"

# --- ボタンが押されたときの処理を定義する永続View ---
class RoomMatchPersistentView(View):
    def __init__(self, target_channel_id: int):
        super().__init__(timeout=None)
        # 募集メッセージを送信するチャンネルIDを保持
        self.target_channel_id = target_channel_id

    @button(label="@everyoneでルムマ募集", style=discord.ButtonStyle.primary, custom_id="persistent_view:room_match_separate_channel")
    async def room_match_button(self, interaction: discord.Interaction, button: Button):
        # 保持しているIDから、募集メッセージを送信するチャンネルを取得
        target_channel = interaction.guild.get_channel(self.target_channel_id)

        # チャンネルが見つからない場合のエラー処理
        if not target_channel:
            await interaction.response.send_message(
                "❌ エラー: 募集メッセージを送信するチャンネルが見つかりませんでした。\nBot管理者に連絡してください。",
                ephemeral=True
            )
            return

        # ボタンを押したユーザーとメンション設定
        # user.display_name を使用してサーバープロフィール名を取得
        user_display_name = interaction.user.display_name
        allowed_mentions = discord.AllowedMentions(everyone=True)
        
        # ★★★ ここが変更点です ★★★
        message_content = f"@everyone **{user_display_name}** さんがルームマッチを募集しています！"

        try:
            # 指定されたチャンネルに募集メッセージを送信
            await target_channel.send(content=message_content, allowed_mentions=allowed_mentions)
            # ボタンを押した本人にだけ確認メッセージを送信
            await interaction.response.send_message(
                f"✅ {target_channel.mention} にルームマッチの募集を送信しました。",
                ephemeral=True,
                delete_after=15 # 15秒後に自動で消える
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                f"❌ エラー: {target_channel.mention} にメッセージを送信する権限がありません。",
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ 不明なエラーが発生しました: {e}", ephemeral=True)


# --- Cogクラスの定義 ---
class RoomMatchCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.target_channel_id = None
        # .envからチャンネルIDを読み込む
        try:
            # 環境変数からIDを取得し、整数に変換
            self.target_channel_id = int(os.getenv(ROOM_MATCH_CHANNEL_ID_ENV))
        except (TypeError, ValueError):
            # .envに設定がない、または値が不正な場合は警告を出力
            print(f"⚠️  警告: 環境変数 '{ROOM_MATCH_CHANNEL_ID_ENV}' が.envファイルに設定されていないか、値が無効です。")

        # チャンネルIDが正常に読み込めた場合のみ、永続ViewをBotに登録
        if self.target_channel_id:
            self.bot.add_view(RoomMatchPersistentView(self.target_channel_id))

    # --- プレフィックスコマンドの定義 ---
    @commands.command(name="ルムマ募集パネル設置")
    @commands.has_permissions(manage_channels=True) # 「チャンネルの管理」権限を持つユーザーのみ実行可能
    async def create_room_match_panel(self, ctx: commands.Context):
        # Cog初期化時にチャンネルIDが読み込めていなければエラーメッセージを返す
        if not self.target_channel_id:
            await ctx.send("❌ エラー: 募集メッセージを送信するチャンネルが設定されていません。`.env`ファイルを確認してください。")
            return

        # IDからチャンネルオブジェクトを取得
        target_channel = self.bot.get_channel(self.target_channel_id)
        if not target_channel:
            await ctx.send(f"❌ エラー: 指定されたID `{self.target_channel_id}` のチャンネルが見つかりません。IDが正しいか確認してください。")
            return

        # パネルの埋め込みメッセージを作成
        embed = discord.Embed(
            title="ルームマッチ募集パネル",
            description=f"下のボタンを押すと、{target_channel.mention} に `@everyone` メンション付きでルームマッチの募集ができます。",
            color=discord.Color.blue()
        )
        
        # ViewにターゲットチャンネルIDを渡してメッセージを送信
        await ctx.send(embed=embed, view=RoomMatchPersistentView(self.target_channel_id))

    # --- コマンドのエラーハンドリング ---
    @create_room_match_panel.error
    async def create_room_match_panel_error(self, ctx: commands.Context, error: commands.CommandError):
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("❌ このコマンドを実行するには`チャンネルの管理`権限が必要です。")
        else:
            await ctx.send(f"❌ コマンド実行中にエラーが発生しました: {error}")
            print(f"コマンドエラー: {error}")


# --- BotにCogを登録するためのsetup関数 ---
async def setup(bot: commands.Bot):
    await bot.add_cog(RoomMatchCog(bot))