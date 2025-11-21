import discord
from discord.ext import commands
from discord import ui
import cv2
import numpy as np
import aiohttp
import io
from urllib.parse import urlparse, parse_qs, urlunparse, urlencode
import asyncio
import sqlite3 # データベース操作のためにインポート

# --- グローバル変数・定数 ---
DB_FILE = "deck_data.db" # 保存するデータベースファイル名

# --- データベース初期化関数 ---
def init_database():
    """データベースファイルとテーブルを初期化する"""
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    # decksテーブルを作成: user_idとdeck_urlを保存
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS decks (
            user_id INTEGER NOT NULL,
            deck_url TEXT NOT NULL,
            PRIMARY KEY (user_id, deck_url)
        )
    """)
    conn.commit()
    conn.close()

# ユーザー操作用のView
class DeckUserView(ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    @ui.button(label="デッキ登録", style=discord.ButtonStyle.success, custom_id="deck_register_button")
    async def register(self, interaction: discord.Interaction, button: ui.Button):
        if not self.cog.registration_open:
            await interaction.response.send_message("現在、デッキの登録は締め切られています。", ephemeral=True)
            return
        
        # DBから登録数を取得して上限チェック
        deck_count = await self.cog.db_get_user_deck_count(interaction.user.id)
        if deck_count >= 2:
            await interaction.response.send_message("デッキの登録は1人2つまでです。既存のデッキを削除してから再試行してください。", ephemeral=True)
            return

        await interaction.response.send_message("デッキのQRコードが写っている画像を、60秒以内に1つこのチャンネルに送信してください。", ephemeral=True)

        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and m.attachments

        try:
            msg = await self.cog.bot.wait_for('message', check=check, timeout=60.0)
        except asyncio.TimeoutError:
            await interaction.followup.send("タイムアウトしました。もう一度「デッキ登録」ボタンからやり直してください。", ephemeral=True)
            return
        
        processing_msg = await interaction.followup.send("画像を処理中です...", ephemeral=True, wait=True)
        await self.cog._process_deck_image(interaction, msg.attachments[0], processing_msg)
        
        try:
            await msg.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

    @ui.button(label="登録確認", style=discord.ButtonStyle.primary, custom_id="deck_check_button")
    async def check(self, interaction: discord.Interaction, button: ui.Button):
        await self.cog.check_user_decks(interaction)

    @ui.button(label="登録取消", style=discord.ButtonStyle.danger, custom_id="deck_cancel_button")
    async def cancel(self, interaction: discord.Interaction, button: ui.Button):
        deleted_count = await self.cog.db_delete_user_decks(interaction.user.id)
        if deleted_count > 0:
            await interaction.response.send_message(f"登録済みのデッキ{deleted_count}件を全て取り消しました。", ephemeral=True)
        else:
            await interaction.response.send_message("登録済みのデッキはありません。", ephemeral=True)

# 管理者操作用のView
class DeckAdminView(ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog
    
    @ui.button(label="登録締切", style=discord.ButtonStyle.secondary, custom_id="deck_close_button")
    async def close_registration(self, interaction: discord.Interaction, button: ui.Button):
        self.cog.registration_open = False
        await interaction.response.send_message("デッキの登録を締め切りました。", ephemeral=True)

    @ui.button(label="受付再開", style=discord.ButtonStyle.secondary, custom_id="deck_reopen_button")
    async def reopen_registration(self, interaction: discord.Interaction, button: ui.Button):
        self.cog.registration_open = True
        await interaction.response.send_message("デッキの登録を再開しました。", ephemeral=True)

    @ui.button(label="登録発表", style=discord.ButtonStyle.primary, custom_id="deck_announce_button")
    async def announce_decks(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer()
        await self.cog.announce_all_decks(interaction.channel)
        await interaction.followup.send("発表が完了しました。", ephemeral=True)

    @ui.button(label="全登録削除", style=discord.ButtonStyle.danger, custom_id="deck_delete_all_button")
    async def delete_all_registrations(self, interaction: discord.Interaction, button: ui.Button):
        class ConfirmationView(ui.View):
            def __init__(self, cog_ref):
                super().__init__(timeout=30.0)
                self.cog = cog_ref
                self.message = None

            async def on_timeout(self):
                if self.message:
                    try:
                        await self.message.edit(content="タイムアウトしました。操作はキャンセルされました。", view=None)
                    except discord.NotFound:
                        pass

            @ui.button(label="はい、全て削除します", style=discord.ButtonStyle.danger)
            async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
                await self.cog.db_delete_all_decks()
                await interaction.response.edit_message(content="全てのデッキ登録を削除しました。", view=None)
                self.stop()

            @ui.button(label="いいえ", style=discord.ButtonStyle.secondary)
            async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
                await interaction.response.edit_message(content="操作はキャンセルされました。", view=None)
                self.stop()

        view = ConfirmationView(self.cog)
        await interaction.response.send_message(
            "**警告:** 本当に全てのデッキ登録を削除しますか？\nこの操作は元に戻せません。",
            view=view,
            ephemeral=True
        )
        view.message = await interaction.original_response()

# Cogクラスの定義
class DeckCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.registration_open = True
        init_database() # Bot起動時にDBを初期化
        bot.loop.create_task(self.register_views())

    async def register_views(self):
        await self.bot.wait_until_ready()
        self.bot.add_view(DeckUserView(self))
        self.bot.add_view(DeckAdminView(self))

    # --- データベース操作メソッド ---
    def _db_add_deck(self, user_id: int, deck_url: str):
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("INSERT OR IGNORE INTO decks (user_id, deck_url) VALUES (?, ?)", (user_id, deck_url))

    async def db_add_deck(self, user_id: int, deck_url: str):
        await asyncio.to_thread(self._db_add_deck, user_id, deck_url)

    def _db_get_user_decks(self, user_id: int) -> list[str]:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.execute("SELECT deck_url FROM decks WHERE user_id = ?", (user_id,))
            return [row[0] for row in cursor.fetchall()]

    async def db_get_user_decks(self, user_id: int) -> list[str]:
        return await asyncio.to_thread(self._db_get_user_decks, user_id)

    def _db_get_user_deck_count(self, user_id: int) -> int:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.execute("SELECT COUNT(*) FROM decks WHERE user_id = ?", (user_id,))
            return cursor.fetchone()[0]

    async def db_get_user_deck_count(self, user_id: int) -> int:
        return await asyncio.to_thread(self._db_get_user_deck_count, user_id)

    def _db_delete_user_decks(self, user_id: int) -> int:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM decks WHERE user_id = ?", (user_id,))
            return cursor.rowcount

    async def db_delete_user_decks(self, user_id: int) -> int:
        return await asyncio.to_thread(self._db_delete_user_decks, user_id)

    def _db_get_all_decks(self) -> dict[int, list[str]]:
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.execute("SELECT user_id, deck_url FROM decks ORDER BY user_id")
            all_decks = {}
            for user_id, deck_url in cursor.fetchall():
                all_decks.setdefault(user_id, []).append(deck_url)
            return all_decks

    async def db_get_all_decks(self) -> dict[int, list[str]]:
        return await asyncio.to_thread(self._db_get_all_decks)

    def _db_delete_all_decks(self):
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute("DELETE FROM decks")

    async def db_delete_all_decks(self):
        await asyncio.to_thread(self._db_delete_all_decks)


    # --- ヘルパー関数 ---
    def _transform_deck_url(self, qr_url: str) -> str | None:
        try:
            if "shadowverse-wb.com" not in qr_url or "/ja/deck/detail/" not in qr_url:
                return None
            parsed_url = urlparse(qr_url)
            query_params = parse_qs(parsed_url.query)
            if 'hash' not in query_params: return None
            hash_value = query_params['hash'][0]
            new_query_params = {'hash': hash_value, 'lang': 'ja'}
            return urlunparse(('https', 'shadowverse-wb.com', '/web/Image/deck', '', urlencode(new_query_params), ''))
        except Exception as e:
            print(f"URL transformation error: {e}")
            return None

    async def _process_deck_image(self, interaction: discord.Interaction, attachment: discord.Attachment, processing_msg: discord.WebhookMessage):
        if not attachment.content_type or not attachment.content_type.startswith('image/'):
            await processing_msg.edit(content="画像ファイルを添付してください。")
            return
        
        try:
            image_bytes = await attachment.read()
            np_array = np.frombuffer(image_bytes, np.uint8)
            img_color = cv2.imdecode(np_array, cv2.IMREAD_COLOR)

            if img_color is None:
                await processing_msg.edit(content="画像の読み込みに失敗しました。")
                return

            detector = cv2.QRCodeDetector()
            qr_data = None
            
            gray_img = cv2.cvtColor(img_color, cv2.COLOR_BGR2GRAY)
            
            # 試行1: グレースケール
            qr_data, _, _ = detector.detectAndDecode(gray_img)
            # 試行2: 右上
            if not qr_data:
                h, w = gray_img.shape
                qr_data, _, _ = detector.detectAndDecode(gray_img[0:h//2, w//2:w])
            # 試行3: 二値化
            if not qr_data:
                _, binary_img = cv2.threshold(gray_img, 128, 255, cv2.THRESH_BINARY)
                qr_data, _, _ = detector.detectAndDecode(binary_img)
            # 試行4: 拡大
            if not qr_data:
                h, w = gray_img.shape
                upscaled = cv2.resize(gray_img, (w*2, h*2), interpolation=cv2.INTER_CUBIC)
                qr_data, _, _ = detector.detectAndDecode(upscaled)

            if not qr_data:
                await processing_msg.edit(content="QRコードを検出できませんでした。")
                return
            
            deck_image_url = self._transform_deck_url(qr_data)
            if not deck_image_url:
                await processing_msg.edit(content="読み取られたURLが正しくありません。")
                return
            
            await self.db_add_deck(interaction.user.id, deck_image_url)

            async with aiohttp.ClientSession() as session:
                async with session.get(deck_image_url) as resp:
                    if resp.status == 200:
                        file = discord.File(io.BytesIO(await resp.read()), filename="deck.png")
                        await processing_msg.edit(content="デッキを登録しました！", attachments=[file])
                    else:
                        await processing_msg.edit(content="デッキを登録しました！\n(確認画像の表示に失敗)")

        except Exception as e:
            print(f"Deck registration error: {e}")
            await processing_msg.edit(content="デッキの登録中にエラーが発生しました。")

    async def check_user_decks(self, interaction: discord.Interaction):
        user_decks = await self.db_get_user_decks(interaction.user.id)
        if not user_decks:
            await interaction.response.send_message("登録済みのデッキはありません。", ephemeral=True)
            return
        
        await interaction.response.defer(ephemeral=True)
        
        files_to_send = []
        async with aiohttp.ClientSession() as session:
            for i, url in enumerate(user_decks):
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            files_to_send.append(discord.File(io.BytesIO(await resp.read()), filename=f"deck_{i+1}.png"))
                except Exception as e:
                    print(f"Error fetching deck image for check: {e}")
        
        if files_to_send:
            await interaction.followup.send("あなたが登録したデッキはこちらです。", files=files_to_send, ephemeral=True)
        else:
            await interaction.followup.send("デッキ画像の取得に失敗しました。")

    async def announce_all_decks(self, channel: discord.TextChannel):
        all_decks = await self.db_get_all_decks()
        if not all_decks:
            await channel.send("登録されたデッキは1つもありません。")
            return

        await channel.send("--- 全員の登録デッキを発表します！ ---")
        
        for user_id, deck_urls in all_decks.items():
            try:
                user = await self.bot.fetch_user(user_id)
                await channel.send(f"▼ {user.mention} さんのデッキ")
                
                files_to_send = []
                async with aiohttp.ClientSession() as session:
                    for i, url in enumerate(deck_urls):
                        try:
                            async with session.get(url) as resp:
                                if resp.status == 200:
                                    files_to_send.append(discord.File(io.BytesIO(await resp.read()), filename=f"{user.name}_deck_{i+1}.png"))
                        except Exception as e:
                             print(f"Error fetching deck image for announce: {e}")
                
                if files_to_send:
                    await channel.send(files=files_to_send)
                else:
                    await channel.send("（デッキ画像の取得に失敗しました）")
            except discord.NotFound:
                await channel.send(f"ID: `{user_id}` のユーザーが見つかりませんでした。")
            except Exception as e:
                print(f"Announce error for user {user_id}: {e}")
                await channel.send(f"ユーザーID: `{user_id}` のデッキ発表中にエラーが発生しました。")
    
    # --- コマンド ---
    @commands.hybrid_command(name="deck_panel_user", description="ユーザー用のデッキ操作パネルを設置します。")
    @commands.has_permissions(administrator=True)
    async def deck_panel_user(self, ctx: commands.Context):
        embed = discord.Embed(title="シャドウバース デッキ登録", description="下のボタンからデッキの登録・確認・取消を行ってください。", color=discord.Color.blue())
        embed.add_field(name="登録ルール", value="・1人2デッキまで登録可能です。\n・登録を取り消すと、登録したデッキが全て削除されます。\n・デッキは1つずつ登録してください。")
        await ctx.send(embed=embed, view=DeckUserView(self))
    
    @commands.hybrid_command(name="deck_panel_admin", description="管理者用の操作パネルを設置します。")
    @commands.has_permissions(administrator=True)
    async def deck_panel_admin(self, ctx: commands.Context):
        embed = discord.Embed(title="管理者用パネル", description="下のボタンでデッキ登録の管理を行ってください。", color=discord.Color.dark_red())
        await ctx.send(embed=embed, view=DeckAdminView(self))

async def setup(bot: commands.Bot):
    await bot.add_cog(DeckCog(bot))

