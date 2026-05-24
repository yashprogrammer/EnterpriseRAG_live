"""Local filesystem storage backend."""

from pathlib import Path

from app.storage.storage_backend import StorageBackend


class LocalStorage(StorageBackend):
    """Stores bytes under a local base directory."""

    def __init__(self, base_dir: str = "./cache"):
        self.base_dir = Path(base_dir).expanduser().resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        candidate = (self.base_dir / key).resolve()
        if not str(candidate).startswith(str(self.base_dir)):
            raise ValueError(f"Invalid storage key path traversal: {key}")
        return candidate

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def save_bytes(self, key: str, data: bytes) -> None:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def read_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        return path.read_bytes()

    def url_for(self, key: str) -> str:
        path = self._resolve(key)
        return path.as_uri()