"""
大会コマンド用の共通ユーティリティ
デバッグモード時の出力チャンネル制御など
"""
import discord
from typing import Optional


class DebugOutputController:
    """デバッグモード時の出力先を管理するクラス"""

    def __init__(self):
        # ギルドID -> デバッグモード有効フラグ
        self.debug_mode: dict[int, bool] = {}
        # ギルドID -> デバッグコマンド実行チャンネル
        self.debug_channel: dict[int, int] = {}

    def enable_debug(self, guild_id: int, channel: discord.TextChannel):
        """デバッグモードを有効化し、チャンネルを記録"""
        self.debug_mode[guild_id] = True
        self.debug_channel[guild_id] = channel.id

    def disable_debug(self, guild_id: int):
        """デバッグモードを無効化"""
        self.debug_mode[guild_id] = False
        self.debug_channel.pop(guild_id, None)

    def is_debug_enabled(self, guild_id: int) -> bool:
        """デバッグモードが有効か確認"""
        return self.debug_mode.get(guild_id, False)

    def get_debug_channel_id(self, guild_id: int) -> Optional[int]:
        """デバッグチャンネルを取得"""
        return self.debug_channel.get(guild_id) if self.is_debug_enabled(guild_id) else None

    async def get_output_channel(
        self, bot: discord.Client, guild_id: int, default_channel: discord.TextChannel
    ) -> discord.TextChannel:
        """出力先チャンネルを取得（デバッグモードなら指定チャンネル、そうでなければデフォルト）"""
        if not self.is_debug_enabled(guild_id):
            return default_channel

        debug_ch_id = self.get_debug_channel_id(guild_id)
        if debug_ch_id:
            guild = bot.get_guild(guild_id)
            if guild:
                channel = guild.get_channel(debug_ch_id)
                if channel:
                    return channel

        return default_channel


# グローバルインスタンス
debug_controller = DebugOutputController()
