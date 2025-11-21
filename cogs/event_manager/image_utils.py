# cogs/tournament/image_utils.py
import discord
import math
from PIL import Image, ImageDraw, ImageFont
import io
from typing import Dict, Tuple
from .database import DatabaseManager

def create_bracket_image_from_db(guild_id: int, db: DatabaseManager) -> discord.File:
    """DBから情報を取得し、トーナメント表画像を生成する（勝者色変更対応版）"""
    tournament_info = db.fetchone("SELECT * FROM se_tournaments WHERE guild_id = ?", (guild_id,))
    if not tournament_info: return None
    
    matches_data = db.fetchall("SELECT * FROM se_matches WHERE guild_id = ? ORDER BY round_num, match_in_round", (guild_id,))
    players_data = db.fetchall("SELECT user_id, display_name FROM players WHERE guild_id = ?", (guild_id,))
    
    player_map = {p["user_id"]: p["display_name"] for p in players_data}
    player_map[None] = " "

    padding, box_w, box_h, h_gap, v_gap = 60, 200, 50, 100, 40
    font_size, line_w = 20, 3
    font_color, line_color, bg_color = (0, 0, 0), (180, 180, 180), (255, 255, 255)
    
    # ▼▼▼ 修正点 ▼▼▼
    # ラウンドごとの勝者の色を定義
    round_winner_colors = [
        (20, 140, 30),   # Round 1: Green
        (30, 100, 200),  # Round 2: Blue
        (200, 120, 30),  # Round 3: Orange/Gold
        (150, 50, 180),  # Round 4: Purple
        (210, 50, 50),   # Round 5: Red
    ]
    # ▲▲▲ 修正ここまで ▲▲▲

    try:
        font = ImageFont.truetype("meiryo.ttc", font_size)
    except IOError:
        font = ImageFont.load_default()

    num_rounds = tournament_info["num_rounds"]
    tourney_size = 2 ** num_rounds

    img_w = padding * 2 + box_w * (num_rounds + 1) + h_gap * num_rounds
    img_h = padding * 2 + tourney_size * box_h + v_gap * (tourney_size - 1)
    img = Image.new('RGB', (int(img_w), int(img_h)), bg_color)
    draw = ImageDraw.Draw(img)

    match_centers: Dict[str, Tuple[float, float]] = {}
    slot_y_increment = box_h + v_gap
    base_slot_centers_y = [padding + (i + 0.5) * slot_y_increment for i in range(tourney_size)]

    for match in matches_data:
        r_idx = match["round_num"] - 1
        winner_box_center_x = padding + box_w + h_gap + r_idx * (box_w + h_gap) + (box_w / 2)
        
        # ▼▼▼ 修正点 ▼▼▼
        # 現在のラウンドに対応する色を取得（色リストの数を超えたらループする）
        winner_font_color = round_winner_colors[r_idx % len(round_winner_colors)]
        # ▲▲▲ 修正ここまで ▲▲▲
        
        if r_idx == 0:
            m_idx = match["match_in_round"]
            player_box_center_x = padding + box_w / 2
            p1_center_y = base_slot_centers_y[m_idx * 2]; p2_center_y = base_slot_centers_y[m_idx * 2 + 1]
            winner_box_center_y = (p1_center_y + p2_center_y) / 2
            match_centers[match['match_id']] = (winner_box_center_x, winner_box_center_y)
            
            p1_name = player_map.get(match["player1_id"], " "); p2_name = player_map.get(match["player2_id"], " ")
            is_p1_winner = match["winner_id"] == match["player1_id"]; is_p2_winner = match["winner_id"] == match["player2_id"]
            if match["is_bye"]:
                if match["player1_id"] is None:
                    p1_name = "(不戦勝)"
                else: # player2_id is None
                    p2_name = "(不戦勝)"
            
            draw.rectangle((player_box_center_x - box_w/2, p1_center_y - box_h/2, player_box_center_x + box_w/2, p1_center_y + box_h/2), outline=line_color, width=line_w)
            draw.text((player_box_center_x - box_w/2 + 10, p1_center_y - box_h/2 + 10), p1_name, font=font, fill=winner_font_color if is_p1_winner else font_color)
            draw.rectangle((player_box_center_x - box_w/2, p2_center_y - box_h/2, player_box_center_x + box_w/2, p2_center_y + box_h/2), outline=line_color, width=line_w)
            draw.text((player_box_center_x - box_w/2 + 10, p2_center_y - box_h/2 + 10), p2_name, font=font, fill=winner_font_color if is_p2_winner else font_color)
        else:
            p1_source_center = match_centers[match["player1_source_match_id"]]
            p2_source_center = match_centers[match["player2_source_match_id"]]
            winner_box_center_y = (p1_source_center[1] + p2_source_center[1]) / 2
            match_centers[match['match_id']] = (winner_box_center_x, winner_box_center_y)

        winner_name = player_map.get(match["winner_id"], " ")
        draw.rectangle((winner_box_center_x - box_w/2, winner_box_center_y - box_h/2, winner_box_center_x + box_w/2, winner_box_center_y + box_h/2), outline=line_color, width=line_w)
        draw.text((winner_box_center_x - box_w/2 + 10, winner_box_center_y - box_h/2 + 10), winner_name, font=font, fill=winner_font_color)

    # STEP 2: 全ての線を繋ぐ
    for r_idx in range(num_rounds):
        round_matches = [m for m in matches_data if m["round_num"] == r_idx + 1]
        for match in round_matches:
            target_center = match_centers[match['match_id']]

            if r_idx == 0: # 1回戦 -> 1回戦勝者ボックスへの線
                m_idx = match['match_in_round']
                p1_center_y = base_slot_centers_y[m_idx * 2]
                p2_center_y = base_slot_centers_y[m_idx * 2 + 1]
                source_x_right = padding + box_w
                mid_x = source_x_right + h_gap / 2
                draw.line([(source_x_right, p1_center_y), (mid_x, p1_center_y)], fill=line_color, width=line_w)
                draw.line([(source_x_right, p2_center_y), (mid_x, p2_center_y)], fill=line_color, width=line_w)
                draw.line([(mid_x, p1_center_y), (mid_x, p2_center_y)], fill=line_color, width=line_w)
                draw.line([(mid_x, target_center[1]), (target_center[0] - box_w/2, target_center[1])], fill=line_color, width=line_w)
            
            elif r_idx < num_rounds: # 2回戦以降の勝者ボックスへの線
                s1_center = match_centers.get(match['player1_source_match_id'])
                s2_center = match_centers.get(match['player2_source_match_id'])
                if not s1_center or not s2_center: continue
                mid_x = target_center[0] - box_w/2 - h_gap/2
                draw.line([(s1_center[0] + box_w/2, s1_center[1]), (mid_x, s1_center[1])], fill=line_color, width=line_w)
                draw.line([(s2_center[0] + box_w/2, s2_center[1]), (mid_x, s2_center[1])], fill=line_color, width=line_w)
                draw.line([(mid_x, s1_center[1]), (mid_x, s2_center[1])], fill=line_color, width=line_w)
                draw.line([(mid_x, target_center[1]), (target_center[0] - box_w/2, target_center[1])], fill=line_color, width=line_w)
    
    # --- STEP 3: 優勝者表示 (不要なため削除) ---
    # このブロックを削除することで、最後のボックスから右に伸びる線とトロフィーが描画されなくなります。

    # --- 3. ファイルを返却 ---
    buffer = io.BytesIO()
    img.save(buffer, format='PNG')
    buffer.seek(0)
    return discord.File(buffer, filename="tournament_bracket.png")