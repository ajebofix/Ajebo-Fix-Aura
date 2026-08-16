from __future__ import annotations

from dataclasses import dataclass

from evidence.storage import RetrievedEvidenceObject, StoredEvidenceObject
from scripts import smoke_evidence_storage as smoke


@dataclass
class _FakeStorage:
    corrupt_read: bool = False
    sticky_delete: bool = False

    provider_name = "r2"

    def __post_init__(self):
        self.objects: dict[str, bytes] = {}
        self.delete_calls = 0

    def put_bytes(self, *, object_key: str, payload: bytes, content_type: str):
        del content_type
        self.objects[object_key] = payload
        return StoredEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            byte_size=len(payload),
        )

    def get_bytes(self, *, object_key: str, max_bytes: int):
        payload = self.objects[object_key][:max_bytes]
        if self.corrupt_read:
            payload = b"corrupt"
        return RetrievedEvidenceObject(
            provider=self.provider_name,
            object_key=object_key,
            payload=payload,
            byte_size=len(payload),
        )

    def delete(self, *, object_key: str) -> None:
        self.delete_calls += 1
        if not self.sticky_delete:
            self.objects.pop(object_key, None)

    def exists(self, *, object_key: str) -> bool:
        return object_key in self.objects


def test_smoke_tool_refuses_to_touch_storage_without_explicit_confirmation(
    monkeypatch,
    capsys,
):
    monkeypatch.delenv("EVIDENCE_STORAGE_SMOKE_CONFIRM", raising=False)

    def _unexpected_builder(_config):
        raise AssertionError("storage provider should not be constructed")

    monkeypatch.setattr(smoke, "build_evidence_storage_provider", _unexpected_builder)

    assert smoke.main() == 2
    output = capsys.readouterr().out
    assert "was not run" in output


def test_smoke_tool_verifies_write_read_lookup_and_delete(monkeypatch, capsys):
    fake = _FakeStorage()
    monkeypatch.setenv("EVIDENCE_STORAGE_SMOKE_CONFIRM", "1")
    monkeypatch.setattr(smoke, "build_evidence_storage_provider", lambda _config: fake)

    assert smoke.main() == 0
    assert fake.objects == {}
    assert fake.delete_calls == 1
    output = capsys.readouterr().out
    assert "smoke test passed" in output.lower()


def test_smoke_tool_cleans_up_after_read_integrity_failure(monkeypatch, capsys):
    fake = _FakeStorage(corrupt_read=True)
    monkeypatch.setenv("EVIDENCE_STORAGE_SMOKE_CONFIRM", "1")
    monkeypatch.setattr(smoke, "build_evidence_storage_provider", lambda _config: fake)

    assert smoke.main() == 1
    assert fake.objects == {}
    assert fake.delete_calls == 1
    output = capsys.readouterr().out
    assert "failed during private storage verification" in output


def test_smoke_tool_retries_cleanup_when_delete_did_not_remove_object(
    monkeypatch,
    capsys,
):
    fake = _FakeStorage(sticky_delete=True)
    monkeypatch.setenv("EVIDENCE_STORAGE_SMOKE_CONFIRM", "1")
    monkeypatch.setattr(smoke, "build_evidence_storage_provider", lambda _config: fake)

    assert smoke.main() == 1
    assert fake.delete_calls == 2
    assert fake.objects
    output = capsys.readouterr().out
    assert "failed during private storage verification" in output
