# PROJECT OVERVIEW

このドキュメントは、Apeiron Botプロジェクトの実装全体像・設計・拡張ポイント・依存関係などをAIや開発者が素早く把握できるようまとめたものです。

---

## 1. プロジェクト概要

- Discordサーバー向け多機能Bot（大会運営、Shadowverse戦績管理、音楽、通話ログ、AI連携など）
- Python 3.10+ / discord.py / SQLite (aiosqlite)

---


## 2. ディレクトリ・主要ファイル・機能構成

### cogs/audio
- `music.py` : YouTube音楽再生（yt-dlp/ffmpeg連携、キュー管理、VC再生）
- `voice_logger.py` : 通話入退室ログ、定例通知、サブアカウント管理（登録/削除/一覧/除外判定）

### cogs/events
- `event_manager/` : 大会管理（スイスドロー/シングルエリミ/履歴/通知/承認フロー）
	- `cog.py` : イベント管理コマンド・UI
	- `database.py` : 大会用DB操作
	- `image_utils.py` : 画像処理（リザルト等）
	- `models.py` : DBモデル
	- `views.py` : Discord UI（ボタン/パネル）
- `roundrobin/` : チーム対抗総当たり戦
	- `cog.py` : チーム戦コマンド・進行管理
	- `rr_handlers.py` : チーム戦ロジック
	- `rr_models.py` : DBモデル
	- `rr_views.py` : UI

### cogs/shadowverse
- `main.py` : Shadowverseコマンド・右クリックメニュー・OCR連携
- `panel.py` : パネルUI・永続View・通知チャンネル設定・全データ削除
- `card_manager.py` : カードデータ管理
- `deck.py` : デッキ登録・管理
- `sv_db.py` : SV用DB操作
- `sv_ui.py` : UI部品（View/Modal/Select等）
- `sv_utils.py` : 戦績集計・Embed生成・OCR/テキスト解析
- `sv_constants.py` : 定数

### cogs/utils
- `db_handler.py` : 共通非同期DBハンドラ
- `reaction.py` : メッセージ/リアクション自動付与（@everyone検知、カスタム絵文字付与）
- `gemini.py` : Gemini AIチャット連携
- `constants.py` : 汎用定数
- `room_match.py` : ルームマッチ募集・通知

### その他
- `apeiron_bot.py` : エントリーポイント、Cog自動読込、ロギング
- `data/main.db` : 全機能統合DB

---

## 3. 主要クラス・関数・用途（cogs別）

### audio
- `MusicCog` : 音楽再生コマンド・VC管理
- `VoiceLoggerCog` : 通話ログ、定例通知、サブアカウント管理・除外判定

### events/event_manager
- `EventManagerCog` : 大会管理コマンド・進行
- `各種DBモデル/ハンドラ/ビュー` : 大会進行・履歴・承認・通知

### events/roundrobin
- `RoundRobinCog` : チーム戦コマンド・進行
- `rr_handlers/rr_models/rr_views` : チーム戦ロジック・UI

### shadowverse
- `ShadowverseCog` : SVコマンド・右クリックメニュー・OCR
- `ShadowversePanelCog` : パネルUI・永続View
- `ConfirmDeleteView` : 全データ削除ボタンの確認・削除処理
- `各種View/Modal` : 手動登録・履歴・通知設定・削除等
- `get_stats_summary/get_recent_matches` : 戦績集計・Embed生成

### utils
- `AsyncDatabaseManager` : 非同期DB操作
- `ReactionCog` : メッセージ/リアクション自動付与
- `GeminiCog` : AIチャット
- `RoomMatchCog` : ルームマッチ募集

---

## 4. 機能・拡張ポイントまとめ

- 各機能はCog単位で分離・拡張可能
- UI（View/Modal/Select）は`cogs/shadowverse/sv_ui.py`等で部品化
- DBスキーマは`data/main.db`に集約、必要に応じてテーブル追加
- イベント/大会/チーム戦/パネル/通話/AI/リアクション等、用途ごとに独立
- 新機能追加は`cogs/`配下に.py追加＋Cog登録で容易

---

---

## 3. 主要クラス・関数と用途

