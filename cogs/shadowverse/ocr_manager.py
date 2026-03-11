"""
OCR 抽象化レイヤー
Yomitoku と NDLOCR の両方に対応し、簡単に切り替え可能
"""

import os
from abc import ABC, abstractmethod
from typing import Optional


class OCREngine(ABC):
    """OCR エンジンの抽象基底クラス"""
    
    @abstractmethod
    async def extract_text(self, image_path: str) -> str:
        """画像からテキストを抽出"""
        pass
    
    @abstractmethod
    async def health_check(self) -> bool:
        """OCR エンジンの稼働状態を確認"""
        pass


class YomitokuOCR(OCREngine):
    """Yomitoku による OCR 実装"""
    
    def __init__(self, device: str = "cpu"):
        self.device = device
        self.analyzer = None
        self._initialized = False
    
    async def _initialize(self):
        """Yomitoku モデルを初期化"""
        if self._initialized:
            return
        
        from yomitoku import DocumentAnalyzer
        import asyncio
        loop = asyncio.get_running_loop()
        self.analyzer = await loop.run_in_executor(
            None, lambda: DocumentAnalyzer(device=self.device)
        )
        self._initialized = True
    
    async def extract_text(self, image_path: str) -> str:
        """Yomitoku でテキストを抽出"""
        if not self._initialized:
            await self._initialize()
        
        import asyncio
        from .sv_utils import extract_text_from_image as yomitoku_extract
        
        loop = asyncio.get_running_loop()
        text = await loop.run_in_executor(
            None, lambda: yomitoku_extract(self.analyzer, image_path)
        )
        return text or ""
    
    async def health_check(self) -> bool:
        """Yomitoku の状態確認"""
        try:
            await self._initialize()
            return self._initialized
        except Exception:
            return False


class NDLOCREngine(OCREngine):
    """NDLOCR v2.1 による OCR 実装"""
    
    def __init__(self, api_url: str = "http://localhost:5000"):
        self.api_url = api_url
    
    async def extract_text(self, image_path: str) -> str:
        """NDLOCR API を使用してテキストを抽出"""
        import aiohttp
        import json
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/ocr",
                    json={"image_path": image_path},
                    timeout=aiohttp.ClientTimeout(total=120)  # 2分タイムアウト
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        return result.get("text", "")
                    else:
                        raise Exception(f"NDLOCR API error: {resp.status}")
        except Exception as e:
            print(f"NDLOCR extraction error: {e}")
            return ""
    
    async def health_check(self) -> bool:
        """NDLOCR API の稼働状態確認"""
        import aiohttp
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_url}/health",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False


class OCRManager:
    """OCR エンジンマネージャー（ファクトリー + フェイルオーバー）"""
    
    # 環境変数で OCR エンジンを指定
    OCR_ENGINE = os.getenv("OCR_ENGINE", "yomitoku").lower()  # "yomitoku" or "ndlocr"
    NDLOCR_API_URL = os.getenv("NDLOCR_API_URL", "http://localhost:5000")
    
    _instance: Optional[OCREngine] = None
    _fallback: Optional[OCREngine] = None
    
    @classmethod
    def get_engine(cls) -> OCREngine:
        """利用可能な OCR エンジンを取得"""
        if cls._instance is None:
            cls._initialize_engine()
        return cls._instance
    
    @classmethod
    def _initialize_engine(cls):
        """OCR エンジンを初期化"""
        if cls.OCR_ENGINE == "ndlocr":
            cls._instance = NDLOCREngine(cls.NDLOCR_API_URL)
            cls._fallback = YomitokuOCR()  # フェイルオーバー
        else:
            cls._instance = YomitokuOCR()
            cls._fallback = NDLOCREngine(cls.NDLOCR_API_URL)  # フェイルオーバー
    
    @classmethod
    async def extract_text_with_fallback(cls, image_path: str) -> str:
        """フェイルオーバー対応でテキストを抽出"""
        engine = cls.get_engine()
        
        try:
            # プライマリ エンジンで試行
            text = await engine.extract_text(image_path)
            if text:
                return text
        except Exception as e:
            print(f"Primary OCR engine failed: {e}")
        
        # フェイルオーバー エンジンで試行
        if cls._fallback:
            try:
                print(f"Attempting fallback OCR engine...")
                text = await cls._fallback.extract_text(image_path)
                if text:
                    return text
            except Exception as e:
                print(f"Fallback OCR engine also failed: {e}")
        
        return ""
    
    @classmethod
    async def health_check(cls) -> dict:
        """すべての OCR エンジンの状態を確認"""
        engine = cls.get_engine()
        fallback = cls._fallback
        
        return {
            "primary": {
                "engine": cls.OCR_ENGINE,
                "healthy": await engine.health_check()
            },
            "fallback": {
                "engine": "ndlocr" if cls.OCR_ENGINE == "yomitoku" else "yomitoku",
                "healthy": await fallback.health_check() if fallback else False
            }
        }
