# sv_utils.py
import discord
import pandas as pd
import datetime
import cv2
import re
import io
from yomitoku import DocumentAnalyzer
from discord import Embed, Color, PartialEmoji, SelectOption
from PIL import Image, ImageDraw, ImageFont

from .sv_constants import DB_FILE, CLASS_NAMES, CLASS_EMOJI_MAP, RESULTS, TURN_ORDERS
from .sv_db import get_records_as_df
import sqlite3
import os

JST = datetime.timezone(datetime.timedelta(hours=9))


def _to_jst_datetime(series: pd.Series) -> pd.Series:
    parsed = pd.to_datetime(series, format="mixed")
    if getattr(parsed.dt, "tz", None) is None:
        return parsed.dt.tz_localize(JST)
    return parsed.dt.tz_convert(JST)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = [
        "assets/meiryob.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "assets/meiryo.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc"
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size, index=0)
        except IOError:
            continue
    return ImageFont.load_default()


def _text_bbox(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int, int, int]:
    return draw.textbbox((0, 0), text, font=font)


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    bbox = _text_bbox(draw, text, font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def _draw_class_icon(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    class_name: str,
    font,
    base_image: Image.Image | None = None,
) -> None:
    """カスタム絵文字が利用できない場合の代替描画。
    クラス色の丸を描き、その中に頭文字を描画する。
    """
    class_colors = {
        "エルフ": (72, 201, 176),
        "ロイヤル": (241, 196, 15),
        "ウィッチ": (142, 68, 173),
        "ドラゴン": (231, 111, 81),
        "ナイトメア": (122, 92, 255),
        "ビショップ": (255, 196, 61),
        "ネメシス": (57, 181, 163),
    }
    color = class_colors.get(class_name, (200, 200, 200))

    # try load icon image from repo `icon/` directory
    icon_dir = os.path.normpath(
        os.path.join(os.path.dirname(__file__), "..", "..", "icon")
    )
    filename_map = {
        "エルフ": "class_E.png",
        "ロイヤル": "class_R.png",
        "ウィッチ": "class_W.png",
        "ドラゴン": "class_D.png",
        "ナイトメア": "class_Ni.png",
        "ビショップ": "class_B.png",
        "ネメシス": "class_Nm.png",
    }
    img_path = None
    try:
        candidate = filename_map.get(class_name)
        if candidate:
            p = os.path.join(icon_dir, candidate)
            if os.path.isfile(p):
                img_path = p
    except Exception:
        img_path = None

    if img_path and os.path.isfile(img_path):
        try:
            with Image.open(img_path) as ic:
                ic = ic.convert("RGBA")
                ic = ic.resize((size, size), Image.Resampling.LANCZOS if hasattr(Image, "Resampling") else Image.LANCZOS)
                img_box = (int(x), int(y))
                if base_image is not None:
                    base_image.paste(ic, img_box, ic)
                    return
        except Exception:
            pass

    # fallback: draw circle
    draw.ellipse((x, y, x + size, y + size), fill=color)
    initial = class_name[0] if class_name else "?"
    try:
        init_font = _load_font(int(size * 0.65))
    except Exception:
        init_font = font
    tw, th = _text_size(draw, initial, init_font)
    tx = x + (size - tw) / 2
    ty = y + (size - th) / 2 - 3.0
    draw.text((tx, ty), initial, font=init_font, fill=(16, 20, 28))


def _fit_font_for_box(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_width: int,
    max_height: int,
    base_size: int,
):
    """与えられた最大幅／高さに収まる最大フォントサイズを返す。"""
    size = base_size
    while size > 8:
        try:
            f = _load_font(size)
        except Exception:
            f = ImageFont.load_default()
        w, h = _text_size(draw, text, f)
        if w <= max_width and h <= max_height:
            return f
        size -= 2
    return _load_font(10)


def _build_stats_context(
    user_id: int,
    period: str = "all",
    class_name: str | None = None,
    season_start_date: str | None = None,
) -> tuple[pd.DataFrame | None, str, str, str | None]:
    user_df = get_records_as_df(user_id)
    class_text = f"{class_name}の" if class_name else ""
    if user_df.empty:
        return None, "あなたの", class_text, "あなたの戦績データはまだありません。"

    user_df["match_time"] = _to_jst_datetime(user_df["match_time"])
    now = datetime.datetime.now(JST)
    today_business_date = (now - datetime.timedelta(hours=5)).date()

    period_text_map = {"today": "今日の", "yesterday": "昨日の", "week": "一週間の"}

    def resolve_default_season_start(base_date: datetime.date) -> datetime.date:
        if base_date.day >= 26:
            return base_date.replace(day=26)
        if base_date.month == 1:
            return datetime.date(base_date.year - 1, 12, 26)
        return datetime.date(base_date.year, base_date.month - 1, 26)

    resolved_season_start_date: datetime.date | None = None
    if period == "season":
        if season_start_date:
            try:
                resolved_season_start_date = datetime.datetime.strptime(
                    season_start_date, "%Y-%m-%d"
                ).date()
            except ValueError:
                resolved_season_start_date = None
        if resolved_season_start_date is None:
            resolved_season_start_date = resolve_default_season_start(
                today_business_date
            )
        period_text = f"今期({resolved_season_start_date.month}/{resolved_season_start_date.day}~)の"
    else:
        period_text = period_text_map.get(period, "あなたの")

    def to_business_start(start_date: datetime.date) -> datetime.datetime:
        return datetime.datetime.combine(start_date, datetime.time(5, 0, tzinfo=JST))

    def to_business_range(
        start_date: datetime.date, end_date: datetime.date
    ) -> tuple[datetime.datetime, datetime.datetime]:
        return (
            to_business_start(start_date),
            to_business_start(end_date),
        )

    if period != "all":
        if period == "today":
            start_time, end_time = to_business_range(
                today_business_date,
                today_business_date + datetime.timedelta(days=1),
            )
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "yesterday":
            yesterday_business_date = today_business_date - datetime.timedelta(days=1)
            start_time, end_time = to_business_range(
                yesterday_business_date, today_business_date
            )
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "week":
            start_time, end_time = to_business_range(
                today_business_date - datetime.timedelta(days=6),
                today_business_date + datetime.timedelta(days=1),
            )
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "season":
            start_base = resolved_season_start_date or resolve_default_season_start(
                today_business_date
            )
            start_time, end_time = to_business_range(
                start_base,
                today_business_date + datetime.timedelta(days=1),
            )
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]

    if user_df.empty:
        return None, period_text, class_text, f"{period_text}戦績データはありません。"

    if class_name:
        user_df = user_df[user_df["my_class"] == class_name]
        if user_df.empty:
            return (
                None,
                period_text,
                class_text,
                f"あなたが{class_name}を使用した{period_text}戦績データはありません。",
            )

    return user_df, period_text, class_text, None


