# apeiron_bot.py (修正後)
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import zoneinfo
import os
from dotenv import load_dotenv
import logging

# .envファイルから環境変数を読み込む
load_dotenv()

# yomitokuライブラリのロガーを直接取得し、ログの出力を完全に抑制します。
# これにより、ライブラリ内部の特殊なログ設定に影響されずにログを非表示にします。
yomi_logger = logging.getLogger('yomitoku')
yomi_logger.setLevel(logging.CRITICAL + 1) # ログレベルをCRITICALより上に設定して事実上無効化
yomi_logger.propagate = False              # 上位のロガーにログが伝播するのを防ぐ

# Botの初期設定 (main.pyのより多くの権限を持つ設定を採用)
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True # スラッシュコマンドやギルド情報に必要
intents.voice_states = True     # ユーザーのボイスチャンネルへの接続状態を知るために必要

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# 読み込むCogsのリスト
INITIAL_EXTENSIONS = [
    # 🏆 イベント関連 (eventsフォルダへ移動)
    'cogs.events.event_manager.cog',
    'cogs.events.roundrobin.cog',

    # ⚔️ Shadowverse関連 (shadowverseフォルダへ集約)
    'cogs.shadowverse.main',         # 元 cog.py
    'cogs.shadowverse.deck',         # 元 deck_cog.py
    'cogs.shadowverse.panel',        # 元 shadowverse_panel_cog.py
    'cogs.shadowverse.card_manager', # 元 shadowverse_card_manager/cog.py
    
    # 🎵 音声関連 (設定済み)
    'cogs.audio.music',
    'cogs.audio.voice_logger',

    # 🛠️ 便利機能 (設定済み)
    'cogs.utils.gemini',
    'cogs.utils.room_match',
    'cogs.utils.reaction',
]

# --- 定期実行タスク ---
TARGET_CHANNEL_ID = 941626417345593345 # チャンネルID
JST = zoneinfo.ZoneInfo("Asia/Tokyo")
TARGET_TIME = datetime.time(hour=22, minute=0, tzinfo=JST)

@tasks.loop(time=TARGET_TIME)
async def send_regular_announcement():
    if datetime.datetime.now(JST).weekday() != 5: return
    channel = bot.get_channel(TARGET_CHANNEL_ID)
    if channel:
        await channel.send("@everyone 定例5分前です\n欠席の方は ⁠#定例-出欠✅ に書き込んでください")

# --- カスタムヘルプコマンド ---
@bot.command(name='ヘルプ', aliases=['help'])
async def custom_help(ctx):
    embed = discord.Embed(
        title="🏆 Botコマンド一覧",
        description="このBotで利用できるコマンドの一覧です。",
        color=0x00aaff
    )
    embed.add_field(
        name="⚔️ 大会・チーム分け機能",
        value=(
            "**`!募集`** : イベント参加者の募集を開始します。\n"
            "参加者は👍リアクションで参加し、表示されるボタンでイベント（スイスドロー、トーナメント、チーム分け）を開始・操作します。\n"
            "**詳しくは `!ヘルプ3` を参照してください。**"
        ),
        inline=False
    )
    embed.add_field(
        name="その他",
        value=(
            "**`!ヘルプ`** : このヘルプメッセージを表示します。\n"
            "**`!ヘルプ2`** : 音楽機能のヘルプメッセージを表示します。\n"
            "**`!ヘルプ3`** : 大会・チーム分け機能の詳細なヘルプを表示します。\n"
            "**`!ヘルプ4`** : Shadowverse戦績管理機能のヘルプを表示します。\n"
            "**`!ヘルプ5`** : チーム総当たり戦機能のヘルプを表示します。\n"
            "**`@Bot名 [メッセージ]`**: Botと会話します（Gemini）"
        ),
        inline=False
    )
    embed.set_footer(text="Shadowverse関連のコマンドはスラッシュ(/)で入力します。")
    await ctx.send(embed=embed)

@bot.command(name='ヘルプ2')
async def help2(ctx):
    """音楽機能のヘルプメッセージを表示します。"""
    embed = discord.Embed(
        title="🎵 音楽機能コマンド一覧",
        description="YouTubeの音楽を再生するためのコマンドです。",
        color=0x3498db
    )
    embed.add_field(
        name="基本操作",
        value=(
            "**`!通話`**: Botをボイスチャンネルに呼び出します。\n"
            "**`!再生 [曲名 or URL]`**: 曲を検索して再生リストに追加します。\n"
            "**`!退出`**: Botをボイスチャンネルから退出させます。"
        ),
        inline=False
    )
    embed.add_field(
        name="再生コントロール",
        value=(
            "**`!一時停止`**: 再生を一時停止します。\n"
            "**`!再開`**: 再生を再開します。\n"
            "**`!スキップ`**: 現在の曲をスキップします。\n"
            "**`!停止`**: 再生を完全に停止し、再生リストを空にします。"
        ),
        inline=False
    )
    embed.add_field(
        name="再生リスト管理",
        value=(
            "**`!一覧`**: 現在の再生リストを表示します。"
        ),
        inline=False
    )
    embed.set_footer(text="ボイスチャンネルに参加してからコマンドを使用してください。")
    await ctx.send(embed=embed)


