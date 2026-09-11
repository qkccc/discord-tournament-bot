# SVチーム統計のサイト公開（sv-stats.json パイプライン）

`sv_matches`（各メンバーの Shadowverse 対戦記録）を **チーム全体・匿名**で集計し、
1日1回 `apeiron-site` リポジトリの `data/sv-stats.json` を更新 → Vercel 自動デプロイ →
サイトの `sv-stats.html` が表示する仕組み。

```
sv_matches (main.db)
      │  cogs/shadowverse/sv_stats_agg.py  … 期間フィルタ＋匿名集計
      ▼
cogs/shadowverse/stats_publisher.py         … 1日1回 & 手動コマンド
      │  git commit / push
      ▼
apeiron-site/data/sv-stats.json             … 静的JSON（Vercelがそのまま配信）
      ▼
apeiron-site/sv-stats.html + sv-stats.js    … 期間タブで集計を表示
```

- 集計は個人名を一切含まない（記録者数のみ）。
- 記録者が **3名未満**のウィンドウは、個人特定を避けるため詳細（クラス別・対面など）を伏せ、全体の勝敗だけ出す（`sv_stats_agg.MIN_RECORDERS_FOR_DETAIL`）。
- 期間境界は `/stats` と同じ **朝5時 JST**（ビジネスデー）基準。
- 期間ウィンドウ: `3d` / `7d` / `season`（今環境）/ `all` ＋ 直近30日の日別トレンド。
  - `season` の開始日は `/season_start` の設定値。未設定なら「26日区切り」で自動判定。
  - `today` は個人特定が容易なため出力しない。

---

## サーバー側セットアップ（Proxmox ホスト）

### 1. apeiron-site のクローンを用意

ボットが push できる専用クローンをホストに置く（ボット本体とは別ディレクトリ）。

```bash
cd /root
git clone https://github.com/qkccc/apeiron-site.git
cd apeiron-site
git config user.name  "apeiron-bot"
git config user.email "apeiron-bot@users.noreply.github.com"
```

このクローンでは `data/sv-stats.json` 以外を手で編集しないこと（毎回 `git pull --rebase` される）。

### 2. GitHub トークンを発行

- Fine-grained PAT を推奨。対象リポジトリ = `qkccc/apeiron-site`、権限 = **Contents: Read and write** のみ。
- 発行したトークンを `.env` に設定（下記）。
- （代替）Deploy Key（SSH）や `git credential` を先に設定済みなら `APEIRON_SITE_GIT_TOKEN` は不要。

### 3. `.env` に追記

```dotenv
# --- SVチーム統計 サイト公開 ---
APEIRON_SITE_PATH=/root/apeiron-site
APEIRON_SITE_BRANCH=main
# Deploy Key 方式なら不要。HTTPS + PAT で push する場合のみ設定
# APEIRON_SITE_GIT_TOKEN=github_pat_xxxxxxxxxxxxxxxxxxxx
# 省略可（既定値あり）
# APEIRON_GUILD_ID=123456789012345678   # 「今環境」開始日を引く guild。未設定なら sv_guild_settings の先頭行
# SV_STATS_COMMIT_NAME=apeiron-bot
# SV_STATS_COMMIT_EMAIL=apeiron-bot@users.noreply.github.com
```

`APEIRON_SITE_PATH` 未設定でもボットは正常起動し、公開処理だけスキップされる。
ボットが複数サーバーに入っている場合は `APEIRON_GUILD_ID` を設定すること。

### 4. 反映

同期スクリプトでボットを再起動（`SYNC_GUIDE.md` 参照）。
`cogs/shadowverse/stats_publisher.py` は自動ロードされる。

---

## 運用

| 操作 | 説明 |
|---|---|
| 自動 | 毎日 **05:15 JST** に集計 → 変更があれば commit / push |
| `!svstats確認` | 集計のみ実行して件数を表示（push しない）。管理者権限が必要 |
| `!svstats公開` | 今すぐ集計 → commit / push。管理者権限が必要 |

- 内容に変化が無い日は commit されない（`git status --porcelain` で判定）。
- push 失敗時はコマンド応答とログにエラー内容（トークンはマスク）が出る。

---

## 変更したい場合

| 変えたいもの | 場所 |
|---|---|
| ローリング期間の種類・日数 | `sv_stats_agg.py` `WINDOW_DAYS` / `WINDOW_KEYS` |
| 詳細を伏せる記録者数のしきい値 | `sv_stats_agg.py` `MIN_RECORDERS_FOR_DETAIL` |
| アーキタイプ表の採用条件（最低試合数・件数） | `sv_stats_agg.py` `ARCHETYPE_MIN_GAMES` / `ARCHETYPE_TOP_N` |
| トレンドの日数 | `sv_stats_agg.py` `DAILY_TREND_DAYS` |
| 実行時刻 | `stats_publisher.py` `PUBLISH_TIME` |
| 出力パス | `stats_publisher.py` `REL_JSON_PATH` |
| 表示（表・マトリクス・グラフ） | `apeiron-site/sv-stats.js` / `sv-stats-style.css` |

---

## ローカルで JSON を再生成（デバッグ）

```bash
cd /path/to/discord-tournament-bot
python3 -c "
import sqlite3, json
from cogs.shadowverse.sv_stats_agg import build_payload
c = sqlite3.connect('data/main.db'); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute('SELECT * FROM sv_matches')]
json.dump(build_payload(rows), open('sv-stats.json','w'), ensure_ascii=False, indent=2)
print('rows:', len(rows))
"
```