def _format_class_group_summary(df: pd.DataFrame, group_column: str) -> str:
    grouped = df.groupby(group_column, sort=False)["result"].apply(
        lambda x: (
            f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
        )
    )
    ordered_index = [name for name in CLASS_NAMES if name in grouped.index]
    remaining_index = [name for name in grouped.index if name not in ordered_index]
    return grouped.reindex(ordered_index + remaining_index).to_string()


def _draw_card(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    accent: tuple[int, int, int],
    background: tuple[int, int, int],
    title_font,
):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=28, fill=background)
    draw.rounded_rectangle(box, radius=28, outline=accent, width=4)
    title_bbox = _text_bbox(draw, title, title_font)
    title_height = title_bbox[3] - title_bbox[1]
    draw.text((left + 28, top + 20), title, font=title_font, fill=(245, 247, 250))
    draw.line((left + 24, top + 72, right - 24, top + 72), fill=accent, width=3)
    return top + 88


def _draw_progress_row(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    width: int,
    label: str,
    value_text: str,
    rate: float,
    fill_color: tuple[int, int, int],
    label_font,
    value_font,
    bar_height: int = 24,
):
    label_w = 170
    value_w = 150
    bar_w = max(10, width - label_w - value_w - 40)
    draw.text((x, y), label, font=label_font, fill=(240, 244, 248))
    bar_x = x + label_w
    bar_y = y + 6
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_height),
        radius=10,
        fill=(56, 63, 76),
    )
    if rate > 0:
        fill_w = max(4, int(bar_w * min(rate, 1.0)))
        draw.rounded_rectangle(
            (bar_x, bar_y, bar_x + fill_w, bar_y + bar_height),
            radius=10,
            fill=fill_color,
        )
    value_bbox = _text_bbox(draw, value_text, value_font)
    value_h = value_bbox[3] - value_bbox[1]
    draw.text(
        (x + width - value_w, y + 2),
        value_text,
        font=value_font,
        fill=(230, 233, 238),
    )


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    ellipsis = "…"
    while text and _text_size(draw, text + ellipsis, font)[0] > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis
