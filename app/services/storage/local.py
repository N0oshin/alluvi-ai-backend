"""Local-disk storage plus the image pipeline.

Local disk stands in for S3/GCS during development. The important part is not
the backend but the shape: the API only ever hands out a *key* and a URL, so
switching to a real object store means implementing `StorageBackend` once and
changing an env var — no caller changes.
"""

from __future__ import annotations

import asyncio
import io
import shutil
import uuid
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.services.storage.base import StorageBackend, StoredPhoto


class LocalStorageBackend(StorageBackend):
    def __init__(self, root: str | None = None) -> None:
        self.root = Path(root or settings.MEDIA_ROOT).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Resolve and confine: a key is never allowed to escape the media root.
        target = (self.root / key).resolve()
        if not target.is_relative_to(self.root):
            raise ValueError(f"Refusing to write outside media root: {key}")
        return target

    async def save(self, data: bytes, *, key: str, mime_type: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def delete(self, key: str) -> None:
        path = self._path(key)
        await asyncio.to_thread(path.unlink, True)

    async def delete_prefix(self, prefix: str) -> None:
        path = self._path(prefix)
        if path.is_dir():
            await asyncio.to_thread(shutil.rmtree, path, True)

    def url_for(self, key: str) -> str:
        return f"{settings.MEDIA_URL_PREFIX}/{key}"


def process_photo(raw: bytes) -> tuple[bytes, int, int]:
    """Re-encode a phone photo for storage.

    Three things happen here, all deliberate:
      * Downscale to a sane long edge — phone JPEGs are multi-megabyte and the
        display size is a card thumbnail.
      * Re-encode as JPEG, which **drops EXIF** along with it. Phone photos
        carry GPS coordinates; meal photos are health data. Both reasons to
        not keep it.
      * Honour the EXIF orientation flag before discarding it, so portrait
        photos don't come back rotated.
    """
    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("unreadable image") from exc

    # Apply then discard orientation, before EXIF is dropped by the re-encode.
    try:
        from PIL import ImageOps

        image = ImageOps.exif_transpose(image)
    except Exception:  # orientation is best-effort, never fatal
        pass

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    max_edge = settings.PHOTO_MAX_EDGE_PX
    if max(image.size) > max_edge:
        image.thumbnail((max_edge, max_edge), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(
        buffer,
        format="JPEG",
        quality=settings.PHOTO_JPEG_QUALITY,
        optimize=True,
    )
    return buffer.getvalue(), image.width, image.height


def build_key(user_id: uuid.UUID, photo_id: uuid.UUID) -> str:
    """`meals/{userId}/{photoId}.jpg` — prefixed by user so account deletion
    can purge one subtree."""
    return f"meals/{user_id}/{photo_id}.jpg"


def build_avatar_key(user_id: uuid.UUID, photo_id: uuid.UUID) -> str:
    """`avatars/{userId}/{photoId}.jpg` — a fresh id per upload, so the URL
    changes on replace and client-side image caches never show a stale photo."""
    return f"avatars/{user_id}/{photo_id}.jpg"


_backend: StorageBackend | None = None


def get_storage() -> StorageBackend:
    global _backend
    if _backend is None:
        _backend = LocalStorageBackend()
    return _backend


__all__ = [
    "LocalStorageBackend",
    "StoredPhoto",
    "build_avatar_key",
    "build_key",
    "get_storage",
    "process_photo",
]
