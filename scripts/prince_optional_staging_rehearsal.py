"""Optional, scope-locked Prince synthetic rehearsal in Railway pre-deploy.

No operation in production Aura or any other staging project unless this
specific synthetic-seed switch is explicitly enabled. The seed function
refuses wrong Railway project/environment identifiers even if misconfigured.
"""
from __future__ import annotations

import os


def main():
    if os.getenv("PRINCE_STAGING_SYNTHETIC_SEED", "").strip().lower() != "true":
        print("Prince synthetic staging rehearsal: not requested; skipping.")
        return
    from scripts.prince_seed_synthetic_staging import seed
    from scripts.prince_verify_synthetic_staging import verify
    seed()
    verify()


if __name__ == "__main__":
    main()
