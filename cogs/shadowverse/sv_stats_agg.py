# cogs/shadowverse/sv_stats_agg.py
"""
sv_matches の生レコードから、サイト掲載用の「チーム全体・匿名」集計 dict を作る。

- 個人を特定できる情報は出力しない（記録者数のみ）。
- 期間境界は /stats と同じ「朝5時 JST」ビジネスデー基準。
- pandas 非依存（sqlite3 の行 dict をそのまま渡せる）。stats_publisher から利用。
"""
from __future__ import annotations

import datetime

CLASS_ORDER = [
    "エルフ",
    "ロイヤル",
    "ウィッチ",
    "ドラゴン",
    "ナイトメア",
    "ビショップ",
    "ネメシス",
]

JST = datetime.timezone(datetime.timedelta(hours=9))
DAY_BOUNDARY_HOUR = 5
# 「今期」の開始境界だけは17時区切り（他の期間は上記DAY_BOUNDARY_HOURの5時区切り）
SEASON_START_HOUR = 17

# ローリング期間ウィンドウ（key -> 「今日を含めて何日さかのぼるか」。all は None）
# ※ "today" は個人特定が容易なため出力しない。"今環境(season)" は build_payload で
#    /season_start の設定値（または既定の26日区切り）から算出する特別ウィンドウ。
WINDOW_DAYS: dict[str, int | None] = {
    "3d": 2,
    "7d": 6,
    "all": None,
}

# 掲載する順番（season は特別扱いで build_payload が挿入）
WINDOW_KEYS = ["3d", "7d", "season", "all"]

# archetype メタ表の採用条件
ARCHETYPE_MIN_GAMES = 3
ARCHETYPE_TOP_N = 15
# 直近トレンドの日数
DAILY_TREND_DAYS = 30
# 記録者がこの人数未満のウィンドウは、個人特定を避けるため詳細集計を出さない
MIN_RECORDERS_FOR_DETAIL = 3

_MATCH_TIME_FORMATS = (
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d %H:%M:%S",
)


