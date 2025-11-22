# cogs/audio/music.py
import asyncio
import discord
from discord.ext import commands
import yt_dlp
import logging
import os
from dotenv import load_dotenv

# .envファイルから環境変数を読み込む
load_dotenv()

# ロギング（記録）の設定
logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')


class MusicCog(commands.Cog):
    """YouTubeからの音楽再生を管理するCog"""

    def __init__(self, bot):
        self.bot = bot
        self.music_queue = []
        self.is_playing = False
        self.current_song = None
        self.voice_client = None
        # .envファイルからFFmpegのパスを読み込む。見つからない場合は'ffmpeg'をデフォルト値とする。
        self.ffmpeg_path = os.getenv('FFMPEG_PATH', 'ffmpeg')
        
        # .envからCookieファイルのパスを読み込む（任意）
        self.cookie_path = os.getenv('YTDL_COOKIE_PATH')

        self.YDL_OPTIONS = {
            'format': 'bestaudio[ext=opus]/bestaudio/best',
            'noplaylist': True,
            'quiet': True,
            'default_search': 'auto',
            'source_address': '0.0.0.0',
            # Cookieファイルがあれば設定に追加
            'cookiefile': self.cookie_path if self.cookie_path and os.path.exists(self.cookie_path) else None
        }
        
        # Noneの項目は削除しておく
        if self.YDL_OPTIONS['cookiefile'] is None:
            del self.YDL_OPTIONS['cookiefile']

        self.FFMPEG_OPTIONS = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn'
        }

    async def play_next_song(self, ctx):
        """キュー内の次の曲を再生します。"""
        if self.music_queue:
            self.is_playing = True
            video_info = self.music_queue.pop(0)
            self.current_song = video_info

            try:
                # FFmpegの実行ファイルのパスを指定して音声ソースを作成
                audio_source = discord.FFmpegPCMAudio(
                    video_info['url'],
                    executable=self.ffmpeg_path,
                    before_options=self.FFMPEG_OPTIONS['before_options'],
                    options=self.FFMPEG_OPTIONS['options']
                )
                self.voice_client.play(audio_source, after=lambda e: self.handle_after_play(e, ctx))
                await ctx.send(f"**再生中:** {video_info['title']}")
            except Exception as e:
                logging.error(f"音声ソースの作成に失敗: {e}")
                await ctx.send(f"音声の再生準備に失敗しました。Botのログを確認してください。")
                self.is_playing = False
                await self.play_next_song(ctx)
        else:
            self.is_playing = False
            self.current_song = None
            await asyncio.sleep(180) # 3分間待機
            if not self.is_playing and self.voice_client and self.voice_client.is_connected():
                await self.voice_client.disconnect()
                await ctx.send("再生する曲がないためボイスチャンネルから切断しました。")

    def handle_after_play(self, error, ctx):
        """曲の再生が終了した後に呼び出される関数"""
        if error:
            logging.error(f'再生エラーが発生しました: {error}')
            coro = ctx.send(f"再生中にエラーが発生しました: ```{error}```")
            asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
        
        # 次の曲の再生を試みる
        asyncio.run_coroutine_threadsafe(self.play_next_song(ctx), self.bot.loop)

    @commands.command(name='join', aliases=['通話'], help='Botがボイスチャンネルに参加します。')
    async def join(self, ctx):
        if not ctx.author.voice:
            return await ctx.send("このコマンドを使用するには、ボイスチャンネルに参加している必要があります。")
        channel = ctx.author.voice.channel
        if self.voice_client is not None:
            await self.voice_client.move_to(channel)
        else:
            self.voice_client = await channel.connect()
        await ctx.send(f"**{channel.name}** に接続しました。")

    # --- 情報取得処理（URL対応版） ---
    def _extract_info_sync(self, query):
        with yt_dlp.YoutubeDL(self.YDL_OPTIONS) as ydl:
            # URLかどうかの簡易チェック
            if query.startswith("http://") or query.startswith("https://"):
                # URLならそのまま渡す
                return ydl.extract_info(query, download=False)
            else:
                # 文字列ならYouTube検索を行う
                return ydl.extract_info(f"ytsearch:{query}", download=False)

    @commands.command(name='play', aliases=['p', '再生'], help='YouTubeで曲を検索し、キューに追加します。')
    async def play(self, ctx, *, search: str):
        if not self.voice_client:
            if ctx.author.voice:
                self.voice_client = await ctx.author.voice.channel.connect()
            else:
                return await ctx.send("音楽を再生するには、ボイスチャンネルに参加している必要があります。")
        
        # URLか検索ワードかでメッセージを変える
        if search.startswith("http"):
            await ctx.send(f"URLを読み込んでいます...")
        else:
            await ctx.send(f"`{search}` を検索しています...")
        
        try:
            # 非同期で情報を取得
            info = await asyncio.to_thread(self._extract_info_sync, search)
            
            video = None
            
            # 【重要】URL直接指定と検索結果で返り値の構造が違う場合があるため分岐
            if 'entries' in info:
                # 検索結果またはプレイリストの場合
                if not info['entries']:
                    return await ctx.send("動画が見つかりませんでした。")
                video = info['entries'][0] # 先頭の動画を取得
            else:
                # 直接の動画情報の場合
                video = info
            
            video_info = { 'url': video['url'], 'title': video['title'], 'requester': ctx.author }
            self.music_queue.append(video_info)
            
            await ctx.send(f"**キューに追加しました:** {video_info['title']}")
            
            if not self.is_playing:
                await self.play_next_song(ctx)
                
        except Exception as e:
            logging.error(f"再生コマンドでエラーが発生しました: {e}")
            if "Sign in" in str(e):
                 await ctx.send("⚠️ この動画は年齢制限などで再生できませんでした。")
            else:
                 await ctx.send("曲の再生中にエラーが発生しました。")
    
    @commands.command(name='pause', aliases=['一時停止'], help='現在再生中の曲を一時停止します。')
    async def pause(self, ctx):
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            await ctx.send("再生を一時停止しました。")
        else:
            await ctx.send("現在、再生中の曲はありません。")

    @commands.command(name='resume', aliases=['再開'], help='一時停止中の曲を再開します。')
    async def resume(self, ctx):
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            await ctx.send("再生を再開しました。")
        else:
            await ctx.send("一時停止中の曲はありません。")

    @commands.command(name='stop', aliases=['停止'], help='音楽を停止し、キューを空にします。')
    async def stop(self, ctx):
        if self.voice_client:
            self.music_queue.clear()
            if self.voice_client.is_playing() or self.voice_client.is_paused():
                self.voice_client.stop()
                await ctx.send("再生を停止し、キューをクリアしました。")
            else:
                await ctx.send("現在、再生中の曲はありません。")
        else:
            await ctx.send("Botはボイスチャンネルに参加していません。")

    @commands.command(name='skip', aliases=['s', 'スキップ'], help='現在再生中の曲をスキップします。')
    async def skip(self, ctx):
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.stop()
            await ctx.send("現在の曲をスキップしました。")
        else:
            await ctx.send("スキップする曲がありません。")
            
    @commands.command(name='queue', aliases=['q', '一覧'], help='現在のキュー（再生リスト）を表示します。')
    async def queue(self, ctx):
        if not self.music_queue and not self.current_song:
            return await ctx.send("キューは空です。")
        embed = discord.Embed(title="再生リスト", color=discord.Color.blue())
        if self.current_song:
            embed.add_field(name="再生中", value=f"**{self.current_song['title']}** (リクエスト: {self.current_song['requester'].mention})", inline=False)
        if self.music_queue:
            queue_list = ""
            for i, song in enumerate(self.music_queue[:10]):
                queue_list += f"{i+1}. **{song['title']}** (リクエスト: {song['requester'].mention})\n"
            embed.add_field(name="次の曲", value=queue_list, inline=False)
        if len(self.music_queue) > 10:
            embed.set_footer(text=f"...さらに{len(self.music_queue) - 10}曲")
        await ctx.send(embed=embed)

    @commands.command(name='leave', aliases=['退出'], help='Botがボイスチャンネルから退出します。')
    async def leave(self, ctx):
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect()
            self.music_queue.clear()
            self.is_playing = False
            self.current_song = None
            self.voice_client = None
            await ctx.send("ボイスチャンネルから切断しました。")
        else:
            await ctx.send("Botはボイスチャンネルに参加していません。")


async def setup(bot):
    await bot.add_cog(MusicCog(bot))