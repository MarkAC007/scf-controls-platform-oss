"""
One-off converter: split webclient/public/data/system_collection_recipes.json
into per-vendor seed files under backend/data/system_catalog/.

Kept for provenance — the monolith is retired as a data source after this,
but the file itself must remain (Dockerfile.backend copies it).

Usage: cd backend && python scripts/convert_recipes_monolith.py
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from services.system_catalog_validation import validate_vendor_file, validate_fallbacks_file

MONOLITH = Path(__file__).parent.parent.parent / "webclient" / "public" / "data" / "system_collection_recipes.json"
OUT_DIR = Path(__file__).parent.parent / "data" / "system_catalog"

# Metadata the monolith lacks, keyed by its top-level system key.
# primary_product picks which product's recipes become the template's recipes
# (only AWS has more than one product).
VENDOR_META = {
    "okta": {
        "slug": "okta",
        "name": "Okta",
        "vendor": "Okta, Inc.",
        "category": "Identity & Access Management",
        "description": "Workforce identity, single sign-on and MFA platform.",
        "website": "https://www.okta.com",
        "aliases": ["okta", "okta sso", "okta workforce identity"],
        "logo_hint": "okta",
    },
    "aws": {
        "slug": "aws",
        "name": "Amazon Web Services",
        "vendor": "Amazon Web Services, Inc.",
        "category": "Cloud Infrastructure",
        "description": "Public cloud platform; audit evidence via CloudTrail, Config and Security Hub.",
        "website": "https://aws.amazon.com",
        "aliases": ["aws", "amazon web services", "aws cloudtrail", "aws config"],
        "logo_hint": "aws",
        "primary_product": "aws_cloudtrail",
    },
    "azure_ad": {
        "slug": "microsoft-entra-id",
        "name": "Microsoft Entra ID",
        "vendor": "Microsoft Corporation",
        "category": "Identity & Access Management",
        "description": "Microsoft's cloud identity platform (formerly Azure Active Directory).",
        "website": "https://www.microsoft.com/en-gb/security/business/identity-access/microsoft-entra-id",
        "aliases": ["entra", "entra id", "azure ad", "azure active directory", "microsoft entra"],
        "logo_hint": "microsoft-entra",
    },
    "github": {
        "slug": "github",
        "name": "GitHub",
        "vendor": "GitHub, Inc.",
        "category": "Source Control & CI/CD",
        "description": "Code hosting, review and CI/CD platform; audit log and security evidence.",
        "website": "https://github.com",
        "aliases": ["github", "github enterprise", "github enterprise cloud"],
        "logo_hint": "github",
    },
    "jira": {
        "slug": "jira",
        "name": "Jira",
        "vendor": "Atlassian Pty Ltd",
        "category": "IT Service & Change Management",
        "description": "Issue tracking and change management platform (Jira Cloud).",
        "website": "https://www.atlassian.com/software/jira",
        "aliases": ["jira", "jira cloud", "atlassian jira"],
        "logo_hint": "jira",
    },
    "servicenow": {
        "slug": "servicenow",
        "name": "ServiceNow",
        "vendor": "ServiceNow, Inc.",
        "category": "IT Service Management",
        "description": "Enterprise ITSM platform; change, incident and CMDB evidence.",
        "website": "https://www.servicenow.com",
        "aliases": ["servicenow", "service now", "servicenow itsm"],
        "logo_hint": "servicenow",
    },
    "splunk": {
        "slug": "splunk",
        "name": "Splunk",
        "vendor": "Splunk LLC (Cisco)",
        "category": "SIEM & Log Management",
        "description": "Log aggregation, search and SIEM platform (Splunk Enterprise / Cloud).",
        "website": "https://www.splunk.com",
        "aliases": ["splunk", "splunk enterprise", "splunk cloud"],
        "logo_hint": "splunk",
    },
    "crowdstrike": {
        "slug": "crowdstrike",
        "name": "CrowdStrike Falcon",
        "vendor": "CrowdStrike, Inc.",
        "category": "Endpoint Detection & Response",
        "description": "Cloud-native EDR/XDR platform (Falcon).",
        "website": "https://www.crowdstrike.com",
        "aliases": ["crowdstrike", "falcon", "crowdstrike falcon"],
        "logo_hint": "crowdstrike",
    },
}


def main() -> int:
    monolith = json.loads(MONOLITH.read_text())
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    failures = []

    for key, entry in monolith.get("systems", {}).items():
        meta = VENDOR_META.get(key)
        if not meta:
            failures.append(f"{key}: no VENDOR_META entry")
            continue
        products = entry.get("products", {})
        primary = meta.pop("primary_product", None) or (next(iter(products)) if products else None)
        recipes = dict(entry.get("recipes", {}))
        if primary and primary in products:
            recipes.update(products[primary].get("recipes", {}))

        out = {
            **meta,
            "system_type": entry["system_type"],
            "version": "1.0",
            "recipes": recipes,
        }
        errors = validate_vendor_file(out)
        if errors:
            failures.append(f"{meta['slug']}: " + "; ".join(errors))
            continue
        path = OUT_DIR / f"{meta['slug']}.json"
        path.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.name} ({len(recipes)} recipe levels)")

    fallbacks = {
        "version": "1.0",
        "fallbacks": monolith.get("generic_fallbacks", {}),
    }
    errors = validate_fallbacks_file(fallbacks)
    if errors:
        failures.append("_fallbacks.json: " + "; ".join(errors))
    else:
        path = OUT_DIR / "_fallbacks.json"
        path.write_text(json.dumps(fallbacks, indent=2, ensure_ascii=False) + "\n")
        print(f"wrote {path.name} ({len(fallbacks['fallbacks'])} system types)")

    if failures:
        print("\nFAILURES:")
        for f in failures:
            print(" -", f)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
