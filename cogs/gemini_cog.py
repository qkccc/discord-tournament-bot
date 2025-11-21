import os
import asyncio
import discord
from discord.ext import commands
import google.generativeai as genai
import sqlite3
import json
import re  # 変更点: 正規表現ライブラリをインポート

# --- 定数の定義 ---
# Gemini APIキーを設定
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

# 変更点: AIへのシステムレベルの指示を定義
SYSTEM_INSTRUCTION = "重要: あなたの回答は、簡潔にまとめてください。"

# 変更点: モデルの初期化時にシステム指示を設定
model = genai.GenerativeModel(
    'gemini-2.5-flash',
    system_instruction=SYSTEM_INSTRUCTION
)

# 会話履歴を保持する上限数を設定
MAX_HISTORY = 10 

# データベースファイルのパスを定数として定義
DB_PATH = 'gemini_history.db'

class GeminiCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self._init_db()

    # --- データベース操作用のメソッド ---

    def _init_db(self):
        """データベースを初期化し、履歴保存用のテーブルを作成する。"""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS chat_history (
                    channel_id INTEGER PRIMARY KEY,
                    history TEXT NOT NULL
                )
            ''')
            conn.commit()
        #print("GeminiCog: データベース接続とテーブルの準備が完了しました。")

    def _serialize_history(self, history: list) -> str:
        """会話履歴オブジェクトをJSON文字列に変換する。"""
        serializable = [
            {'role': content.role, 'parts': [part.text for part in content.parts]}
            for content in history
        ]
        return json.dumps(serializable)

    def _deserialize_history(self, json_str: str) -> list:
        """JSON文字列から会話履歴リストを復元する。"""
        if not json_str:
            return []
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return []

    def _load_history(self, channel_id: int) -> list:
        """データベースから指定されたチャンネルの会話履歴を読み込む。"""
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT history FROM chat_history WHERE channel_id = ?", (channel_id,))
            result = cursor.fetchone()
            if result:
                return self._deserialize_history(result[0])
        return []

    def _save_history(self, channel_id: int, history: list):
        """指定されたチャンネルの会話履歴をデータベースに保存する。"""
        serialized_history = self._serialize_history(history)
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO chat_history (channel_id, history)
                VALUES (?, ?)
            ''', (channel_id, serialized_history))
            conn.commit()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author == self.bot.user or message.mention_everyone or not self.bot.user.mentioned_in(message):
            return

        # 変更点: 正規表現を使ってメンションをより確実に除去
        user_prompt = re.sub(f'^<@!?{self.bot.user.id}>\\s*', '', message.content).strip()
        
        # メンション削除後にメッセージが空になった場合は無視
        if not user_prompt:
            return

        channel_id = message.channel.id
        
        history_data = self._load_history(channel_id)
        chat = model.start_chat(history=history_data)
        
        if len(chat.history) > MAX_HISTORY:
            chat.history = chat.history[-MAX_HISTORY:]
            #print(f"チャンネル {channel_id} の会話履歴を最新{MAX_HISTORY // 2}往復に制限しました。")

        # 変更点: `system_instruction` を利用するため、プロンプトの加工は不要
        prompt = user_prompt
        
        try:
            #print(f"プロンプト: {prompt}")
            
            response_stream = await chat.send_message_async(prompt, stream=True)
            
            buffer = ""
            current_content = ""
            sent_message = None
            last_send_time = asyncio.get_event_loop().time()

            async for chunk in response_stream:
                buffer += chunk.text
                current_time = asyncio.get_event_loop().time()

                if len(buffer) > 200 or (current_time - last_send_time > 1.0 and buffer):
                    new_content = current_content + buffer
                    if not sent_message:
                        sent_message = await message.reply(new_content)
                    else:
                        await sent_message.edit(content=new_content)
                    
                    current_content = new_content
                    buffer = ""
                    last_send_time = current_time

            if buffer:
                final_content = current_content + buffer
                if not sent_message:
                    await message.reply(final_content)
                else:
                    await sent_message.edit(content=final_content)

            #print("ストリーミング返信完了。")

            self._save_history(channel_id, chat.history)
            #print(f"チャンネル {channel_id} の会話履歴をデータベースに保存しました。")

        except Exception as e:
            print(f"エラーが発生しました: {e}")
            await message.reply(f"エラーが発生しました: {e}")

async def setup(bot):
    await bot.add_cog(GeminiCog(bot))