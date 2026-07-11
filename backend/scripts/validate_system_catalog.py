"""
Validation CLI for system catalog seed files.

Usage (from backend/):
    python scripts/validate_system_catalog.py             # schema validation
    python scripts/validate_system_catalog.py --check-urls  # + vendor docs URL liveness

Schema errors exit 1. URL check failures are warnings only (exit 0) —
vendor sites often block HEAD requests; use them to target spot review.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from catalog_seeder import load_system_catalog_files, SYSTEM_CATALOG_DIR


def collect_doc_urls(vendors, fallbacks) -> set:
    urls = set()

    def from_recipes(recipes):
        for recipe in recipes.values():
            for step in recipe.get("steps", []):
                url = step.get("vendor_docs_url")
                if url:
                    urls.add(url)

    for vendor in vendors:
        from_recipes(vendor["recipes"])
    for recipes in fallbacks.values():
        from_recipes(recipes)
    return urls


def check_urls(urls) -> int:
    import httpx

    warnings = 0
    with httpx.Client(timeout=10.0, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (compatible; SCF-catalog-check)"}) as client:
        for url in sorted(urls):
            try:
                resp = client.head(url)
                if resp.status_code >= 400:
                    # Some docs sites reject HEAD; retry with GET before warning
                    resp = client.get(url)
                if resp.status_code >= 400:
                    print(f"  WARN {resp.status_code}: {url}")
                    warnings += 1
            except httpx.HTTPError as exc:
                print(f"  WARN {type(exc).__name__}: {url}")
                warnings += 1
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate system catalog seed files")
    parser.add_argument("--check-urls", action="store_true",
                        help="Also check vendor_docs_url liveness (warnings only)")
    args = parser.parse_args()

    vendors, fallbacks, _fallbacks_version, errors = load_system_catalog_files()
    print(f"Loaded {len(vendors)} vendor files + {len(fallbacks)} fallback types "
          f"from {SYSTEM_CATALOG_DIR}")

    if errors:
        print("\nSCHEMA ERRORS:")
        for err in errors:
            print(f"  - {err}")
        return 1

    print("Schema validation: OK")

    if args.check_urls:
        urls = collect_doc_urls(vendors, fallbacks)
        print(f"\nChecking {len(urls)} vendor docs URLs...")
        warnings = check_urls(urls)
        print(f"URL check complete: {warnings} warnings")

    return 0


if __name__ == "__main__":
    sys.exit(main())