def _draw_table_panel(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    accent: tuple[int, int, int],
    background: tuple[int, int, int],
    title_font,
    header_font,
    cell_font,
    headers: list[str],
    rows: list[list[str]],
    col_widths: list[int],
    row_height: int,
    header_height: int = 52,
    body_top_padding: int = 8,
    row_fill: tuple[int, int, int] = (34, 39, 53),
    alt_fill: tuple[int, int, int] = (38, 44, 59),
    base_image: Image.Image | None = None,
):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=12, fill=background)
    draw.rounded_rectangle(box, radius=12, outline=accent, width=2)
    
    # タイトルが指定されている場合のみ描画
    if title:
        draw.text((left + 18, top + 12), title, font=title_font, fill=(245, 247, 250))
        draw.line((left + 14, top + 42, right - 14, top + 42), fill=accent, width=2)
        grid_top = top + 52
    else:
        # 表題非表示時は、テーブル総高さに基づき上下中央寄せ計算を行う
        total_grid_height = header_height + body_top_padding + (row_height * len(rows)) + 4 * (len(rows) - 1)
        panel_height = bottom - top
        grid_top = top + max(12, (panel_height - total_grid_height) // 2)

    # グリッドの幅と、中央寄せのための開始X座標を枠内で計算する
    total_width = sum(col_widths)
    panel_width = right - left - 28 # 左右のパディング 14px ずつを引いた利用可能幅
    x = left + 14 + (panel_width - total_width) // 2
    y = grid_top

    class_initials = {
        "エルフ": "E", "ロイヤル": "R", "ウィッチ": "W", "ドラゴン": "D", 
        "ナイトメア": "Ni", "ビショップ": "B", "ネメシス": "Nm"
    }
    initial_to_class = {v: k for k, v in class_initials.items()}

    # header
    draw.rounded_rectangle(
        (x, y, x + total_width, y + header_height),
        radius=6,
        fill=(47, 54, 71),
    )
    cur_x = x
    for col_idx, (header, width) in enumerate(zip(headers, col_widths)):
        left_pad = 6
        
        target_class = None
        if header in CLASS_NAMES:
            target_class = header
        elif header in initial_to_class:
            target_class = initial_to_class[header]
            
        icon_size = 30
        icon_spacing = 36
        header_text = _truncate_text(draw, header, header_font, width - (icon_spacing if target_class else 0) - 12)
        w_h = _text_size(draw, header_text, header_font)[0]
        header_h = _text_size(draw, header_text, header_font)[1]
        
        # 勝率列の分離に伴い、マトリクス（対戦相手クラス）列のインデックスは col_idx >= 3 になる
        if col_idx >= 3:
            # マトリクス列は中央寄せ
            content_w = w_h + (icon_spacing if target_class else 0)
            start_x = cur_x + (width - content_w) / 2
            
            if target_class:
                icon_x = start_x
                icon_y = y + (header_height - icon_size) // 2
                try:
                    _draw_class_icon(
                        draw, icon_x, icon_y, icon_size, target_class, header_font, base_image=base_image
                    )
                except Exception:
                    pass
                start_x += icon_spacing
            
            draw.text(
                (start_x, y + (header_height - header_h) / 2 - 4.5), # 縦中央微調整
                header_text,
                font=header_font,
                fill=(190, 198, 210),
            )
        else:
            # クラス・勝敗・勝率列は左寄せ
            if target_class:
                icon_x = cur_x + 6
                icon_y = y + (header_height - icon_size) // 2
                try:
                    _draw_class_icon(
                        draw, icon_x, icon_y, icon_size, target_class, header_font, base_image=base_image
                    )
                except Exception:
                    pass
                left_pad += icon_spacing
                
            draw.text(
                (cur_x + left_pad, y + (header_height - header_h) / 2 - 4.5), # 縦中央微調整
                header_text,
                font=header_font,
                fill=(190, 198, 210),
            )
        cur_x += width

    y += header_height + body_top_padding
    for idx, row in enumerate(rows):
        fill = row_fill if idx % 2 == 0 else alt_fill
        draw.rounded_rectangle(
            (x, y, x + total_width, y + row_height),
            radius=6,
            fill=fill,
        )
        cur_x = x
        for col_idx, (cell, width) in enumerate(zip(row, col_widths)):
            # アイコン描画
            icon_offset = 0
            if col_idx == 0 and isinstance(cell, str) and cell in CLASS_NAMES:
                icon_x = cur_x + 6
                icon_y = y + (row_height - icon_size) // 2
                try:
                    _draw_class_icon(
                        draw, icon_x, icon_y, icon_size, cell, cell_font, base_image=base_image
                    )
                except Exception:
                    pass
                icon_offset = icon_spacing

            text_x = cur_x + 6 + icon_offset
            
            # LEDシグナルの描画 (勝率列 col_idx == 2 に対する混同防止色付け)
            sig_color = None
            cell_str = str(cell)
            if col_idx == 2 and cell_str != "-" and cell_str != "":
                try:
                    rate_val = float(cell_str.replace("%", "").strip())
                    if rate_val > 50.0:
                        sig_color = (52, 152, 219) # 明るいブルー (青と緑を逆転)
                    elif rate_val < 50.0:
                        sig_color = (231, 76, 60) # 明るいレッド
                    else:
                        sig_color = (46, 204, 113) # 明るいグリーン
                except Exception:
                    pass

            if sig_color:
                sig_size = 12
                sig_x = text_x
                sig_y = y + (row_height - sig_size) // 2 - 4.5
                draw.ellipse((sig_x, sig_y, sig_x + sig_size, sig_y + sig_size), fill=sig_color)
                text_x += 20
            
            cell_fill = None
            text_color = (236, 239, 244)

            if cell_str == "-" or "0-0" in cell_str or cell_str == "":
                text_color = (90, 100, 115)
            elif "%" in cell_str and "\n" in cell_str:
                # ユーザー要望に基づき「勝敗数(2-2)が上、勝率(50%)が下」に構造逆転したため、パース処理も逆転
                try:
                    lines = cell_str.split("\n")
                    w_l = lines[0].split("-")
                    total_games = int(w_l[0]) + int(w_l[1])
                    rate_val = float(lines[1].replace("%", "").strip())
                    
                    if total_games > 0:
                        if rate_val > 50.0:
                            cell_fill = (25, 45, 78) # ダークブルー (青と緑を逆転)
                            text_color = (190, 215, 255)
                        elif rate_val < 50.0:
                            cell_fill = (78, 25, 25) # ダークレッド
                            text_color = (255, 180, 180)
                        else:
                            cell_fill = (22, 64, 40) # ダークグリーン
                            text_color = (175, 238, 186)
                except Exception:
                    pass

            if cell_fill:
                draw.rounded_rectangle(
                    (cur_x + 2, y + 2, cur_x + width - 2, y + row_height - 2),
                    radius=4,
                    fill=cell_fill
                )

            cell_text = _truncate_text(
                draw, cell_str, cell_font, width - (text_x - cur_x) - 6
            )
            
            # セル内のテキストを描画
            lines = cell_text.split("\n")
            if len(lines) > 1:
                # 2行ある場合
                h1 = _text_size(draw, lines[0], cell_font)[1]
                h2 = _text_size(draw, lines[1], cell_font)[1]
                line_gap = 4
                total_h = h1 + h2 + line_gap
                start_y = y + (row_height - total_h) / 2 - 4.0 # 縦中央微調整
                
                # 1行目
                w1 = _text_size(draw, lines[0], cell_font)[0]
                if col_idx >= 3: # 勝率列の分離に伴い、col_idx >= 3 に判定更新
                    tx1 = cur_x + (width - w1) / 2
                else:
                    tx1 = text_x
                draw.text((tx1, start_y), lines[0], font=cell_font, fill=text_color)
                
                # 2行目
                w2 = _text_size(draw, lines[1], cell_font)[0]
                if col_idx >= 3: # 勝率列の分離に伴い、col_idx >= 3 に判定更新
                    tx2 = cur_x + (width - w2) / 2
                else:
                    tx2 = text_x
                draw.text((tx2, start_y + h1 + line_gap), lines[1], font=cell_font, fill=text_color)
            else:
                # 1行のみの場合
                cell_bbox = _text_bbox(draw, cell_text, cell_font)
                cell_h = cell_bbox[3] - cell_bbox[1]
                draw.text(
                    (text_x, y + (row_height - cell_h) / 2 - 4.5), # 縦中央微調整
                    cell_text,
                    font=cell_font,
                    fill=text_color,
                )
                
            cur_x += width
        y += row_height + 4


def _format_rate_record(wins: int, total: int) -> str:
    if total <= 0:
        return "-"
    rate = wins / total * 100
    return f"{rate:.1f}% ({wins}勝/{total}戦)"


def _create_gradient_bg_fast(width: int, height: int, color1: tuple[int, int, int], color2: tuple[int, int, int]) -> Image.Image:
    base = Image.new("RGB", (1, height))
    for y in range(height):
        ratio = y / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        base.putpixel((0, y), (r, g, b))
    return base.resize((width, height))


def _draw_donut_chart(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], rate: float, fill_color: tuple[int, int, int], bg_color: tuple[int, int, int], panel_bg_color: tuple[int, int, int]):
    # 1. 全体のドーナツ（グレー背景）
    draw.pieslice(box, start=0, end=360, fill=bg_color)
    # 2. 勝率分のドーナツ（時計の12時位置 = -90度 から開始）
    if rate > 0:
        end_angle = -90 + (360 * (rate / 100))
        draw.pieslice(box, start=-90, end=end_angle, fill=fill_color)
    # 3. 中央のくり抜き
    w = box[2] - box[0]
    inner_radius = (w / 2) * 0.72
    center_x = box[0] + w / 2
    center_y = box[1] + w / 2
    draw.ellipse(
        (center_x - inner_radius, center_y - inner_radius,
         center_x + inner_radius, center_y + inner_radius),
        fill=panel_bg_color
    )


