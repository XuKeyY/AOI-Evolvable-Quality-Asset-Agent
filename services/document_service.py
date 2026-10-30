from __future__ import annotations

import csv
import hashlib
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from openpyxl import load_workbook
from pypdf import PdfReader

from services.asset_service import AssetService
from services.store import Store, now
from services.trace_service import TraceService
from services.ocr_service import OCRService


class DocumentService:
    PARSER_VERSION = "unified-v2-ocr"

    def __init__(self, assets: AssetService, store: Store, traces: TraceService, ocr: OCRService | None = None):
        self.assets = assets
        self.store = store
        self.traces = traces
        self.ocr = ocr or OCRService()

    def parse(self, asset_id: str, force: bool = False) -> dict[str, Any]:
        asset = self.assets.resolve(asset_id)
        parse_id = f"parse_{asset_id}"
        if not force:
            try:
                cached = self.store.get("asset_parse", parse_id)
                if cached.get("source_sha256") == asset["sha256"] and cached.get("parser_version") == self.PARSER_VERSION:
                    return cached
            except KeyError:
                pass
        trace = self.traces.start("parse_asset", {"asset_id": asset_id, "parser_version": self.PARSER_VERSION})
        try:
            chunks, notices = self._extract(asset)
            record = {
                "id": parse_id,
                "parse_id": parse_id,
                "asset_id": asset_id,
                "asset_type": asset["type"],
                "source_sha256": asset["sha256"],
                "parser_version": self.PARSER_VERSION,
                "status": "parsed" if chunks else "metadata_only",
                "summary": self._summary(chunks, asset),
                "chunks": chunks,
                "notices": notices,
                "trace_id": trace["id"],
                "created_at": now(),
            }
            self.store.put("asset_parse", record)
            self.traces.finish(trace["id"], {"parse_id": parse_id, "chunk_count": len(chunks)})
            return record
        except Exception as exc:
            self.traces.finish(trace["id"], error=str(exc))
            raise

    def _extract(self, asset: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        path = Path(asset["path"])
        suffix = path.suffix.lower()
        if asset["type"] == "image":
            regions = self.ocr.recognize(path)
            chunks = [self._ocr_chunk(asset["asset_id"], region, 1, asset.get("width"), asset.get("height")) for region in regions]
            if chunks:
                return chunks, []
            text = f"图像 {asset['original_name']}，尺寸 {asset.get('width')}x{asset.get('height')}，未识别到文字。"
            return [self._chunk(asset["asset_id"], {"kind": "image", "page": 1}, text)], ["OCR 未识别到文字"]
        if suffix in {".txt", ".md"}:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            return self._text_chunks(asset["asset_id"], text, "paragraph"), []
        if suffix == ".csv":
            return self._csv_chunks(asset["asset_id"], path), []
        if suffix == ".xlsx":
            return self._xlsx_chunks(asset["asset_id"], path), []
        if suffix == ".pdf":
            chunks = []
            reader = PdfReader(str(path))
            for page_no, page in enumerate(reader.pages, 1):
                text = (page.extract_text() or "").strip()
                if text:
                    chunks.append(self._chunk(asset["asset_id"], {"kind": "pdf_text", "page": page_no}, text))
            chunks.extend(self._pdf_ocr_chunks(asset["asset_id"], path))
            return chunks, ([] if chunks else ["PDF 未抽取或识别到文字"])
        if suffix == ".docx":
            with zipfile.ZipFile(path) as archive:
                root = ElementTree.fromstring(archive.read("word/document.xml"))
            ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
            chunks = []
            for index, paragraph in enumerate(root.iter(ns + "p"), 1):
                text = "".join(node.text or "" for node in paragraph.iter(ns + "t")).strip()
                if text:
                    chunks.append(self._chunk(asset["asset_id"], {"paragraph": index}, text))
            return chunks, []
        raise ValueError(f"No parser for {suffix}")

    def _csv_chunks(self, asset_id: str, path: Path) -> list[dict[str, Any]]:
        with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
            rows = list(csv.reader(handle))
        if not rows:
            return []
        headers = rows[0]
        chunks = []
        for row_no, values in enumerate(rows[1:], 2):
            text = "；".join(f"{headers[i] if i < len(headers) else i + 1}={value}" for i, value in enumerate(values) if value)
            if text:
                chunks.append(self._chunk(asset_id, {"row": row_no}, text))
        return chunks

    def _xlsx_chunks(self, asset_id: str, path: Path) -> list[dict[str, Any]]:
        workbook = load_workbook(path, read_only=True, data_only=True)
        chunks = []
        try:
            for sheet in workbook.worksheets:
                rows = sheet.iter_rows(values_only=True)
                headers = [str(value or "") for value in next(rows, ())]
                for row_no, values in enumerate(rows, 2):
                    text = "；".join(f"{headers[i] if i < len(headers) and headers[i] else i + 1}={value}" for i, value in enumerate(values) if value not in (None, ""))
                    if text:
                        chunks.append(self._chunk(asset_id, {"sheet": sheet.title, "row": row_no}, text))
        finally:
            workbook.close()
        return chunks

    def _text_chunks(self, asset_id: str, text: str, kind: str) -> list[dict[str, Any]]:
        paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
        return [self._chunk(asset_id, {kind: index}, value) for index, value in enumerate(paragraphs, 1)]

    def _pdf_ocr_chunks(self, asset_id: str, path: Path) -> list[dict[str, Any]]:
        import fitz
        import numpy as np

        chunks = []
        document = fitz.open(path)
        try:
            matrix = fitz.Matrix(2, 2)
            for page_no, page in enumerate(document, 1):
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                image = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(pixmap.height, pixmap.width, pixmap.n)
                for region in self.ocr.recognize(image):
                    chunks.append(self._ocr_chunk(asset_id, region, page_no, pixmap.width, pixmap.height))
        finally:
            document.close()
        return chunks

    def _ocr_chunk(self, asset_id: str, region: dict[str, Any], page: int, width: int | None, height: int | None) -> dict[str, Any]:
        locator = {
            "kind": "ocr",
            "page": page,
            "polygon": region["polygon"],
            "bbox": region["bbox"],
            "confidence": region["confidence"],
            "image_width": width,
            "image_height": height,
        }
        return self._chunk(asset_id, locator, region["text"])

    @staticmethod
    def _chunk(asset_id: str, locator: dict[str, Any], text: str) -> dict[str, Any]:
        normalized = " ".join(text.split())[:8000]
        raw = f"{asset_id}|{locator}|{normalized}".encode("utf-8")
        return {"chunk_id": "chunk_" + hashlib.sha256(raw).hexdigest()[:20], "asset_id": asset_id, "locator": locator, "text": normalized}

    @staticmethod
    def _summary(chunks: list[dict[str, Any]], asset: dict[str, Any]) -> str:
        if not chunks:
            return f"{asset['original_name']}：未抽取到可检索内容"
        preview = " ".join(chunk["text"] for chunk in chunks[:3])[:500]
        return f"{asset['original_name']}：{len(chunks)} 个证据片段。{preview}"
