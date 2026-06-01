# apeiron_bot.py
import discord
from discord.ext import commands, tasks
import asyncio
import datetime
import zoneinfo
import os
from dotenv import load_dotenv
import logging
import logging.handlers # 追加: ログローテーション用

# .envファイルから環境変数を読み込む
load_dotenv()

# --- ロギング設定 (新規追加) ---
def setup_logging():
    # ルートロガーの設定
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    # フォーマット（日時 - ファイル名 - レベル - メッセージ）
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

    # 1. ファイル出力設定 (bot.log)
    # 5MBごとに新しいファイルを作成し、最大3つまでバックアップを残す
    file_handler = logging.handlers.RotatingFileHandler(
        filename='bot.log',
        encoding='utf-8',
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 2. コンソール出力設定 (画面にも表示)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    # 外部ライブラリのログレベル調整 (うるさいログを抑制)
    logging.getLogger('discord').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    
    # yomitokuライブラリのログ抑制
    yomi_logger = logging.getLogger('yomitoku')
    yomi_logger.setLevel(logging.CRITICAL + 1)
    yomi_logger.propagate = False

# ログ設定を実行
setup_logging()
# -----------------------------

# Botの初期設定
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.reactions = True
intents.guilds = True
intents.voice_states = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# --- Cogを自動ロードする関数 ---
async def load_cogs():
    """cogsフォルダ以下の拡張機能を自動的に探してロードします"""
    cogs_dir = './cogs'
    logger = logging.getLogger('cogs')
    logger.info("--- Cogのロードを開始します ---")
    
    # cogsフォルダ内を再帰的に探索 (サブフォルダも全て見る)
    for root, dirs, files in os.walk(cogs_dir):
        # __pycache__ などの不要なフォルダは除外
        if '__' in root:
            continue
            
        for filename in files:
            # .pyファイルのみ対象、_で始まるファイル（__init__.pyなど）は除外
            if filename.endswith('.py') and not filename.startswith('_'):
                # ファイルパスを作成 (例: ./cogs/audio/music.py)
                file_path = os.path.join(root, filename)
                
                # モジュールパス形式に変換 (例: cogs.audio.music)
                module_name = os.path.relpath(file_path, '.').replace(os.sep, '.')[:-3]
                
                try:
                    await bot.load_extension(module_name)
                    logger.info(f"✅ '{module_name}' をロードしました。")
                except commands.NoEntryPointError:
                    # setup関数がないファイル（ユーティリティなど）はスキップ
                    logger.debug(f"スキップ: エントリポイントなし {module_name}")
                except Exception as e:
                    logger.error(f"⚠️ '{module_name}' のロードに失敗しました: {e}", exc_info=True)
    
    logger.info("--- ロード完了 ---")

    # フォールバック: event_manager が何らかの理由でロードされていない場合、明示的にロードを試みる
    try:
        if 'cogs.events.event_manager.cog' not in bot.extensions:
            await bot.load_extension('cogs.events.event_manager.cog')
            logger.info("フォールバックで 'cogs.events.event_manager.cog' をロードしました。")
    except Exception as e:
        logger.error("フォールバックで event_manager のロードに失敗しました", exc_info=True)

# --- 定期実行タスク ---
TARGET_CHANNEL_ID = 941626417345593345
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
    embed = discord.Embed(title="🏆 Botコマンド一覧", description="このBotで利用できるコマンドの一覧です。", color=0x00aaff)
    embed.add_field(name="⚔️ 大会・チーム分け機能", value="**`!募集`** : イベント参加者の募集を開始します。\n参加者は👍リアクションで参加し、ボタンでイベントを開始・操作します。\n**詳しくは `!ヘルプ3` を参照してください。**", inline=False)
    embed.add_field(name="その他", value="**`!ヘルプ`** : このヘルプメッセージを表示します。\n**`!ヘルプ2`** : 音楽機能のヘルプを表示します。\n**`!ヘルプ3`** : 大会・チーム分け機能の詳細ヘルプを表示します。\n**`!ヘルプ4`** : Shadowverse戦績管理機能のヘルプを表示します。\n**`!ヘルプ5`** : チーム総当たり戦機能のヘルプを表示します。\n**`@Bot名 [メッセージ]`**: Botと会話します（Gemini）", inline=False)
    embed.set_footer(text="Shadowverse関連のコマンドはスラッシュ(/)で入力します。")
    await ctx.send(embed=embed)

@bot.command(name='ヘルプ2')
async def help2(ctx):
    embed = discord.Embed(title="🎵 音楽機能コマンド一覧", description="YouTubeの音楽を再生するためのコマンドです。", color=0x3498db)
    embed.add_field(name="基本操作", value="**`!通話`**: Botをボイスチャンネルに呼び出します。\n**`!再生 [曲名 or URL]`**: 曲を検索して再生リストに追加します。\n**`!退出`**: Botをボイスチャンネルから退出させます。", inline=False)
    embed.add_field(name="再生コントロール", value="**`!一時停止`**, **`!再開`**, **`!スキップ`**, **`!停止`**", inline=False)
    embed.add_field(name="再生リスト管理", value="**`!一覧`**: 現在の再生リストを表示します。", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='ヘルプ4')
async def help4(ctx):
    embed = discord.Embed(title="⚔️ Shadowverse戦績管理ヘルプ", description="戦績管理機能で利用できるコマンドやボタン操作の一覧です。", color=discord.Color.purple())
    embed.add_field(name="【推奨】パネルからの操作", value="指定されたチャンネルにあるパネルのボタンから、直感的にほとんどの機能を利用できます。\n・**手動登録**: 1戦ずつ戦績を記録します。\n・**戦績表示**: 戦績サマリーを表示します。\n・**直近履歴**: 最新の対戦履歴を表示します。\n・**通知チャンネル設定**: 表示先チャンネルを変更します。\n・**全データ削除**: データを削除します。", inline=False)
    embed.add_field(name="スラッシュコマンド", value="・**/replay [image]**: リプレイ画像から一括登録します。\n・**/record**, **/stats**, **/history** も利用可能です。", inline=False)
    embed.add_field(name="テキストコマンド", value="**`!panel`**: 操作パネルを再設置します。", inline=False)
    await ctx.send(embed=embed)

@bot.command(name='ヘルプ5')
async def help5(ctx):
    embed = discord.Embed(title="⚔️ チーム総当たり戦 ヘルプ", description="チームを組んで総当たり戦を行うための機能です。", color=discord.Color.blue())
    embed.add_field(name="【基本】パネルからの操作", value="管理者が `!総当たりパネル` で設置したパネルから操作します。\n・**チーム作成**: チームを作りリーダーになります。\n・**チームに参加/脱退**: チームへの出入りを行います。", inline=False)
    embed.add_field(name="コマンド管理", value="**`!チーム登録`**, **`!チームメンバー追加`**, **`!チーム取消`**", inline=False)
    embed.add_field(name="大会進行", value="**`!総当たり開始`**, **`!次節`**, **`!順位`**, **`!総当たり中止`**", inline=False)
    await ctx.send(embed=embed)

# --- Botのメイン処理 ---
@bot.event
async def on_ready():
    print(f'{bot.user} (ID: {bot.user.id}) としてログインしました')
    send_regular_announcement.start()
    try:
        synced = await bot.tree.sync()
        print(f"{len(synced)}個のスラッシュコマンドを同期しました")
    except Exception as e:
        print(f"コマンドの同期に失敗しました: {e}")
    # 起動時にプレフィックスコマンドの一覧をログ出力（デバッグ用）
    try:
        cmd_names = [c.name for c in bot.commands]
        logging.getLogger('cogs').info(f"登録されたテキストコマンド: {cmd_names}")
        # 代表例としてエイリアスも表示
        alias_map = {c.name: c.aliases for c in bot.commands if c.aliases}
        if alias_map:
            logging.getLogger('cogs').info(f"コマンドのエイリアス: {alias_map}")
    except Exception:
        pass
    print('------')


@bot.event
async def on_message(message: discord.Message):
    # デバッグ: プレフィックスで始まるメッセージのみログに出力してコマンド処理を続ける
    if message.author.bot:
        return
    try:
        if isinstance(message.content, str) and message.content.startswith('!'):
            logging.getLogger('cogs').info(f"受信メッセージ: author={message.author} channel={getattr(message.channel, 'id', None)} content={message.content!r}")
    except Exception:
        pass
    # コマンド処理を続行
    await bot.process_commands(message)
    
@bot.command()
@commands.is_owner()
async def sync(ctx: commands.Context):
    try:
        synced = await bot.tree.sync()
        await ctx.send(f"✅ {len(synced)}個のスラッシュコマンドを同期しました。")
    except Exception as e:
        await ctx.send(f"❌ 同期に失敗しました: {e}")

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