def generate_stats_summary_image(
    user_id: int,
    period: str = "all",
    class_name: str | None = None,
    season_start_date: str | None = None,
) -> discord.File | None:
    user_df, period_text, class_text, empty_reason = _build_stats_context(
        user_id, period, class_name, season_start_date
    )
    if user_df is None:
        return None

    user_df = user_df.copy()
    total_matches = len(user_df)
    win_count = len(user_df[user_df["result"] == "WIN"])
    loss_count = total_matches - win_count
    win_rate = (win_count / total_matches) * 100 if total_matches > 0 else 0.0

    # 1920x1080 にスケールダウン
    canvas_w = 1920
    canvas_h = 1080
    bg_color1 = (20, 26, 38)
    bg_color2 = (12, 15, 22)
    panel_bg = (24, 29, 41)
    panel_bg_alt = (30, 36, 50)
    accent = (65, 105, 225) # ロイヤルブルー
    green = (46, 125, 50)  # マテリアルグリーン

    # ユーザーフィードバック（2倍近い文字サイズ）を受けてフォントサイズを極限まで拡大
    title_font = _load_font(48)
    subtitle_font = _load_font(22)
    main_big_font = _load_font(60)
    panel_title_font = _load_font(26)
    header_font = _load_font(24)
    row_font = _load_font(24)
    small_font = _load_font(20)
    micro_font = _load_font(16)
    
    # 総合サマリー用の大きな太字フォント
    top_info_font = _load_font(32)

    # グラデーション背景を生成
    img = _create_gradient_bg_fast(canvas_w, canvas_h, bg_color1, bg_color2)
    draw = ImageDraw.Draw(img)

    # タイトル (復活、サブタイトルは完全廃止)
    title = f"【 {period_text}{class_text}{'戦績' if period != 'all' else '戦績サマリー'} 】"
    draw.text((40, 15), title, font=title_font, fill=(245, 247, 250))

    # Top summary strip (スリム化された総合サマリー、Y=80から開始)
    top_box_bottom = 195
    top_box = (30, 80, canvas_w - 30, top_box_bottom)
    draw.rounded_rectangle(top_box, radius=12, fill=panel_bg)
    draw.rounded_rectangle(top_box, radius=12, outline=accent, width=2)
    
    # 総合サマリー内のブロック (横長スリム化、Y座標調整)
    donut_size = 80
    donut_box = (60, 80 + (115 - donut_size) // 2, 60 + donut_size, 80 + (115 - donut_size) // 2 + donut_size)
    _draw_donut_chart(draw, donut_box, win_rate, green, (45, 54, 71), panel_bg)
    
    # ドーナツ中央に勝率をテキストで中央寄せ (フォントサイズをサイズ26に拡大)
    rate_text = f"{win_rate:.1f}%" if win_rate > 0 or total_matches > 0 else "-"
    tw, th = _text_size(draw, rate_text, panel_title_font)
    tx = donut_box[0] + (donut_size - tw) / 2
    ty = donut_box[1] + (donut_size - th) / 2 - 4.5
    draw.text((tx, ty), rate_text, font=panel_title_font, fill=(245, 247, 250))

    # 右側のテキスト情報 (横並びで配置してスリム化、Y座標調整、フォントサイズをサイズ32に拡大)
    tx_info = 170
    ty_info = 80 + (115 - 40) // 2 # 縦中央寄せ
    draw.text((tx_info, ty_info), "総合戦績", font=top_info_font, fill=green)
    
    stats_text = f"{win_count}勝 {loss_count}敗 / {total_matches}戦"
    draw.text((tx_info + 180, ty_info + 1), stats_text, font=top_info_font, fill=(245, 247, 250))

    # Main panel (クラス別 ＆ 対クラス成績マトリクス表、開始位置をスライド)
    panel_top = top_box_bottom + 15
    
    # 表示する自分のクラスの決定
    target_my_classes = [class_name] if class_name else CLASS_NAMES
    
    # 英語省略表記マッピング
    class_initials = {
        "エルフ": "E",
        "ロイヤル": "R",
        "ウィッチ": "W",
        "ドラゴン": "D",
        "ナイトメア": "Ni",
        "ビショップ": "B",
        "ネメシス": "Nm"
    }
    
    # ヘッダー (勝率を新規の独立した列に分離し、対戦相手クラス名は英語省略表記 [E, R, W, D, Ni, B, Nm] に統一)
    combined_headers = ["クラス", "勝敗", "勝率"] + [class_initials.get(cn, cn[0]) for cn in CLASS_NAMES]
    combined_rows = []
    
    for my_class in target_my_classes:
        class_df = user_df[user_df["my_class"] == my_class]
        total = len(class_df)
        wins = int((class_df["result"] == "WIN").sum())
        losses = total - wins
        rate_str = f"{wins / total * 100:.1f}%" if total > 0 else "-"
        record_str = f"{wins}勝{losses}敗 / {total}戦" if total > 0 else "-"
        row = [
            my_class,
            record_str,
            rate_str,
        ]
        for opponent_class in CLASS_NAMES:
            matchup_df = class_df[class_df["opponent_class"] == opponent_class]
            t = len(matchup_df)
            w = int((matchup_df["result"] == "WIN").sum())
            if t == 0:
                cell = "-"
            else:
                rate = w / t * 100
                cell = f"{w}-{t - w}\n{rate:.0f}%"
            row.append(cell)
        combined_rows.append(row)

    # 青線枠（パネル）は画面横幅いっぱい (左右余白30px) の 1860px に広げて揃える
    combined_panel = (30, panel_top, canvas_w - 30, canvas_h - 40)
    col_widths = [220, 260, 160] + [160] * len(CLASS_NAMES)
    
    # _draw_table_panel 側で自動的に枠内垂直・水平中央寄せが行われます
    _draw_table_panel(
        draw,
        combined_panel,
        "", # 表題「クラス別 & 対クラス成績」は非表示にして上部を詰める
        accent,
        panel_bg,
        panel_title_font,
        header_font,
        row_font,
        combined_headers,
        combined_rows,
        col_widths,
        row_height=80, # 行の高さ 80px は維持
        base_image=img,
    )

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    filename_suffix = class_name or "all"
    return discord.File(buffer, filename=f"stats-summary-{filename_suffix}.png")


def extract_text_from_image(
    ocr_instance: DocumentAnalyzer, image_path: str
) -> str | None:
    try:
        img = cv2.imread(image_path)
        if img is None:
            return None
        results, _, _ = ocr_instance(img)
        if not results:
            return None
        paragraphs_with_coords = [
            ((p.box[1] + p.box[3]) / 2, p.contents)
            for f in results.figures
            for p in f.paragraphs
        ]
        paragraphs_with_coords.sort(key=lambda item: item[0])
        return "\n".join([text for _, text in paragraphs_with_coords])
    except Exception as e:
        print(f"Yomitoku処理中にエラーが発生しました: {e}")
        return None


def parse_replay_text(text: str) -> list[dict]:
    matches = list(re.finditer(r"\d{4}/\d{2}/\d{2}\s\d{2}:\d{2}", text))
    if not matches:
        return []
    results = []
    for i, current_match in enumerate(matches):
        start_pos = current_match.start()
        end_pos = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        match_segment = text[start_pos:end_pos]
        all_found_classes = [cn for cn in CLASS_NAMES if cn in match_segment]
        unique_classes_in_order = sorted(
            list(set(all_found_classes)), key=lambda x: match_segment.find(x)
        )
        if not unique_classes_in_order:
            continue
        my_class = "不明"
        opponent_class = "不明"
        class_pattern_with_de = f"({'|'.join(CLASS_NAMES)})で"
        anchor_match = re.search(class_pattern_with_de, match_segment)
        if anchor_match:
            my_class = anchor_match.group(1)
            for cls in unique_classes_in_order:
                if cls != my_class:
                    opponent_class = cls
                    break
            if opponent_class == "不明":
                opponent_class = my_class
        else:
            if len(unique_classes_in_order) >= 2:
                my_class = unique_classes_in_order[0]
                opponent_class = unique_classes_in_order[1]
            elif len(unique_classes_in_order) == 1:
                my_class = unique_classes_in_order[0]
                opponent_class = unique_classes_in_order[0]
        if my_class == "不明":
            continue
        results.append(
            {
                "match_time": current_match.group(0),
                "my_class": my_class,
                "opponent_class": opponent_class,
                "result": "WIN" if "WIN" in match_segment else "LOSE",
                "turn_order": "不明",
            }
        )
    return results


def get_stats_summary(
    user_id: int,
    period: str = "all",
    class_name: str | None = None,
    season_start_date: str | None = None,
) -> discord.Embed:
    user_df, period_text, class_text, empty_reason = _build_stats_context(
        user_id, period, class_name, season_start_date
    )
    if user_df is None:
        return discord.Embed(
            description=empty_reason or "あなたの戦績データはまだありません。",
            color=discord.Color.orange(),
        )

    class_text = f"{class_name}の" if class_name else ""
    summary_text = "戦績" if period != "all" else "戦績サマリー"
    embed = discord.Embed(
        title=f"⚔️ {period_text}{class_text}{summary_text} ⚔️", color=discord.Color.gold()
    )

    total_matches = len(user_df)
    win_count = len(user_df[user_df["result"] == "WIN"])
    win_rate = (win_count / total_matches) * 100 if total_matches > 0 else 0
    summary_lines = [
        f"総合: {total_matches}戦 {win_count}勝 {total_matches - win_count}敗 (勝率: {win_rate:.1f}%)"
    ]
    if "turn_order" in user_df.columns:
        for order in ["先攻", "後攻"]:
            order_df = user_df[user_df["turn_order"] == order]
            if not order_df.empty:
                order_wins = (order_df["result"] == "WIN").sum()
                order_total = len(order_df)
                order_rate = (order_wins / order_total) * 100 if order_total > 0 else 0
                summary_lines.append(
                    f"{order}: {order_wins}勝 {order_total - order_wins}敗 (勝率: {order_rate:.1f}%)"
                )
    embed.add_field(
        name="📊 総合戦績",
        value=f"```\n" + "\n".join(summary_lines) + "\n```",
        inline=False,
    )
    if not user_df.empty:
        has_archetype_column = "opponent_archetype" in user_df.columns
        archetype_df = user_df
        if has_archetype_column:
            archetype_df = user_df[
                user_df["opponent_archetype"].notna()
                & (user_df["opponent_archetype"].astype(str).str.strip() != "")
            ]

        if class_name:
            matchup_summary = _format_class_group_summary(user_df, "opponent_class")
            my_class_info = CLASS_EMOJI_MAP.get(class_name)
            my_class_emoji = (
                f"{discord.PartialEmoji(name=my_class_info[1], id=my_class_info[0])} "
                if my_class_info
                else ""
            )
            embed.add_field(
                name=f"{my_class_emoji}対相手クラス成績 ({class_name})",
                value=f"```{matchup_summary}```",
                inline=False,
            )

            if not archetype_df.empty:
                archetype_summary = (
                    archetype_df.groupby("opponent_archetype")["result"]
                    .apply(
                        lambda x: (
                            f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
                        )
                    )
                    .to_string()
                )
                embed.add_field(
                    name=f"{my_class_emoji}相手アーキタイプ別勝率 ({class_name})",
                    value=f"```{archetype_summary}```",
                    inline=False,
                )
        else:
            class_summary = _format_class_group_summary(user_df, "my_class")
            embed.add_field(
                name="自分のクラス別勝率", value=f"```{class_summary}```", inline=False
            )

            if not archetype_df.empty:
                archetype_view_df = archetype_df.copy()
                if "opponent_class" in archetype_view_df.columns:
                    archetype_view_df["opponent_deck_type"] = (
                        archetype_view_df["opponent_archetype"].astype(str).str.strip()
                        + archetype_view_df["opponent_class"].astype(str).str.strip()
                    )
                    archetype_group_key = "opponent_deck_type"
                else:
                    archetype_group_key = "opponent_archetype"

                archetype_summary = (
                    archetype_view_df.groupby(archetype_group_key)["result"]
                    .apply(
                        lambda x: (
                            f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
                        )
                    )
                    .to_string()
                )
                embed.add_field(
                    name="相手アーキタイプ別勝率",
                    value=f"```{archetype_summary}```",
                    inline=False,
                )

            played_classes = [
                name
                for name in CLASS_NAMES
                if name in set(user_df["my_class"].unique())
            ]
            for my_class in played_classes:
                class_df = user_df[user_df["my_class"] == my_class]
                if class_df.empty:
                    continue
                matchup_summary = _format_class_group_summary(
                    class_df, "opponent_class"
                )
                my_class_info = CLASS_EMOJI_MAP.get(my_class)
                my_class_emoji = (
                    f"{discord.PartialEmoji(name=my_class_info[1], id=my_class_info[0])} "
                    if my_class_info
                    else ""
                )
                embed.add_field(
                    name=f"{my_class_emoji}対相手クラス成績 ({my_class})",
                    value=f"```{matchup_summary}```",
                    inline=False,
                )
    return embed


def get_recent_matches(user_id: int, count: int) -> tuple[Embed, list[dict]]:
    """
    直近の戦績データを取得し、Embedとデータの辞書リストを返します。
    """
    conn = sqlite3.connect(DB_FILE)
    try:
        recent_df = pd.read_sql_query(
            "SELECT * FROM sv_matches WHERE user_id = ? ORDER BY match_time DESC LIMIT ?",
            conn,
            params=(user_id, count),
        )
    finally:
        conn.close()

    if recent_df.empty:
        embed = Embed(
            description="あなたの戦績データはまだありません。", color=Color.orange()
        )
        return embed, []

    recent_df["match_time_dt"] = pd.to_datetime(recent_df["match_time"], format="mixed")

    descriptions = []
    for row in recent_df.itertuples():
        result_emoji = "✅" if row.result == "WIN" else "❌"
        my_class_info = CLASS_EMOJI_MAP.get(row.my_class)
        my_class_emoji = (
            f"{PartialEmoji(name=my_class_info[1], id=my_class_info[0])}"
            if my_class_info
            else ""
        )
        opp_class_info = CLASS_EMOJI_MAP.get(row.opponent_class)
        opp_class_emoji = (
            f"{PartialEmoji(name=opp_class_info[1], id=opp_class_info[0])}"
            if opp_class_info
            else ""
        )
        turn_order_text = (
            f"({row.turn_order})"
            if hasattr(row, "turn_order") and row.turn_order != "不明"
            else ""
        )
        archetype_text = (
            f" [{row.my_archetype}]"
            if hasattr(row, "my_archetype") and row.my_archetype
            else ""
        )
        opponent_archetype_text = (
            f" [{row.opponent_archetype}]"
            if hasattr(row, "opponent_archetype") and row.opponent_archetype
            else ""
        )
        match_time_str = row.match_time_dt.strftime("%m/%d %H:%M")
        descriptions.append(
            f"{result_emoji} `{match_time_str}` {my_class_emoji}{archetype_text} vs {opp_class_emoji} **{row.opponent_class}**{opponent_archetype_text} {turn_order_text}"
        )

    embed = Embed(
        title=f"直近の戦績 ({len(recent_df)}件)",
        description="\n".join(descriptions),
        color=Color.blue(),
    )
    records_list = recent_df.drop(columns=["match_time_dt"]).to_dict("records")

    return embed, records_list


def get_help_embed():
    embed = Embed(
        title="⚔️ シャドウバース戦績管理ヘルプ",
        description="戦績管理機能で利用できるコマンドやボタン操作の一覧です。",
        color=Color.purple(),
    )
    embed.add_field(
        name="【推奨】パネルからの操作",
        value=(
            "このパネルのボタンから、直感的にほとんどの機能を利用できます。\n・**手動登録**: ボタン操作で1戦ずつ戦績を記録します。\n・**戦績表示**: 期間とクラスを指定して、詳細な戦績サマリーを表示します。\n・**直近履歴**: 記録した最新の対戦履歴を表示し、個別削除も可能です。\n・**通知チャンネル設定**: 戦績の表示先チャンネルを設定・変更します。\n・**全データ削除**: あなたの全データを削除します（要確認）。"
        ),
        inline=False,
    )
    embed.add_field(
        name="コマンドでの操作",
        value=(
            "パネル操作に加えて、以下のコマンドも利用可能です。\n・**/replay [image]**: リプレイのスクリーンショットから戦績を一括登録します。\n・**/record**: ボタン操作と同じ手動登録を開始します。\n・**/stats [period] [class_name]**: 期間とクラスを指定して戦績サマリーを表示します。\n・**/history [count]**: 直近の対戦履歴を指定した件数表示し、個別削除も可能です。\n・**!panel**: このパネルを再設置する際に使用します。"
        ),
        inline=False,
    )
    return embed
