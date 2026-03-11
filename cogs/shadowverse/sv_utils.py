# sv_utils.py
import discord
import pandas as pd
import datetime
import cv2
import re
from yomitoku import DocumentAnalyzer
from discord import Embed, Color, PartialEmoji, SelectOption

from .sv_constants import DB_FILE, CLASS_NAMES, CLASS_EMOJI_MAP, RESULTS, TURN_ORDERS
from .sv_db import get_records_as_df
import sqlite3


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
    user_df = get_records_as_df(user_id)
    if user_df.empty:
        return discord.Embed(
            description="あなたの戦績データはまだありません。",
            color=discord.Color.orange(),
        )

    user_df["match_time"] = pd.to_datetime(user_df["match_time"], format="mixed")
    now = datetime.datetime.now()
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

    if period != "all":
        if period == "today":
            start_time = datetime.datetime.combine(
                today_business_date, datetime.time(5, 0)
            )
            end_time = start_time + datetime.timedelta(days=1)
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "yesterday":
            yesterday_business_date = today_business_date - datetime.timedelta(days=1)
            start_time = datetime.datetime.combine(
                yesterday_business_date, datetime.time(5, 0)
            )
            end_time = start_time + datetime.timedelta(days=1)
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "week":
            start_time = datetime.datetime.combine(
                today_business_date - datetime.timedelta(days=6), datetime.time(5, 0)
            )
            end_time = datetime.datetime.combine(
                today_business_date, datetime.time(5, 0)
            ) + datetime.timedelta(days=1)
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]
        elif period == "season":
            start_base = resolved_season_start_date or resolve_default_season_start(
                today_business_date
            )
            start_time = datetime.datetime.combine(start_base, datetime.time(5, 0))
            end_time = datetime.datetime.combine(
                today_business_date, datetime.time(5, 0)
            ) + datetime.timedelta(days=1)
            user_df = user_df[
                (user_df["match_time"] >= start_time)
                & (user_df["match_time"] < end_time)
            ]

    if user_df.empty:
        return discord.Embed(
            description=f"{period_text}戦績データはありません。",
            color=discord.Color.orange(),
        )

    if class_name:
        user_df = user_df[user_df["my_class"] == class_name]
        if user_df.empty:
            return discord.Embed(
                description=f"あなたが{class_name}を使用した{period_text}戦績データはありません。",
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
            matchup_summary = (
                user_df.groupby("opponent_class")["result"]
                .apply(
                    lambda x: (
                        f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
                    )
                )
                .to_string()
            )
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
            class_summary = (
                user_df.groupby("my_class")["result"]
                .apply(
                    lambda x: (
                        f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
                    )
                )
                .to_string()
            )
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

            played_classes = sorted(user_df["my_class"].unique())
            for my_class in played_classes:
                class_df = user_df[user_df["my_class"] == my_class]
                if class_df.empty:
                    continue
                matchup_summary = (
                    class_df.groupby("opponent_class")["result"]
                    .apply(
                        lambda x: (
                            f"{(x == 'WIN').sum() / len(x) * 100:.1f}% ({(x == 'WIN').sum()}勝 / {len(x)}戦)"
                        )
                    )
                    .to_string()
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
        # 修正: FROM matches -> FROM sv_matches
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
