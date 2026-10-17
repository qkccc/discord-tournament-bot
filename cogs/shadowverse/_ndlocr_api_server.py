#!/usr/bin/env python3
"""
NDLOCR v2.1 API サーバー
Discord ボットから HTTP で OCR 処理をリクエストするための API
"""

import os
import json
import subprocess
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import Optional

app = FastAPI(title="NDLOCR API Server")

# 設定
NDLOCR_DIR = "/root/ndlocr_cli"
OUTPUT_DIR = "/tmp/ndlocr_output"
TEMP_DIR = "/tmp"


class OCRRequest(BaseModel):
    """OCR リクエスト"""
    image_path: str
    dump: bool = False  # 中間出力を保存するか


class OCRResponse(BaseModel):
    """OCR レスポンス"""
    text: str
    success: bool
    error: Optional[str] = None


@app.get("/health")
async def health_check():
    """ヘルスチェック"""
    return {
        "status": "healthy",
        "ocr_engine": "NDLOCR v2.1",
        "version": "2.1",
        "ndlocr_path": NDLOCR_DIR
    }


@app.post("/ocr", response_model=OCRResponse)
async def process_ocr(request: OCRRequest):
    """
    NDLOCR で OCR 処理を実行
    
    Parameters:
    - image_path: 処理対象の画像ファイルパス
    - dump: 中間出力を保存するか (デフォルト: False)
    
    Returns:
    - text: 抽出されたテキスト
    - success: 処理成功フラグ
    - error: エラーメッセージ（失敗時）
    """
    
    try:
        # ファイル存在確認
        if not Path(request.image_path).exists():
            raise FileNotFoundError(f"Image file not found: {request.image_path}")
        
        # 出力ディレクトリ作成
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        
        # NDLOCR コマンド構築
        cmd = [
            "python", "main.py", "infer",
            request.image_path,
            OUTPUT_DIR,
            "-s", "f",  # Single image file mode
            "-x"        # Save XML output
        ]
        
        if request.dump:
            cmd.append("-d")  # Dump intermediate outputs
        
        # NDLOCR 実行
        result = subprocess.run(
            cmd,
            cwd=NDLOCR_DIR,
            capture_output=True,
            text=True,
            timeout=120  # 2分タイムアウト
        )
        
        if result.returncode != 0:
            raise RuntimeError(f"NDLOCR process failed: {result.stderr}")
        
        # テキスト抽出（出力ディレクトリの txt ファイルから）
        extracted_text = _extract_text_from_output(OUTPUT_DIR)
        
        return OCRResponse(
            text=extracted_text,
            success=True
        )
    
    except FileNotFoundError as e:
        return OCRResponse(
            text="",
            success=False,
            error=f"File not found: {str(e)}"
        )
    except subprocess.TimeoutExpired:
        return OCRResponse(
            text="",
            success=False,
            error="OCR processing timed out (>120s)"
        )
    except Exception as e:
        return OCRResponse(
            text="",
            success=False,
            error=f"OCR processing failed: {str(e)}"
        )


@app.post("/ocr/batch")
async def process_ocr_batch(requests: list[OCRRequest]):
    """バッチ OCR 処理"""
    results = []
    for req in requests:
        result = await process_ocr(req)
        results.append(result)
    return results


@app.get("/status")
async def get_status():
    """NDLOCR エンジンの状態を確認"""
    ndlocr_available = Path(NDLOCR_DIR).exists()
    
    return {
        "ndlocr_available": ndlocr_available,
        "output_dir": OUTPUT_DIR,
        "ndlocr_dir": NDLOCR_DIR,
        "version": "2.1"
    }


def _extract_text_from_output(output_dir: str) -> str:
    """
    NDLOCR 出力ディレクトリからテキストを抽出
    
    出力構造:
    output_dir/
    ├── PID/
    │   ├── txt/
    │   │   └── R[xxxxx].txt
    │   └── xml/
    └── opt.json
    """
    
    try:
        # PID ディレクトリを探す
        output_path = Path(output_dir)
        pid_dirs = list(output_path.glob("*/txt/*.txt"))
        
        if not pid_dirs:
            return ""
        
        # 最初の txt ファイルを読む
        with open(pid_dirs[0], 'r', encoding='utf-8') as f:
            text = f.read()
        
        return text.strip()
    
    except Exception as e:
        print(f"Error extracting text from NDLOCR output: {e}")
        return ""


if __name__ == "__main__":
    import uvicorn
    
    print("""
    ╔═══════════════════════════════════════════════════════════════╗
    ║         NDLOCR v2.1 API Server が起動しました               ║
    ╚═══════════════════════════════════════════════════════════════╝
    
    API ドキュメント: http://localhost:5000/docs
    ヘルスチェック: http://localhost:5000/health
    """)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=5000,
        log_level="info"
    )
