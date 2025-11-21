import discord
from discord.ext import commands
import asyncio

# リアクションのトリガーと、それに対応するリアクションのリストをマッピングで管理します。
# ここに新しいトリガーとリアクションの組み合わせを追加するだけで、機能拡張ができます。
REACTION_MAP = {
    # ⭐ が押されたら、クラス絵文字を順番に追加
    "⭐": [
        discord.PartialEmoji(name="Class_Forestcraft", id=922142168473301082),
        discord.PartialEmoji(name="Class_Swordcraft", id=922142203323744296),
        discord.PartialEmoji(name="Class_Runecraft", id=922142232595791953),
        discord.PartialEmoji(name="Class_Dragoncraft", id=922142264942284830),
        discord.PartialEmoji(name="Class_Abysscraft", id=1410146572930519040),
        discord.PartialEmoji(name="Class_Havencraft", id=922142398073700433),
        discord.PartialEmoji(name="Class_Portalcraft", id=922142424380346399),
    ],
    # ✅ が押されたら、⭕ と ❌ を追加 (Unicode絵文字もそのまま使えます)
    "✅": ["⭕", "❌"],
}


class ReactionCog(commands.Cog):
    """
    特定のリアクションやメッセージをトリガーにして、リアクションを追加するCog
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """
        メッセージが送信されたときに呼び出されるイベントリスナー
        @everyone を含むメッセージに特定の絵文字でリアクションします。
        """
        # Bot自身のメッセージは無視する
        if message.author == self.bot.user:
            return

        # メッセージに @everyone が含まれている場合
        if "@everyone" in message.content:
            try:
                # 指定されたカスタム絵文字でリアクション
                await message.add_reaction("<:Mimashita:1425853002392272999>")
            except discord.Forbidden:
                # リアクション追加の権限がない場合
                print(f"リアクションの追加権限がありません: channel_id={message.channel.id}")
            except discord.HTTPException as e:
                # 絵文字が見つからない、またはその他のHTTPエラー
                print(f"リアクション追加中にHTTPエラー: {e}")
            except Exception as e:
                print(f"リアクション追加中に予期せぬエラー: {e}")


    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        """
        リアクションが追加されたときに呼び出されるイベントリスナー
        on_reaction_add と違い、Botが起動する前のメッセージにも対応できます
        """
        # リアクションを追加したのがBot自身の場合は無視する
        if payload.user_id == self.bot.user.id:
            return

        # トリガーとなる絵文字がマップに存在するか確認
        trigger_emoji = str(payload.emoji)
        if trigger_emoji not in REACTION_MAP:
            return

        # 対応するリアクションリストを取得
        reactions_to_add = REACTION_MAP[trigger_emoji]

        # リアクションが付けられたチャンネルとメッセージを取得
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if not channel or not isinstance(channel, discord.TextChannel):
                return # DMなど、テキストチャンネル以外は無視
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            # メッセージが見つからない場合は何もしない
            print(f"メッセージが見つかりませんでした: {payload.message_id}")
            return
        except discord.Forbidden:
            # チャンネルにアクセス権がない場合は何もしない
            print(f"チャンネルにアクセスできません: {payload.channel_id}")
            return
        except Exception as e:
            print(f"メッセージ取得中に予期せぬエラー: {e}")
            return

        # 定義された絵文字リストを順番にリアクションとして追加
        try:
            for emoji in reactions_to_add:
                await message.add_reaction(emoji)
                # リアクションが早すぎると順番が分かりにくいため、少し待機する
                await asyncio.sleep(0.5)
        except discord.Forbidden:
            # リアクション追加の権限がない場合
            print(f"リアクションの追加権限がありません: channel_id={payload.channel_id}")
        except Exception as e:
            print(f"リアクション追加中にエラー: {e}")


async def setup(bot: commands.Bot):
    """
    BotにCogを登録するためのセットアップ関数
    """
    await bot.add_cog(ReactionCog(bot))
    #print("ReactionCogが正常にロードされました。")
