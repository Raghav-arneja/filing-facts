"""Cloud Storage raw store. Writes are create-only via a generation precondition."""

# google-cloud-storage ships no type hints for Bucket/Blob members. Strict mode elsewhere.
# pyright: reportUnknownMemberType=false

from __future__ import annotations

from pathlib import Path

from google.api_core.exceptions import PreconditionFailed
from google.cloud import storage

from filing_facts.storage.protocols import AlreadyExistsError


class GcsRawStore:
    def __init__(self, client: storage.Client, bucket: str, prefix: str) -> None:
        self._bucket = client.bucket(bucket)
        self._prefix = prefix.strip("/")

    def _blob(self, key: str) -> storage.Blob:
        return self._bucket.blob(f"{self._prefix}/{key}")

    def exists(self, key: str) -> bool:
        return bool(self._blob(key).exists())

    def uri_for(self, key: str) -> str:
        return f"gs://{self._bucket.name}/{self._blob(key).name}"

    def fetch(self, key: str, dest: Path) -> None:
        blob = self._blob(key)
        if not blob.exists():
            raise KeyError(key)
        blob.download_to_filename(str(dest))

    def put(self, key: str, path: Path, sha256: str) -> str:
        blob = self._blob(key)
        blob.metadata = {"sha256": sha256}
        try:
            # if_generation_match=0 means "only if no live object exists". Replays cannot overwrite.
            blob.upload_from_filename(str(path), if_generation_match=0)
        except PreconditionFailed as exc:
            blob.reload()
            existing = (blob.metadata or {}).get("sha256")
            raise AlreadyExistsError(key, existing) from exc
        return self.uri_for(key)
