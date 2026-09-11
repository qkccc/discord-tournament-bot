# cogs/shadowverse/stats_publisher.py
"""
sv_matches の集計 JSON を1日1回生成し、サイトのリポジトリ（apeiron-site）へ
git commit / push する Cog。サイト側は data/sv-stats.json を静的に読むだけ。

必要な環境変数（.env）:
  APEIRON_SITE_PATH        … ホスト上の apeiron-site クローンの絶対パス（必須）
  APEIRON_SITE_BRANCH      … push 先ブランチ（省略時 main）
  APEIRON_SITE_GIT_TOKEN   … push 用の GitHub トークン（省略時は git 認証設定に委ねる）
  SV_STATS_COMMIT_NAME     … コミット者名（省略時 apeiron-bot）
  SV_STATS_COMMIT_EMAIL    … コミット者メール（省略時 noreply）

未設定でもボットは正常起動し、公開処理だけスキップされる（安全に段階導入可）。
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import os
from pathlib import Path

from discord.ext import commands, tasks

from cogs.utils.db_handler import db
from .sv_stats_agg import build_payload, JST

logger = logging.getLogger(__name__)

PUBLISH_TIME = datetime.time(hour=5, minute=15, tzinfo=JST)
REL_JSON_PATH = "data/sv-stats.json"
GIT_TIMEOUT = 120


class SVStatsPublisher(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.site_path = os.getenv("APEIRON_SITE_PATH", "").strip()
        self.branch = os.getenv("APEIRON_SITE_BRANCH", "main").strip() or "main"
        self.git_token = os.getenv("APEIRON_SITE_GIT_TOKEN", "").strip()
        self.commit_name = os.getenv("SV_STATS_COMMIT_NAME", "apeiron-bot").strip() or "apeiron-bot"
        self.commit_email = (
            os.getenv("SV_STATS_COMMIT_EMAIL", "").strip()
            or "apeiron-bot@users.noreply.github.com"
        )
        # 「今環境」の開始日を引く guild（未設定なら sv_guild_settings の先頭行）
        self.guild_id = os.getenv("APEIRON_GUILD_ID", "").strip()
        # 定期実行と手動コマンドの多重実行を防ぐ
        self._lock = asyncio.Lock()
        self.daily_publish.start()

    def cog_unload(self):
        self.daily_publish.cancel()

    # ------------------------------------------------------------------ #
    # 定期実行
    # ------------------------------------------------------------------ #
    @tasks.loop(time=PUBLISH_TIME)
    async def daily_publish(self):
        ok, msg = await self._run(reason="scheduled")
        (logger.info if ok else logger.warning)("[sv-stats] %s", msg)

    @daily_publish.before_loop
    async def _before_daily_publish(self):
        await self.bot.wait_until_ready()

    # ------------------------------------------------------------------ #
    # 本体
    # ------------------------------------------------------------------ #
    async def _season_start_date(self) -> str | None:
        """/season_start で設定した「今環境」開始日（YYYY-MM-DD）を返す。"""
        try:
            if self.guild_id.isdigit():
                row = await db.fetchone(
                    "SELECT season_start_date FROM sv_guild_settings WHERE guild_id = ?",
                    (int(self.guild_id),),
                )
            else:
                row = await db.fetchone(
                    "SELECT season_start_date FROM sv_guild_settings "
                    "WHERE season_start_date IS NOT NULL AND season_start_date != '' "
                    "ORDER BY rowid LIMIT 1"
                )
        except Exception:  # noqa: BLE001
            logger.warning("[sv-stats] season_start_date の取得に失敗（既定値を使用）")
            return None
        return row["season_start_date"] if row and row["season_start_date"] else None

    async def _build_payload(self) -> dict:
        rows = await db.fetchall("SELECT * FROM sv_matches")
        season_start = await self._season_start_date()
        row_dicts = [dict(r) for r in rows]
        # 集計は CPU バウンドなので、イベントループをブロックしないようスレッドへ逃がす
        return await asyncio.to_thread(
            build_payload, row_dicts, season_start_date=season_start
        )

    async def _run(self, reason: str, dry_run: bool = False) -> tuple[bool, str]:
        async with self._lock:
            return await self._run_locked(reason, dry_run)

    async def _run_locked(self, reason: str, dry_run: bool) -> tuple[bool, str]:
        if not dry_run and not self.site_path:
            return False, "APEIRON_SITE_PATH 未設定のため公開をスキップしました。"

        try:
            payload = await self._build_payload()
        except Exception as e:  # noqa: BLE001
            logger.exception("[sv-stats] 集計に失敗")
            return False, f"集計に失敗しました: {e}"

        w = payload["windows"]
        summary = " / ".join(
            f"{k}:{w[k]['total_games']}戦" for k in ("7d", "season", "all") if k in w
        )
        total_records = payload["meta"]["total_records"]

        if dry_run:
            return True, (
                f"ドライラン成功（{summary} ／ 累計 {total_records} 件）。commit/push は行っていません。"
            )

        site = Path(self.site_path).expanduser()
        if not site.is_dir():
            return False, f"APEIRON_SITE_PATH がディレクトリではありません: {site}"
        if not (site / ".git").exists():
            return False, f"git リポジトリではありません: {site}"

        json_path = site / REL_JSON_PATH
        json_path.parent.mkdir(parents=True, exist_ok=True)

        # 最新化（失敗しても続行）
        rc, _, err = await self._git(
            site, "pull", "--rebase", "--autostash", "origin", self.branch, check=False
        )
        if rc != 0:
            logger.warning("[sv-stats] git pull 警告: %s", err.strip()[:300])

        json_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        rc, out, _ = await self._git(
            site, "status", "--porcelain", "--", REL_JSON_PATH, check=False
        )
        if not out.strip():
            return True, f"内容に変更なし（{summary}）。commit はしていません。"

        try:
            await self._git(site, "add", "--", REL_JSON_PATH)
            await self._git(
                site,
                "-c",
                f"user.name={self.commit_name}",
                "-c",
                f"user.email={self.commit_email}",
                "commit",
                "-m",
                f"chore: update sv-stats.json (auto, {reason})",
            )
        except RuntimeError as e:
            return False, str(e)

        push_target = ["origin"]
        if self.git_token:
            rc, origin_url, _ = await self._git(
                site, "remote", "get-url", "origin", check=False
            )
            push_target = [self._inject_token(origin_url.strip())]

        rc, _, err = await self._git(
            site, "push", *push_target, f"HEAD:{self.branch}", check=False, redact=self.git_token
        )
        if rc != 0:
            # ローカルのコミットを捨てて origin と揃える（次回に持ち越さない）
            await self._git(
                site, "reset", "--hard", f"origin/{self.branch}", check=False
            )
            return False, (
                f"push に失敗しました（ローカルの変更は破棄しました）: {err.strip()[:400]}"
            )

        return True, f"サイトへ公開しました（{summary} ／ 累計 {total_records} 件）。"

    def _inject_token(self, url: str) -> str:
        if url.startswith("https://"):
            rest = url[len("https://") :]
            host_part = rest.split("/", 1)[0]
            if "@" in host_part:
                rest = rest.split("@", 1)[1]
            return f"https://x-access-token:{self.git_token}@{rest}"
        return url

    async def _git(
        self, cwd: Path, *args: str, check: bool = True, redact: str | None = None
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(cwd),
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out_b, err_b = await asyncio.wait_for(
                proc.communicate(), timeout=GIT_TIMEOUT
            )
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"git {args[0]} がタイムアウトしました")

        out = out_b.decode("utf-8", "replace")
        err = err_b.decode("utf-8", "replace")
        if redact:
            out = out.replace(redact, "***")
            err = err.replace(redact, "***")
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} に失敗: {err.strip()}")
        return proc.returncode, out, err

    # ------------------------------------------------------------------ #
    # 手動コマンド
    # ------------------------------------------------------------------ #
    @commands.command(name="svstats公開")
    @commands.has_permissions(manage_guild=True)
    async def cmd_publish(self, ctx: commands.Context):
        """sv-stats.json を今すぐ生成してサイトへ push（管理者用）。"""
        async with ctx.typing():
            ok, msg = await self._run(reason="manual")
        await ctx.send(("✅ " if ok else "❌ ") + msg)

    @commands.command(name="svstats確認")
    @commands.has_permissions(manage_guild=True)
    async def cmd_dryrun(self, ctx: commands.Context):
        """集計だけ実行して件数を確認（push しない・管理者用）。"""
        async with ctx.typing():
            ok, msg = await self._run(reason="dryrun", dry_run=True)
        await ctx.send(("✅ " if ok else "❌ ") + msg)


async def setup(bot: commands.Bot):
    await bot.add_cog(SVStatsPublisher(bot))
