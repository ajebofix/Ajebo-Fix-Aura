"""Install a fresh Fernet-compatible profile key for one GitHub Actions job.

Only for CI. Never log the generated key or write it into the repository.
Production uses separately managed Railway environment configuration.
"""
from __future__ import annotations

import base64
import os
import secrets
from pathlib import Path


def main() -> None:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise SystemExit("Refusing to generate CI environment outside GitHub Actions.")

    env_file = os.environ.get("GITHUB_ENV")
    if not env_file:
        raise SystemExit("GitHub Actions GITHUB_ENV is unavailable.")

    key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    with Path(env_file).open("a", encoding="utf-8") as stream:
        stream.write(f"PROFILE_ENCRYPTION_KEY={key}\n")


if __name__ == "__main__":
    main()
