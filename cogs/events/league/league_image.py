# cogs/events/league/league_image.py
"""個人戦リーグ戦(総当たり、複数グループ)の順位画像を生成する。

見た目は戦績機能(cogs/shadowverse/sv_utils.py)と統一するため、
共通のダークテーマ部品(cogs/utils/image_style.py)を利用する。
テーブル自体はクラスアイコンや勝率の色分けが不要な単純な構成のため、
sv_utils.py の _draw_table_panel は使わず、ここで軽量な描画関数を持つ。
"""
import io
import math
from typing import TYPE_CHECKING, Optional

import discord
from PIL import ImageDraw

from cogs.utils.image_style import (
    ACCENT_BLUE,
    BG_GRADIENT_BOTTOM,
    BG_GRADIENT_TOP,
    PANEL_BG,
    TEXT_NEAR_WHITE,
    _create_gradient_bg_fast,
    _draw_card,
    _load_font,
    _text_size,
    _truncate_text,
)
from cogs.utils.ranking import rank_entries

if TYPE_CHECKING:
    from .cog import LeagueCog

RANK1_HIGHLIGHT = (58, 74, 46)  # 1位の行だけ強調する金/緑寄りの色

CANVAS_W = 1920
PANEL_MARGIN = 32
COLUMNS_MAX = 3


def _draw_group_table(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    group_name: str,
    standings: list[dict],
    title_font,
    header_font,
    cell_font,
):
    """1グループ分の順位テーブルパネルを描画する。standingsは rank_entries 済みのリスト。"""
    content_top = _draw_card(draw, box, group_name, ACCENT_BLUE, PANEL_BG, title_font)
    left, top, right, bottom = box

    col_widths = [70, 260, 60, 60]
    headers = ["順位", "名前", "勝", "敗"]
    row_height = 40
    x = left + 24
    y = content_top

    header_h = _text_size(draw, "順位", header_font)[1]
    cur_x = x
    for header, width in zip(headers, col_widths):
        draw.text((cur_x, y), header, font=header_font, fill=(190, 198, 210))
        cur_x += width
    y += header_h + 10

    for i, entry in enumerate(standings):
        fill = RANK1_HIGHLIGHT if i == 0 else ((34, 39, 53) if i % 2 == 0 else (38, 44, 59))
        draw.rounded_rectangle(
            (x - 6, y, x + sum(col_widths) - 6, y + row_height), radius=6, fill=fill
        )
        cur_x = x
        cells = [str(i + 1), entry["display_name"], str(entry["wins"]), str(entry["losses"])]
        for cell, width in zip(cells, col_widths):
            cell_text = _truncate_text(draw, cell, cell_font, width - 10)
            cell_h = _text_size(draw, cell_text, cell_font)[1]
            draw.text(
                (cur_x, y + (row_height - cell_h) / 2),
                cell_text,
                font=cell_font,
                fill=TEXT_NEAR_WHITE,
            )
            cur_x += width
        y += row_height + 4


def generate_league_standings_image(
    guild_id: int, cog: "LeagueCog"
) -> Optional[discord.File]:
    """現在のリーグ戦(guild_id)の順位画像を生成する。進行中のリーグ戦が無ければNoneを返す。"""
    tournament = cog.db.fetchone(
        "SELECT 1 FROM league_tournaments WHERE guild_id = ? AND is_active = 1", (guild_id,)
    )
    if not tournament:
        return None

    groups = cog.db.fetchall(
        "SELECT group_id, group_index, group_name FROM league_groups WHERE guild_id = ? ORDER BY group_index",
        (guild_id,),
    )
    if not groups:
        return None

    title_font = _load_font(48)
    panel_title_font = _load_font(26)
    header_font = _load_font(20)
    cell_font = _load_font(20)

    num_groups = len(groups)
    columns = min(COLUMNS_MAX, num_groups)
    rows_count = math.ceil(num_groups / columns)

    max_group_size = cog.db.fetchone(
        "SELECT COUNT(*) as c FROM league_players WHERE guild_id = ? "
        "GROUP BY group_id ORDER BY c DESC LIMIT 1",
        (guild_id,),
    )
    max_group_size = max_group_size["c"] if max_group_size else 1

    panel_w = (CANVAS_W - PANEL_MARGIN * (columns + 1)) // columns
    # カード枠(88) + ヘッダー行(約40) + 行数*(行高40+間隔4) + 余白
    panel_h = 88 + 50 + max_group_size * 44 + 20
    top_offset = 140
    canvas_h = top_offset + rows_count * (panel_h + PANEL_MARGIN) + PANEL_MARGIN

    bg = _create_gradient_bg_fast(CANVAS_W, canvas_h, BG_GRADIENT_TOP, BG_GRADIENT_BOTTOM)
    draw = ImageDraw.Draw(bg)
    draw.text((PANEL_MARGIN, 40), "🏆 リーグ戦 順位表", font=title_font, fill=TEXT_NEAR_WHITE)

    for idx, group in enumerate(groups):
        players = cog.db.fetchall(
            "SELECT user_id, display_name, wins, losses FROM league_players WHERE group_id = ?",
            (group["group_id"],),
        )
        matches = cog.db.fetchall(
            "SELECT player1_id, player2_id, winner_id FROM league_matches "
            "WHERE group_id = ? AND status = 'confirmed'",
            (group["group_id"],),
        )
        entries = [
            {
                "user_id": p["user_id"],
                "display_name": p["display_name"],
                "wins": p["wins"],
                "losses": p["losses"],
            }
            for p in players
        ]
        standings = rank_entries(
            entries,
            [dict(m) for m in matches],
            id_key="user_id",
            wins_key="wins",
            opponent1_key="player1_id",
            opponent2_key="player2_id",
            winner_key="winner_id",
        )

        col = idx % columns
        row = idx // columns
        left = PANEL_MARGIN + col * (panel_w + PANEL_MARGIN)
        top = top_offset + row * (panel_h + PANEL_MARGIN)
        box = (left, top, left + panel_w, top + panel_h)
        _draw_group_table(
            draw, box, group["group_name"], standings, panel_title_font, header_font, cell_font
        )

    buffer = io.BytesIO()
    bg.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return discord.File(buffer, filename="league_standings.png")
