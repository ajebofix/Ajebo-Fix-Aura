"""Perform one deliberate write/read/delete smoke test against private evidence storage.

Run only from an authorized deployment shell after the R2 variables are loaded:

    EVIDENCE_STORAGE_SMOKE_CONFIRM=1 python scripts/smoke_evidence_storage.py

The script uses an opaque temporary object, prints no storage identifiers or
credentials, and attempts cleanup even when verification fails.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evidence.storage import (  # noqa: E402
    EvidenceStorageConfigurationError,
    EvidenceStorageError,
    build_evidence_storage_provider,
)


_CONFIRM_ENV = "EVIDENCE_STORAGE_SMOKE_CONFIRM"
_PAYLOAD = b"aura-private-evidence-cutover-smoke-v1"


def _storage_config() -> dict[str, object]:
    return {
        "EVIDENCE_STORAGE_PROVIDER": os.getenv("EVIDENCE_STORAGE_PROVIDER", "r2"),
        "R2_ACCOUNT_ID": os.getenv("R2_ACCOUNT_ID"),
        "R2_ACCESS_KEY_ID": os.getenv("R2_ACCESS_KEY_ID"),
        "R2_SECRET_ACCESS_KEY": os.getenv("R2_SECRET_ACCESS_KEY"),
        "R2_BUCKET": os.getenv("R2_BUCKET"),
    }


def main() -> int:
    if os.getenv(_CONFIRM_ENV, "").strip() != "1":
        print(
            "Evidence storage smoke test was not run. "
            f"Set {_CONFIRM_ENV}=1 for one deliberate private-storage check."
        )
        return 2

    try:
        provider = build_evidence_storage_provider(_storage_config())
    except EvidenceStorageConfigurationError:
        print("Evidence storage smoke test failed: private storage is not configured.")
        return 1

    object_key = f"evidence-smoke/{uuid.uuid4().hex}.bin"
    object_created = False

    try:
        stored = provider.put_bytes(
            object_key=object_key,
            payload=_PAYLOAD,
            content_type="application/octet-stream",
        )
        object_created = True
        if stored.byte_size != len(_PAYLOAD):
            raise EvidenceStorageError("Smoke write size verification failed.")

        retrieved = provider.get_bytes(
            object_key=object_key,
            max_bytes=len(_PAYLOAD),
        )
        if retrieved.payload != _PAYLOAD or retrieved.byte_size != len(_PAYLOAD):
            raise EvidenceStorageError("Smoke read verification failed.")

        if not provider.exists(object_key=object_key):
            raise EvidenceStorageError("Smoke object lookup failed.")

        provider.delete(object_key=object_key)
        object_created = False

        if provider.exists(object_key=object_key):
            raise EvidenceStorageError("Smoke object cleanup verification failed.")

    except EvidenceStorageError:
        print("Evidence storage smoke test failed during private storage verification.")
        return 1
    finally:
        if object_created:
            try:
                provider.delete(object_key=object_key)
            except EvidenceStorageError:
                print("Warning: temporary smoke object cleanup requires operator review.")

    print("Evidence storage smoke test passed: write, read, integrity, and delete verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
