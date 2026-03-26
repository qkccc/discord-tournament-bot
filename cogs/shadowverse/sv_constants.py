# sv_constants.py
import discord

from cogs.utils.constants import DB_FILE
CLASS_NAMES = ["エルフ", "ロイヤル", "ウィッチ", "ドラゴン", "ナイトメア", "ビショップ", "ネメシス"]
TURN_ORDERS = ["先攻", "後攻", "不明"]
RESULTS = ["WIN", "LOSE"]
TARGET_CATEGORY_ID = 1003574017900417094 # 通知先として選択できるチャンネルが含まれるカテゴリID

CLASS_EMOJI_MAP = {
    "エルフ": (922142168473301082, "Class_Forestcraft"),
    "ロイヤル": (922142203323744296, "Class_Swordcraft"),
    "ウィッチ": (922142232595791953, "Class_Runecraft"),
    "ドラゴン": (922142264942284830, "Class_Dragoncraft"),
    "ナイトメア": (1410146572930519040, "Class_Abysscraft"),
    "ビショップ": (922142398073700433, "Class_Havencraft"),
    "ネメシス": (922142424380346399, "Class_Portalcraft")
}

def get_class_emoji(class_name: str) -> discord.PartialEmoji | None:
    """クラス名に対応するPartialEmojiを取得します。"""
    emoji_info = CLASS_EMOJI_MAP.get(class_name)
    if emoji_info:
        return discord.PartialEmoji(name=emoji_info[1], id=emoji_info[0])
    return None
