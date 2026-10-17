# cogs/utils/image_style.py
"""
戦績画像(sv_utils.py)で使っているダークテーマのPIL共通部品を切り出したモジュール。
戦績機能以外(リーグ戦の順位画像など)からも同じ見た目を再利用できるようにする。

このファイルの関数は sv_utils.py から移動しただけで、挙動は変更していない。
"""
from PIL import Image, ImageDraw, ImageFont

# --- ダークテーマのカラーパレット（戦績画像と統一） ---
BG_GRADIENT_TOP = (20, 26, 38)
BG_GRADIENT_BOTTOM = (12, 15, 22)
PANEL_BG = (24, 29, 41)
PANEL_BG_ALT = (30, 36, 50)
ACCENT_BLUE = (65, 105, 225)  # ロイヤルブルー
ACCENT_GREEN = (46, 125, 50)  # マテリアルグリーン
ROW_FILL = (34, 39, 53)
ALT_FILL = (38, 44, 59)
TEXT_NEAR_WHITE = (245, 247, 250)


def _load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = [
        "assets/meiryob.ttc",
        "C:/Windows/Fonts/meiryob.ttc",
        "assets/meiryo.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
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


def _truncate_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    if _text_size(draw, text, font)[0] <= max_width:
        return text
    ellipsis = "…"
    while text and _text_size(draw, text + ellipsis, font)[0] > max_width:
        text = text[:-1]
    return text + ellipsis if text else ellipsis


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


def _create_gradient_bg_fast(
    width: int, height: int, color1: tuple[int, int, int], color2: tuple[int, int, int]
) -> Image.Image:
    base = Image.new("RGB", (1, height))
    for y in range(height):
        ratio = y / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        base.putpixel((0, y), (r, g, b))
    return base.resize((width, height))
