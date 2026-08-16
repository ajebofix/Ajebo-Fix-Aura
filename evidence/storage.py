"""Private object-storage boundary for Wave 1.4 evidence.

The domain never exposes bucket credentials or permanent public URLs. The first
production adapter targets Cloudflare R2 through its S3-compatible API while
keeping evidence workflows provider-neutral.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class EvidenceStorageError(RuntimeError):
    """Raised when private evidence storage cannot complete an operation."""


class EvidenceStorageConfigurationError(EvidenceStorageError):
    """Raised when the configured storage provider is incomplete or invalid."""


@dataclass(frozen=True)
class StoredEvidenceObject:
    provider: str
    object_key: str
    byte_size: int
    etag: str | None = None


class EvidenceStorageProvider(Protocol):
    """Minimal storage contract needed by the first evidence intake slice."""

    provider_name: str

    def put_bytes(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
    ) -> StoredEvidenceObject: ...

    def delete(self, *, object_key: str) -> None: ...

    def exists(self, *, object_key: str) -> bool: ...


class R2EvidenceStorageProvider:
    """Cloudflare R2 adapter using the supported S3-compatible API."""

    provider_name = "r2"

    def __init__(
        self,
        *,
        account_id: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        account_id = account_id.strip()
        access_key_id = access_key_id.strip()
        secret_access_key = secret_access_key.strip()
        bucket = bucket.strip()

        missing = [
            label
            for label, value in (
                ("R2_ACCOUNT_ID", account_id),
                ("R2_ACCESS_KEY_ID", access_key_id),
                ("R2_SECRET_ACCESS_KEY", secret_access_key),
                ("R2_BUCKET", bucket),
            )
            if not value
        ]
        if missing:
            raise EvidenceStorageConfigurationError(
                "Private evidence storage is not fully configured."
            )

        self._bucket = bucket
        self._client = boto3.client(
            service_name="s3",
            endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name="auto",
            config=Config(
                signature_version="s3v4",
                connect_timeout=5,
                read_timeout=15,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    @classmethod
    def from_config(cls, config: Mapping[str, object]) -> "R2EvidenceStorageProvider":
        return cls(
            account_id=str(config.get("R2_ACCOUNT_ID") or ""),
            access_key_id=str(config.get("R2_ACCESS_KEY_ID") or ""),
            secret_access_key=str(config.get("R2_SECRET_ACCESS_KEY") or ""),
            bucket=str(config.get("R2_BUCKET") or ""),
        )

    def put_bytes(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
    ) -> StoredEvidenceObject:
        if not object_key or object_key.startswith("/") or ".." in object_key.split("/"):
            raise EvidenceStorageError("Evidence object key is invalid.")
        if not payload:
            raise EvidenceStorageError("Evidence payload is empty.")

        try:
            response = self._client.put_object(
                Bucket=self._bucket,
                Key=object_key,
                Body=payload,
                ContentType=content_type,
            )
        except (BotoCoreError, ClientError) as exc:
            raise EvidenceStorageError("Private evidence storage write failed.") from exc

        etag = response.get("ETag")
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
            etag=str(etag).strip('"') if etag else None,
        )

    def delete(self, *, object_key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_key)
        except (BotoCoreError, ClientError) as exc:
            raise EvidenceStorageError("Private evidence storage delete failed.") from exc

    def exists(self, *, object_key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError as exc:
            status = int(exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0))
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            if status == 404 or error_code in {"404", "NoSuchKey", "NotFound"}:
                return False
            raise EvidenceStorageError("Private evidence storage lookup failed.") from exc
        except BotoCoreError as exc:
            raise EvidenceStorageError("Private evidence storage lookup failed.") from exc
