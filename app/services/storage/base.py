"""Storage contract: bytes go to object storage, keys go to the database."""

from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass(slots=True)
class StoredPhoto:
    storage_key: str
    mime_type: str
    size_bytes: int
    width: int
    height: int


class StorageBackend(abc.ABC):
    @abc.abstractmethod
    async def save(self, data: bytes, *, key: str, mime_type: str) -> None: ...

    @abc.abstractmethod
    async def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    async def delete_prefix(self, prefix: str) -> None:
        """Purge everything under a prefix — account deletion needs this."""

    @abc.abstractmethod
    def url_for(self, key: str) -> str:
        """A URL the client can load. Time-limited in a real object store."""
