from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import Any


class OCRService:
    """Lazy RapidOCR wrapper returning stable evidence coordinates."""

    def __init__(self, engine=None):
        self._engine = engine
        self._lock = Lock()

    def _get_engine(self):
        if self._engine is None:
            with self._lock:
                if self._engine is None:
                    from rapidocr_onnxruntime import RapidOCR
                    self._engine = RapidOCR()
        return self._engine

    def recognize(self, image: str | Path | Any) -> list[dict[str, Any]]:
        result, _ = self._get_engine()(str(image) if isinstance(image, Path) else image)
        regions = []
        for item in result or []:
            polygon, text, confidence = item
            points = [[round(float(x), 2), round(float(y), 2)] for x, y in polygon]
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            regions.append({
                "text": str(text).strip(),
                "confidence": round(float(confidence), 6),
                "polygon": points,
                "bbox": {
                    "x_min": min(xs), "y_min": min(ys),
                    "x_max": max(xs), "y_max": max(ys),
                },
            })
        return [region for region in regions if region["text"]]
