from __future__ import annotations

from botocore.exceptions import ClientError
import pytest

from evidence.storage import (
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    R2EvidenceStorageProvider,
)


class FakeS3Client:
    def __init__(self):
        self.put_calls: list[dict[str, object]] = []
        self.delete_calls: list[dict[str, object]] = []
        self.head_calls: list[dict[str, object]] = []
        self.head_missing = False

    def put_object(self, **kwargs):
        self.put_calls.append(kwargs)
        return {"ETag": '"etag-123"'}

    def delete_object(self, **kwargs):
        self.delete_calls.append(kwargs)
        return {}

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs)
        if self.head_missing:
            raise ClientError(
                {
                    "Error": {"Code": "NoSuchKey", "Message": "missing"},
                    "ResponseMetadata": {"HTTPStatusCode": 404},
                },
                "HeadObject",
            )
        return {"ContentLength": 4}


def test_r2_provider_builds_private_s3_client_without_public_url(monkeypatch):
    fake = FakeS3Client()
    captured: dict[str, object] = {}

    def fake_boto_client(**kwargs):
        captured.update(kwargs)
        return fake

    monkeypatch.setattr("evidence.storage.boto3.client", fake_boto_client)

    provider = R2EvidenceStorageProvider(
        account_id="account-123",
        access_key_id="access-123",
        secret_access_key="secret-123",
        bucket="aura-private-evidence",
    )

    assert captured["service_name"] == "s3"
    assert captured["endpoint_url"] == "https://account-123.r2.cloudflarestorage.com"
    assert captured["region_name"] == "auto"
    assert captured["aws_access_key_id"] == "access-123"
    assert captured["aws_secret_access_key"] == "secret-123"
    assert not hasattr(provider, "public_url")

    stored = provider.put_bytes(
        object_key="evidence/vehicles/1/object.jpg",
        payload=b"data",
        content_type="image/jpeg",
    )
    assert stored.provider == "r2"
    assert stored.object_key == "evidence/vehicles/1/object.jpg"
    assert stored.byte_size == 4
    assert stored.etag == "etag-123"
    assert fake.put_calls == [
        {
            "Bucket": "aura-private-evidence",
            "Key": "evidence/vehicles/1/object.jpg",
            "Body": b"data",
            "ContentType": "image/jpeg",
        }
    ]


def test_r2_provider_requires_complete_secret_configuration(monkeypatch):
    monkeypatch.setattr(
        "evidence.storage.boto3.client",
        lambda **_kwargs: pytest.fail("boto3 must not initialize with missing secrets"),
    )

    with pytest.raises(EvidenceStorageConfigurationError):
        R2EvidenceStorageProvider(
            account_id="account-123",
            access_key_id="",
            secret_access_key="secret-123",
            bucket="aura-private-evidence",
        )


def test_r2_provider_rejects_unsafe_object_key_before_network(monkeypatch):
    fake = FakeS3Client()
    monkeypatch.setattr("evidence.storage.boto3.client", lambda **_kwargs: fake)
    provider = R2EvidenceStorageProvider(
        account_id="account-123",
        access_key_id="access-123",
        secret_access_key="secret-123",
        bucket="aura-private-evidence",
    )

    with pytest.raises(EvidenceStorageError, match="key is invalid"):
        provider.put_bytes(
            object_key="evidence/../escape.jpg",
            payload=b"data",
            content_type="image/jpeg",
        )
    assert fake.put_calls == []


def test_r2_exists_distinguishes_missing_object_without_leaking_error(monkeypatch):
    fake = FakeS3Client()
    monkeypatch.setattr("evidence.storage.boto3.client", lambda **_kwargs: fake)
    provider = R2EvidenceStorageProvider(
        account_id="account-123",
        access_key_id="access-123",
        secret_access_key="secret-123",
        bucket="aura-private-evidence",
    )

    assert provider.exists(object_key="evidence/vehicles/1/a.jpg") is True
    fake.head_missing = True
    assert provider.exists(object_key="evidence/vehicles/1/missing.jpg") is False


def test_r2_delete_uses_private_bucket_and_opaque_key(monkeypatch):
    fake = FakeS3Client()
    monkeypatch.setattr("evidence.storage.boto3.client", lambda **_kwargs: fake)
    provider = R2EvidenceStorageProvider(
        account_id="account-123",
        access_key_id="access-123",
        secret_access_key="secret-123",
        bucket="aura-private-evidence",
    )

    provider.delete(object_key="evidence/vehicles/1/a.jpg")
    assert fake.delete_calls == [
        {
            "Bucket": "aura-private-evidence",
            "Key": "evidence/vehicles/1/a.jpg",
        }
    ]