def parse_match_time(value) -> datetime.datetime | None:
    """match_time 文字列を JST aware datetime に変換。失敗時 None。"""
    if value is None:
        return None
    if isinstance(value, datetime.datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        dt = None
        for fmt in _MATCH_TIME_FORMATS:
            try:
                dt = datetime.datetime.strptime(text, fmt)
                break
            except ValueError:
                continue
        if dt is None:
            try:
                dt = datetime.datetime.fromisoformat(text)
            except ValueError:
                return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def business_date(dt: datetime.datetime) -> datetime.date:
    """その時刻が属するビジネスデー（朝5時始まり）の日付。"""
    return (dt - datetime.timedelta(hours=DAY_BOUNDARY_HOUR)).date()


def _business_start(d: datetime.date) -> datetime.datetime:
    return datetime.datetime.combine(
        d, datetime.time(DAY_BOUNDARY_HOUR, 0, tzinfo=JST)
    )


def _season_business_start(d: datetime.date) -> datetime.datetime:
    """「今期」の開始境界（設定日のSEASON_START_HOUR時）を返す。"""
    return datetime.datetime.combine(
        d, datetime.time(SEASON_START_HOUR, 0, tzinfo=JST)
    )


def window_range(
    window: str, now: datetime.datetime
) -> tuple[datetime.datetime | None, datetime.datetime]:
    """ローリング window の [start, end) を返す。all の start は None。"""
    today_bd = business_date(now)
    end = _business_start(today_bd + datetime.timedelta(days=1))
    days = WINDOW_DAYS[window]
    if days is None:
        return None, end
    start = _business_start(today_bd - datetime.timedelta(days=days))
    return start, end


def resolve_default_season_start(base_date: datetime.date) -> datetime.date:
    """/season_start 未設定時の既定「26日区切り」ルール（sv_utils と同じ）。"""
    if base_date.day >= 26:
        return base_date.replace(day=26)
    if base_date.month == 1:
        return datetime.date(base_date.year - 1, 12, 26)
    return datetime.date(base_date.year, base_date.month - 1, 26)


def _parse_season_start_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _rate(wins: int, games: int) -> float | None:
    if games <= 0:
        return None
    return round(wins / games * 100, 1)


def _normalize_rows(rows) -> list[dict]:
    """必要カラムを取り出し、match_time を datetime 化。無効行は捨てる。"""
    out: list[dict] = []
    for r in rows:
        d = dict(r)
        dt = parse_match_time(d.get("match_time"))
        if dt is None:
            continue
        result = str(d.get("result") or "").strip().upper()
        if result not in ("WIN", "LOSE"):
            continue
        out.append(
            {
                "user_id": d.get("user_id"),
                "dt": dt,
                "my_class": (str(d.get("my_class")).strip() if d.get("my_class") else ""),
                "opponent_class": (
                    str(d.get("opponent_class")).strip()
                    if d.get("opponent_class")
                    else ""
                ),
                "result": result,
                "turn_order": (
                    str(d.get("turn_order")).strip() if d.get("turn_order") else "不明"
                ),
                "my_archetype": (
                    str(d.get("my_archetype")).strip() if d.get("my_archetype") else ""
                ),
                "opponent_archetype": (
                    str(d.get("opponent_archetype")).strip()
                    if d.get("opponent_archetype")
                    else ""
                ),
            }
        )
    return out


def aggregate(rows: list[dict]) -> dict:
    """正規化済み行（window で絞り込み済み）を集計して匿名 dict を返す。"""
    total = len(rows)
    wins = sum(1 for r in rows if r["result"] == "WIN")
    losses = total - wins

    # 先攻 / 後攻
    by_turn: dict[str, dict] = {}
    for order in ("先攻", "後攻"):
        sub = [r for r in rows if r["turn_order"] == order]
        if sub:
            w = sum(1 for r in sub if r["result"] == "WIN")
            by_turn[order] = {"games": len(sub), "wins": w, "win_rate": _rate(w, len(sub))}

    # 自クラス別（7クラス固定・0埋め）
    by_class = []
    for cls in CLASS_ORDER:
        sub = [r for r in rows if r["my_class"] == cls]
        w = sum(1 for r in sub if r["result"] == "WIN")
        by_class.append(
            {
                "class": cls,
                "games": len(sub),
                "wins": w,
                "win_rate": _rate(w, len(sub)),
                "usage_rate": round(len(sub) / total * 100, 1) if total else 0.0,
            }
        )

    # 相手クラス別
    vs_class = []
    for cls in CLASS_ORDER:
        sub = [r for r in rows if r["opponent_class"] == cls]
        w = sum(1 for r in sub if r["result"] == "WIN")
        vs_class.append(
            {
                "class": cls,
                "games": len(sub),
                "wins": w,
                "win_rate": _rate(w, len(sub)),
            }
        )

    # 対面マトリクス（自 × 相手、games>0 のセルのみ）
    matchup: dict[str, dict] = {}
    for my in CLASS_ORDER:
        row: dict[str, dict] = {}
        my_rows = [r for r in rows if r["my_class"] == my]
        for opp in CLASS_ORDER:
            cell = [r for r in my_rows if r["opponent_class"] == opp]
            if not cell:
                continue
            w = sum(1 for r in cell if r["result"] == "WIN")
            row[opp] = {"games": len(cell), "wins": w, "win_rate": _rate(w, len(cell))}
        if row:
            matchup[my] = row

    # 相手アーキタイプ環境（"財宝ロイヤル" のように class を付与）
    arche_counter: dict[str, dict] = {}
    for r in rows:
        arche = r["opponent_archetype"]
        if not arche:
            continue
        label = f"{arche}{r['opponent_class']}".strip()
        slot = arche_counter.setdefault(label, {"games": 0, "wins": 0})
        slot["games"] += 1
        if r["result"] == "WIN":
            slot["wins"] += 1
    archetype_meta = [
        {
            "label": label,
            "games": v["games"],
            "wins": v["wins"],
            "win_rate": _rate(v["wins"], v["games"]),
        }
        for label, v in arche_counter.items()
        if v["games"] >= ARCHETYPE_MIN_GAMES
    ]
    archetype_meta.sort(key=lambda x: (-x["games"], x["label"]))
    archetype_meta = archetype_meta[:ARCHETYPE_TOP_N]

    return {
        "total_games": total,
        "wins": wins,
        "losses": losses,
        "win_rate": _rate(wins, total),
        "recorder_count": len({r["user_id"] for r in rows}),
        "by_turn": by_turn,
        "by_class": by_class,
        "vs_class": vs_class,
        "matchup": matchup,
        "archetype_meta": archetype_meta,
    }


def _daily_trend(rows: list[dict], now: datetime.datetime) -> list[dict]:
    """日別の戦績数推移。記録者数が少ない日は個人特定を避けるため詳細を伏せる。"""
    today_bd = business_date(now)
    start_bd = today_bd - datetime.timedelta(days=DAILY_TREND_DAYS - 1)
    buckets: dict[datetime.date, dict] = {}
    for r in rows:
        bd = business_date(r["dt"])
        if bd < start_bd or bd > today_bd:
            continue
        slot = buckets.setdefault(bd, {"games": 0, "wins": 0, "recorders": set()})
        slot["games"] += 1
        if r["result"] == "WIN":
            slot["wins"] += 1
        slot["recorders"].add(r["user_id"])
    out = []
    for i in range(DAILY_TREND_DAYS):
        bd = start_bd + datetime.timedelta(days=i)
        slot = buckets.get(bd, {"games": 0, "wins": 0, "recorders": set()})
        recorder_count = len(slot["recorders"])
        suppressed = 0 < slot["games"] and recorder_count < MIN_RECORDERS_FOR_DETAIL
        out.append(
            {
                "date": bd.isoformat(),
                "games": slot["games"] if not suppressed else None,
                "wins": slot["wins"] if not suppressed else None,
                "win_rate": _rate(slot["wins"], slot["games"]) if not suppressed else None,
                "recorder_count": recorder_count,
                "detail_suppressed": suppressed,
            }
        )
    return out


_DETAIL_KEYS = ("by_turn", "by_class", "vs_class", "matchup", "archetype_meta")


def build_payload(
    rows,
    now: datetime.datetime | None = None,
    season_start_date: str | None = None,
) -> dict:
    """
    sv_matches 全行（dict の反復可能物）から掲載用 payload を生成。

    rows の各要素は少なくとも user_id, match_time, my_class, opponent_class,
    result, turn_order を持つ。my_archetype / opponent_archetype は任意。

    season_start_date: "YYYY-MM-DD"。/season_start の設定値。None/不正なら
    既定の「26日区切り」ルールで算出する。
    """
    if now is None:
        now = datetime.datetime.now(JST)
    else:
        now = now.astimezone(JST)

    norm = _normalize_rows(rows)
    norm.sort(key=lambda r: r["dt"])

    today_bd = business_date(now)
    end = _business_start(today_bd + datetime.timedelta(days=1))

    # 「今環境」の開始日を解決
    season_start = _parse_season_start_date(season_start_date)
    season_is_default = season_start is None
    if season_start is None:
        season_start = resolve_default_season_start(today_bd)
    season_start_dt = _season_business_start(season_start)

    # (key, start) のリスト。start=None は全期間。
    s3, _ = window_range("3d", now)
    s7, _ = window_range("7d", now)
    specs: list[tuple[str, datetime.datetime | None]] = [
        ("3d", s3),
        ("7d", s7),
        ("season", season_start_dt),
        ("all", None),
    ]

    windows: dict[str, dict] = {}
    for key, start in specs:
        sub = [
            r
            for r in norm
            if r["dt"] < end and (start is None or r["dt"] >= start)
        ]
        agg = aggregate(sub)
        agg["range"] = {
            "start": start.isoformat() if start is not None else None,
            "end": end.isoformat(),
        }
        # 記録者が少ないウィンドウは詳細を伏せる（全体成績のみ残す）
        if 0 < agg["total_games"] and agg["recorder_count"] < MIN_RECORDERS_FOR_DETAIL:
            for k in _DETAIL_KEYS:
                agg.pop(k, None)
            agg["detail_suppressed"] = True
        else:
            agg["detail_suppressed"] = False
        windows[key] = agg

    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "meta": {
            "class_order": CLASS_ORDER,
            "day_boundary_hour": DAY_BOUNDARY_HOUR,
            "windows": WINDOW_KEYS,
            "total_records": len(norm),
            "season_start": season_start.isoformat(),
            "season_start_is_default": season_is_default,
            "min_recorders_for_detail": MIN_RECORDERS_FOR_DETAIL,
            "note": "チーム全体の匿名集計。個人名は含みません。",
        },
        "windows": windows,
        "daily": _daily_trend(norm, now),
    }
