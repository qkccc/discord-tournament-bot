# OCR エンジン切り替えガイド

## 概要

Discord ボットの OCR 処理を **Yomitoku** から **NDLOCR v2.1** に切り替え可能なシステムを構築しました。

### サポートされた OCR エンジン

| エンジン | 速度 | 精度 | 推奨用途 | GPU要否 |
|---------|------|------|---------|--------|
| **Yomitoku** | 速い | 中 | シャドウバース戦績 | 不要 |
| **NDLOCR v2.1** | 遅い | 高 | 古典籍、複雑なレイアウト | 必要 |

---

## 🚀 セットアップ

### ステップ 1: NDLOCR v2.1 のセットアップ（Proxmox 側）

```bash
# 1. ワークスペースから NDLOCR をコピー
cd /root
unzip /config/workspace/ndlocr_cli*.zip

# 2. NDLOCR ディレクトリに移動
cd ndlocr_cli

# 3. サブモジュールを取得（インターネット接続が必要）
git clone --recursive https://github.com/ndl-lab/ndlocr_cli .

# または既存ファイルから
git submodule update --init --recursive

# 4. Docker イメージをビルド
# GPU がある場合
sh ./docker/dockerbuild.sh

# GPU がない場合（CPU モード）
# Dockerfile を修正して CUDA なしで実行
```

### ステップ 2: NDLOCR API サーバーを起動（Proxmox 側）

```bash
# 1. 必要なパッケージをインストール
pip install fastapi uvicorn

# 2. API サーバーを起動（バックグラウンド）
nohup python /root/discord-tournament-bot/cogs/shadowverse/ndlocr_api_server.py > /tmp/ndlocr_api.log 2>&1 &

# または systemd サービスとして登録
# /etc/systemd/system/ndlocr-api.service を作成して systemctl start ndlocr-api
```

### ステップ 3: Discord ボットで OCR エンジンを切り替え

**環境変数で指定：**

```bash
# Yomitoku を使用（デフォルト）
export OCR_ENGINE=yomitoku
systemctl restart discord-bot

# NDLOCR v2.1 に切り替え
export OCR_ENGINE=ndlocr
export NDLOCR_API_URL=http://localhost:5000
systemctl restart discord-bot
```

**.env ファイルで指定：**

```bash
# /root/discord-bot/.env
OCR_ENGINE=ndlocr
NDLOCR_API_URL=http://localhost:5000
```

**Python で読み込み：**

```python
# apeiron_bot.py の最初
from dotenv import load_dotenv
load_dotenv()
```

---

## 📋 OCR エンジンの仕組み

### OCRManager クラス

```python
from cogs.shadowverse.ocr_manager import OCRManager

# OCR を実行（自動的に利用可能なエンジンを選択）
text = await OCRManager.extract_text_with_fallback(image_path)

# ヘルスチェック（すべてのエンジンの状態確認）
status = await OCRManager.health_check()
```

### フェイルオーバー機能

- **プライマリ:** 環境変数で指定したエンジン
- **フェイルオーバー:** もう片方のエンジン

プライマリが失敗した場合、自動的にフェイルオーバーに切り替わります。

```python
# 例：NDLOCR が失敗した場合 → Yomitoku で再試行
OCR_ENGINE=ndlocr  # プライマリ: NDLOCR
                   # フェイルオーバー: Yomitoku
```

---

## 🔧 トラブルシューティング

### エラー: "NDLOCR API に接続できません"

```bash
# 1. API サーバーが起動しているか確認
curl http://localhost:5000/health

# 2. ポートが開いているか確認
netstat -tlnp | grep 5000

# 3. API サーバーのログを確認
tail -f /tmp/ndlocr_api.log
```

### エラー: "GPU メモリ不足"

```bash
# メモリ使用量を削減（Dockerfile 内で設定）
GPU_MEM_LIMIT = (1024**3) // 2  # 500 MB に削減
```

### NDLOCR が遅い場合

**原因：** 複雑な前処理（傾き補正、ノド元分割など）

**対応：** 部分実行で高速化

```bash
# 文字認識（ステップ 3）のみを実行
python main.py infer input output -p 3

# 設定ファイルで追加処理を無効化
# config.yml の line_order, ruby_read, add_title_author を false に
```

---

## 📊 パフォーマンス比較

### Yomitoku

```
処理時間: 2-5秒
メモリ: 1-2GB
精度: 中程度
GPU: 不要
```

### NDLOCR v2.1

```
処理時間: 20-60秒（複合処理含む）
メモリ: 4-8GB
精度: 高（レイアウト情報付き）
GPU: 推奨（CPU モードでは非常に遅い）
```

---

## 🎯 推奨される使い分け

### Yomitoku を使用

- ✅ シャドウバース戦績（テキストのみ）
- ✅ リアルタイム処理が必要
- ✅ GPU 環境がない

### NDLOCR v2.1 を使用

- ✅ 古典籍の読み込み
- ✅ 複雑なレイアウト（表、図など）
- ✅ 高精度が必要
- ✅ バッチ処理（複数枚をまとめて処理）

### Hybrid（両方使用）

```python
# Discord コマンドで使い分け
/replay          # Yomitoku（高速）
/replay-hq       # NDLOCR v2.1（高精度）
```

---

## 📝 実装例

### 環境変数で切り替え

```bash
#!/bin/bash
# switch-ocr.sh

case "$1" in
    yomitoku)
        export OCR_ENGINE=yomitoku
        echo "✅ OCR エンジンを Yomitoku に切り替えました"
        ;;
    ndlocr)
        export OCR_ENGINE=ndlocr
        export NDLOCR_API_URL=http://localhost:5000
        echo "✅ OCR エンジンを NDLOCR v2.1 に切り替えました"
        ;;
    *)
        echo "使用方法: ./switch-ocr.sh [yomitoku|ndlocr]"
        exit 1
        ;;
esac

systemctl restart discord-bot
```

### コマンド実行例

```bash
# Yomitoku で実行
./switch-ocr.sh yomitoku

# NDLOCR v2.1 で実行
./switch-ocr.sh ndlocr

# ステータス確認
curl http://localhost:5000/health
```

---

## 📚 参考資料

- [NDLOCR GitHub](https://github.com/ndl-lab/ndlocr_cli)
- [Yomitoku GitHub](https://github.com/taishi-i/yomitoku)
- [FastAPI ドキュメント](https://fastapi.tiangolo.com/)

---

**最終更新:** 2026-03-04
