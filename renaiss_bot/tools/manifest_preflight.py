"""Offline signature, schema, provenance, and price-gate check for a Renaiss manifest."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from renaiss_bot.runtime import load_runtime_environment
from renaiss_bot.services.card_pool import catalog_row_to_card
from renaiss_bot.services.manifest_signature import verify_manifest
from renaiss_bot.tools.catalog_audit import audit_cards, print_report
from renaiss_bot.tools.import_catalog_json import normalize_manifest_rows


def run(
    *,
    path: Path,
    signature_path: Path,
    public_key_path: Path,
    category: str,
    require_eligible: int,
) -> int:
    try:
        data = path.read_bytes()
        verified = verify_manifest(
            data,
            signature_base64=signature_path.read_text(encoding="ascii"),
            public_key_pem=public_key_path.read_bytes(),
            expected_public_key_sha256=os.getenv(
                "RENAISS_MANIFEST_PUBLIC_KEY_SHA256",
                "",
            ),
            expected_manifest_sha256=os.getenv("RENAISS_MANIFEST_SHA256", ""),
        )
        rows = normalize_manifest_rows(
            data,
            category,
            verified_manifest=verified,
        )
        cards = [catalog_row_to_card(row) for row in rows]
        report = audit_cards(cards)
        print(
            f"Manifest signature PASS | sha256={verified.sha256} "
            f"| signer={verified.signer_key_sha256}"
        )
        print_report(
            report,
            category=category,
            resolved_source=f"signed-manifest:{verified.sha256[:12]}",
        )
        required = max(0, require_eligible)
        if report["eligible"] < required:
            print(f"\nFAIL: verified eligible cards {report['eligible']} < required {required}")
            return 1
        print("\nPASS: signed manifest is ready for collection-only dry-run import")
        return 0
    except Exception as exc:
        print(f"Manifest preflight failed (error={type(exc).__name__}).")
        return 1


def main() -> None:
    load_runtime_environment()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--signature", type=Path, required=True)
    parser.add_argument("--public-key", type=Path, required=True)
    parser.add_argument("--category", default="pokemon_tcg")
    parser.add_argument("--require-eligible", type=int, default=0)
    args = parser.parse_args()
    raise SystemExit(
        run(
            path=args.path,
            signature_path=args.signature,
            public_key_path=args.public_key,
            category=args.category,
            require_eligible=args.require_eligible,
        )
    )


if __name__ == "__main__":
    main()
