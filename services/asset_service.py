from __future__ import annotations

import hashlib
import mimetypes
import uuid
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image, ImageOps

from services.store import Store, now


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
TABLE_EXTS = {".csv", ".xlsx"}
DOCUMENT_EXTS = {".txt", ".md", ".pdf", ".docx"}
ALLOWED_EXTS = IMAGE_EXTS | TABLE_EXTS | DOCUMENT_EXTS
MAX_ASSET_BYTES = 100 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class AssetService:
    def __init__(self, store: Store):
        self.store = store
        self.data_root = store.root
        self.asset_root = store.root / "assets"
        self.asset_root.mkdir(parents=True, exist_ok=True)

    def register_file(self, source: Path, original_name: str | None = None) -> dict[str, Any]:
        source = Path(source)
        if not source.is_file():
            raise ValueError(f"File does not exist: {source}")
        name = Path(original_name or source.name).name
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            raise ValueError(f"Unsupported file type: {suffix or '(none)'}")
        size = source.stat().st_size
        if size > MAX_ASSET_BYTES:
            raise ValueError(f"File exceeds {MAX_ASSET_BYTES // (1024 * 1024)} MiB limit")

        digest = sha256(source)
        asset_id = f"asset_{uuid.uuid4().hex}"
        destination_dir = self.asset_root / asset_id
        destination_dir.mkdir(parents=True, exist_ok=False)
        destination = destination_dir / f"source{suffix}"
        destination.write_bytes(source.read_bytes())

        asset_type = "image" if suffix in IMAGE_EXTS else "table" if suffix in TABLE_EXTS else "document"
        record: dict[str, Any] = {
            "asset_id": asset_id,
            "original_name": name,
            "path": str(destination.resolve()),
            "sha256": digest,
            "size_bytes": size,
            "extension": suffix,
            "mime_type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "type": asset_type,
            "created_at": now(),
        }
        if asset_type == "image":
            try:
                with Image.open(destination) as image:
                    image.verify()
                with Image.open(destination) as image:
                    rgb = ImageOps.exif_transpose(image).convert("RGB")
                    rgb.load()
                    record.update({"width": rgb.width, "height": rgb.height,
                        "pixel_sha256": hashlib.sha256(str(rgb.size).encode() + rgb.tobytes()).hexdigest()})
            except Exception as exc:
                destination.unlink(missing_ok=True)
                raise ValueError(f"Invalid image: {name}: {exc}") from exc

        record["id"] = asset_id
        self.store.put("asset", record)
        return record

    def register_stream(self, stream: BinaryIO, original_name: str) -> dict[str, Any]:
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_EXTS:
            raise ValueError(f"Unsupported file type: {suffix or '(none)'}")
        temp_dir = self.data_root / "tmp" / uuid.uuid4().hex
        temp_dir.mkdir(parents=True, exist_ok=False)
        temp_path = temp_dir / f"upload{suffix}"
        try:
            total = 0
            with temp_path.open("wb") as output:
                while True:
                    chunk = stream.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_ASSET_BYTES:
                        raise ValueError(f"File exceeds {MAX_ASSET_BYTES // (1024 * 1024)} MiB limit")
                    output.write(chunk)
            return self.register_file(temp_path, original_name)
        finally:
            for child in temp_dir.glob("*"):
                child.unlink(missing_ok=True)
            temp_dir.rmdir()

    def get(self, asset_id: str) -> dict[str, Any]:
        return self.store.get("asset", asset_id)

    # Compatibility with the existing API and AOI service.
    def register(self, stream: BinaryIO, original_name: str) -> dict[str, Any]:
        return self.register_stream(stream, original_name)

    def resolve(self, asset_id: str) -> dict[str, Any]:
        return self.get(asset_id)

    def require_image(self, asset_id: str) -> dict[str, Any]:
        asset = self.get(asset_id)
        if asset.get("type") != "image":
            raise ValueError(f"Asset {asset_id} is not an image")
        return asset
