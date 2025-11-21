import discord
from discord.ext import commands
from discord import app_commands
import sqlite3

# データベースファイルへのパス
DB_NAME = 'sv_cards.db'

# --- カード管理Cogクラス ---
class ShadowverseCardManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # --- データ追加コマンド (一時的にコメントアウト) ---
    # @app_commands.command(name="addcard", description="【管理者用】新しいカードをデータベースに追加します。")
    # @app_commands.checks.has_permissions(administrator=True)
    # @app_commands.describe(
    #     name="カード名",
    #     class_name="クラス名 (例: ドラゴン)",
    #     rarity="レアリティ (例: レジェンド)",
    #     card_type="カード種別 (例: フォロワー)",
    #     cost="コスト (数字)",
    #     attack="攻撃力 (フォロワー以外は0)",
    #     health="体力 (フォロワー以外は0)",
    #     text="カードテキスト (\\nで改行)",
    #     image_url="カード画像のURL"
    # )
    # async def addcard(self, interaction: discord.Interaction, name: str, class_name: str, rarity: str, card_type: str, cost: int, attack: int, health: int, text: str, image_url: str):
    #     conn = None
    #     try:
    #         conn = sqlite3.connect(DB_NAME)
    #         cursor = conn.cursor()
    #         # テキストの \n を実際の改行に置換
    #         formatted_text = text.replace('\\n', '\n')
            
    #         cursor.execute('''
    #             INSERT INTO cards (name, class, rarity, type, cost, attack, health, text, image_url)
    #             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    #         ''', (name, class_name, rarity, card_type, cost, attack, health, formatted_text, image_url))
    #         conn.commit()
    #         await interaction.response.send_message(f"✅ カード「{name}」をデータベースに追加しました。", ephemeral=True)
    #     except sqlite3.IntegrityError:
    #         await interaction.response.send_message(f"❌ エラー: カード「{name}」は既に存在します。", ephemeral=True)
    #     except sqlite3.Error as e:
    #         await interaction.response.send_message(f"❌ データベースエラーが発生しました: {e}", ephemeral=True)
    #     finally:
    #         if conn:
    #             conn.close()

    # --- ランダム表示コマンド ---
    @app_commands.command(name="card", description="データベースからランダムにShadowverseのカードを1枚表示します。")
    async def card(self, interaction: discord.Interaction):
        conn = None
        try:
            conn = sqlite3.connect(DB_NAME)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("SELECT * FROM cards ORDER BY RANDOM() LIMIT 1")
            card = cursor.fetchone()

            if card is None:
                await interaction.response.send_message("表示できるカードがデータベースにありません。\n管理者に`/addcard`コマンドでの追加を依頼してください。", ephemeral=True)
                return

            rarity_colors = {"レジェンド": 0xFFD700, "ゴールド": 0xC0C0C0, "シルバー": 0x6E8A99, "ブロンズ": 0xCD7F32}
            embed = discord.Embed(title=f"**{card['name']}**", color=rarity_colors.get(card["rarity"], 0x777777))
            
            if card["image_url"]:
                embed.set_image(url=card["image_url"])
            embed.set_footer(text="Shadowverse Card Database")

            await interaction.response.send_message(embed=embed)
        except sqlite3.Error as e:
            await interaction.response.send_message(f"❌ データベース処理中にエラーが発生しました: {e}", ephemeral=True)
        finally:
            if conn:
                conn.close()
    
    # --- エラーハンドリング (一時的にコメントアウト) ---
    # @addcard.error
    # async def on_addcard_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
    #     if isinstance(error, app_commands.MissingPermissions):
    #         await interaction.response.send_message("❌ このコマンドを実行する権限がありません。", ephemeral=True)
    #     else:
    #         await interaction.response.send_message(f"❌ コマンドの実行中に予期せぬエラーが発生しました: {error}", ephemeral=True)


# CogをBotに登録するための必須の関数
async def setup(bot: commands.Bot):
    await bot.add_cog(ShadowverseCardManager(bot))