- `ShadowversePanelCog` : パネルUI管理、永続View復元
- `ConfirmDeleteView` : 全データ削除ボタンの確認・削除処理
- `is_sub_account_in_vc` : サブアカウントのVC判定
- `_get_mention_targets` : 定例通知の除外判定（サブ垢VC/リアクション対応）
- `register_sub_account` : サブアカウント登録コマンド

---


## 4. データベース設計（data/main.db）

### Shadowverse関連
- **sv_matches**: user_id (PK), match_time (PK), my_class, opponent_class, result, turn_order … 戦績記録
- **sv_user_settings**: user_id (PK), channel_id … 通知チャンネル設定
- **sv_panel_messages**: id (PK, AUTOINC), message_id, channel_id, created_at … パネル永続化

### 通話・サブアカウント関連
- **voice_sessions**: id (PK, AUTOINC), guild_id, channel_id, user_id, join_time, leave_time … 通話入退室ログ
- **user_sub_accounts**: main_user_id, sub_user_id (PK) … サブアカウント管理
- **voice_guild_settings**: guild_id (PK), call_notification_channel_id … 通話通知チャンネル設定

### イベント・大会関連
- **event_settings**: guild_id (PK), main_channel_id, match_channel_id … 大会用チャンネル設定
- **event_players**: guild_id, user_id (PK), display_name, is_dummy, score, opponents, byes, wins, losses, matches_played … 大会参加者
- **swiss_tournaments**: guild_id (PK), is_active, round_num, max_rounds … スイスドロー進行
- **swiss_pairings**: guild_id, player1_id (PK), player2_id … スイスドローペアリング
- **swiss_results**: guild_id, round_num, winner_id, loser_id (PK) … スイスドロー試合結果
- **se_tournaments**: guild_id (PK), is_active, num_players, num_rounds, bracket_message_id … シングルエリミ進行
- **se_matches**: guild_id, match_id (PK), round_num, match_in_round, player1_id, player2_id, player1_source_match_id, player2_source_match_id, winner_id, is_bye … シングルエリミ試合
- **event_recruitments**: guild_id (PK), message_id, channel_id … 募集セッション
- **event_participants**: guild_id, user_id (PK), display_name, is_dummy … 募集中参加者

### チーム戦（RoundRobin）関連
- **rr_teams**: team_id (PK, AUTOINC), guild_id, name, wins, losses, score_for, role_id … チーム情報
- **rr_players**: user_id, guild_id (PK), team_id, display_name, is_dummy, position … チームメンバー
- **rr_tournaments**: guild_id (PK), is_active, message_id, channel_id, current_round, member_order, participant_role_id … チーム戦進行
- **rr_matches**: match_id (PK, AUTOINC), guild_id, round_num, team1_id, team2_id, team1_score, team2_score, status, message_id … チーム戦試合
- **rr_config**: guild_id (PK), role_id, panel_message_id, panel_channel_id … パネル設定

### 大会履歴・アーカイブ
- **history_tournaments**: id (PK, AUTOINC), guild_id, name, type, end_date, winner_name, details … 過去大会
- **history_rankings**: tournament_id, rank, name, info … 過去大会順位

---

## 5. コマンド・UI・API

- `!サブ垢登録 <main_id> <sub_id>` / `!サブ垢削除 <sub_id>` / `!サブ垢一覧`
- Shadowverseパネルの各種ボタン（手動登録・戦績表示・履歴・全データ削除・通知チャンネル設定）

---

## 6. 永続化・通知・権限・例外処理

- パネルの永続View復元（再起動後もボタン有効）
- サブアカウントがVC参加または出席リアクション済みならメインアカ通知除外
- 例外発生時はユーザー通知＋ログ記録

---

## 7. 拡張・カスタマイズ方法

- Cog追加：`cogs/`配下に.pyを追加し、クラスで`commands.Cog`を継承
- DB拡張：`data/main.db`にテーブル追加、必要に応じてマイグレーション
- パネルUI拡張：`panel.py`にボタンやViewを追加

---

## 8. 依存ライブラリ

- discord.py, aiosqlite, watchdog, yt-dlp, opencv-python, pandas, google-generativeai など

---

## 9. 運用・デプロイ・同期

- VSCode⇔Proxmox間はrsync/watchdogで自動同期
- Bot再起動はsystemctl/VSCodeタスクで実施

---

（詳細はREADME.mdも参照）