@bot.command(name='ヘルプ4')
async def help4(ctx):
    """Shadowverse戦績管理機能のヘルプメッセージを表示します。"""
    embed = discord.Embed(
        title="⚔️ Shadowverse戦績管理ヘルプ",
        description="戦績管理機能で利用できるコマンドやボタン操作の一覧です。",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="【推奨】パネルからの操作",
        value=(
            "指定されたチャンネルにあるパネルのボタンから、直感的にほとんどの機能を利用できます。\n"
            "・**手動登録**: ボタン操作で1戦ずつ戦績を記録します。\n"
            "・**戦績表示**: 期間とクラスを指定して、詳細な戦績サマリーを表示します。\n"
            "・**直近履歴**: 記録した最新の対戦履歴を表示します。\n"
            "・**通知チャンネル設定**: 戦績の表示先チャンネルを設定・変更します。\n"
            "・**全データ削除**: あなたの全データを削除します（要確認）。"
        ),
        inline=False
    )
    embed.add_field(
        name="スラッシュコマンドでの操作",
        value=(
            "パネル操作に加えて、以下のスラッシュコマンドも利用可能です。\n"
            "・**/replay [image]**: リプレイのスクリーンショットから戦績を一括登録します。\n"
            "・**/record**: ボタン操作と同じ手動登録を開始します。\n"
            "・**/stats [period] [class_name]**: 期間とクラスを指定して戦績サマリーを表示します。\n"
            "・**/history [count]**: 直近の対戦履歴を指定した件数表示します。"
        ),
        inline=False
    )
    embed.add_field(
        name="テキストコマンド",
        value=(
            "**`!panel`**: 管理用の操作パネルを再設置する際に使用します。"
        ),
        inline=False
    )
    await ctx.send(embed=embed)

# ▼▼▼ 修正: ヘルプ5の内容を更新 ▼▼▼
@bot.command(name='ヘルプ5')
async def help5(ctx):
    """チーム総当たり戦機能のヘルプメッセージを表示します。"""
    embed = discord.Embed(
        title="⚔️ チーム総当たり戦 ヘルプ",
        description="チームを組んで総当たり戦を行うための機能です。",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="【基本】パネルからの操作",
        value=(
            "大会管理者が `!総当たりパネル` で設置したパネルから、ボタン操作で直感的に参加できます。\n"
            "・**チーム作成**: 新しいチームを作り、自分がリーダーになります。\n"
            "・**チームに参加**: 既存のチームにメンバーとして参加します。\n"
            "・**チームから脱退**: 所属しているチームから抜けます。"
        ),
        inline=False
    )
    embed.add_field(
        name="コマンドでのチーム管理",
        value=(
            "**`!チーム登録 [チーム名] [メンバー1] [メンバー2] ...`**: チームとメンバーを一括で登録します。\n"
            "**`!チームメンバー追加 [チーム名] [メンバー]`**: 既存のチームにメンバーを追加します。\n"
            "**`!チーム取消 [チーム名]`**: チームを削除します。"
        ),
        inline=False
    )
    embed.add_field(
        name="大会の進行",
        value=(
            "**`!総当たり開始`**: 全チームの登録完了後、大会を開始します。\n"
            "**`!次節`**: 現在の節の全試合が終了した後、次の節に進みます。\n"
            "**`!順位`**: 現在の順位表を表示します。\n"
            "**`!総当たり中止`**: (管理者用) 大会全体をリセットし、作成されたロールも全て削除します。"
        ),
        inline=False
    )
    embed.add_field(
        name="試合結果の報告",
        value=(
            "大会が始まると、各試合の対戦カードが表示されます。\n"
            "試合終了後、代表者がカードの「**結果報告**」ボタンを押してスコアを入力してください。\n"
            "入力ミスがあった場合は「**結果を訂正**」ボタンから修正できます。"
        ),
        inline=False
    )
    await ctx.send(embed=embed)
# ▲▲▲ 修正ここまで ▲▲▲

# --- Botのメイン処理 ---

# Cogをロードする非同期関数
async def load_cogs():
    for extension in INITIAL_EXTENSIONS:
        try:
            await bot.load_extension(extension)
            print(f"'{extension}' をロードしました。")
        except Exception as e:
            print(f"'{extension}' のロードに失敗しました: {e}")

@bot.event
async def on_ready():
    """Botの起動時に実行されるイベント"""
    print(f'{bot.user} (ID: {bot.user.id}) としてログインしました')
    # 定期実行タスクを開始
    send_regular_announcement.start()
    # スラッシュコマンドを同期
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)}個のスラッシュコマンドを同期しました")
    except Exception as e:
        print(f"コマンドの同期に失敗しました: {e}")
    print('------')
    
@bot.command()
@commands.is_owner()
async def sync(ctx: commands.Context):
    """(Botオーナー専用) スラッシュコマンドを手動で同期します"""
    try:
        synced = await bot.tree.sync()
        message = f"✅ {len(synced)}個のスラッシュコマンドをグローバルに同期しました。"
        print(message)
        await ctx.send(message)
    except Exception as e:
        message = f"❌ 同期に失敗しました: {e}"
        print(message)
        await ctx.send(message)

# Botの実行
async def main():
    async with bot:
        await load_cogs()
        token = os.getenv('DISCORD_BOT_TOKEN')
        if token is None:
            print("エラー: DiscordのBotトークンが.envファイルに設定されていません。")
            return
        await bot.start(token)

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Botを終了します。")
