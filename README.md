# SCF Controls Platform

[![CI](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/ci.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/ci.yml)
[![CodeQL](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/codeql.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/codeql.yml)
[![Gitleaks](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/gitleaks.yml)
[![Semgrep](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/semgrep.yml/badge.svg)](https://github.com/MarkAC007/scf-controls-platform-oss/actions/workflows/semgrep.yml)
[![License: AGPL-3.0-only](https://img.shields.io/badge/License-AGPL--3.0--only-blue.svg)](LICENSE)

A self-hosted **Governance, Risk & Compliance (GRC)** platform for managing cybersecurity
controls with the [Secure Controls Framework (SCF)](https://securecontrolsframework.com/).
Scope controls against 350+ frameworks (ISO 27001, SOC 2, NIST, PCI DSS, NIS2, and more),
track control maturity, run evidence-collection workflows, and assess inherent and residual
risk — all on your own infrastructure.

It ships as a **Docker Compose** stack with bundled PostgreSQL, Redis, and MinIO object
storage. No cloud account required.

**Learn more →** features, screenshots, and documentation at **[scfcontrolsplatform.com](https://scfcontrolsplatform.com/)**.

> **Bring your own SCF catalogue.** The SCF control content is licensed
> [CC BY-ND 4.0](https://creativecommons.org/licenses/by-nd/4.0/) and is **not** distributed
> with this project. You supply your own SCF Excel workbook (a free download from the SCF) and
> the included importer loads it. See [Getting started](#getting-started) below.

---

## Getting started

**The install guide lives in the end-user documentation:**

- **[Deployment guide](https://docs.scfcontrolsplatform.app/admin-guide/deployment/)** — requirements, the installer and every
  option it takes, loading your SCF workbook, first sign-in, upgrades and backups.
- **[Admin guide](https://docs.scfcontrolsplatform.app/admin-guide/)** — credentials and secrets, identity provider,
  configuration reference, backup and restore, troubleshooting.
- **[User guide](https://docs.scfcontrolsplatform.app/user-guide/)** — using the platform once it is running.

The short version, for a host that already has Docker with the Compose v2 plugin:

```bash
git clone https://github.com/MarkAC007/scf-controls-platform-oss.git
cd scf-controls-platform-oss
scripts/install.sh --up
```

The installer generates every credential the stack needs, asks only which database and sign-in
method to use, and starts the stack. Then open http://localhost:5173, upload your SCF workbook on
the onboarding screen, and follow the first sign-in step in the deployment guide for the sign-in
method you chose. Upgrades use `scripts/upgrade.sh`; read [UPGRADING.md](UPGRADING.md) first.

> **Trust boundary in single-tenant mode.** With `OSS_SINGLE_TENANT=1` (the default) and no
> identity provider enabled, the platform has **no user authentication**: anyone who can load the
> UI holds a platform-administrator credential. Do not expose such a deployment beyond a trusted
> network, and do not store third-party credentials in it, unless you have enabled the bundled
> identity provider or an external OIDC provider. See [`SECURITY.md`](SECURITY.md).

---

## Security & releases

To report a vulnerability, see [`SECURITY.md`](SECURITY.md).

### Verifying a release

Each release carries two files. This repository's release workflow
(`.github/workflows/oss-release.yml`) signs a
[SLSA build-provenance attestation](https://slsa.dev/) for both when it publishes the release:

- `upgrade-manifest.json`: the upgrade metadata that `scripts/upgrade.sh` reads.
- `release-images.json`: the digests of the backend and frontend images built for that release.

Download both and check them with the GitHub CLI:

```bash
TAG=vX.Y.Z   # the release you are installing
gh release download "$TAG" --repo MarkAC007/scf-controls-platform-oss \
  --pattern upgrade-manifest.json --pattern release-images.json
for f in upgrade-manifest.json release-images.json; do
  gh attestation verify "$f" --repo MarkAC007/scf-controls-platform-oss \
    --signer-workflow MarkAC007/scf-controls-platform-oss/.github/workflows/oss-release.yml
done
```

A successful check prints the workflow that signed the file and the commit it ran at. If the check
fails, the file is not the one this repository published, and you should not use it.

The image digests come from the verified `release-images.json`. Pull by digest and you get those
exact bytes, whatever any tag points at:

```bash
jq -r '.images[] | "\(.name)@\(.digest)"' release-images.json
docker pull ghcr.io/markac007/scf-backend@sha256:<digest from the line above>
```

The images carry no attestation of their own. They are built in a private repository, and
GitHub's attestation store is not available there. The attested `release-images.json` is what
ties an image digest to a release. Releases up to and including v1.3.1 have no attestations and no
`release-images.json`.

#### What the images contain, and what was waived

Releases after v1.3.1 also carry the release's image scan evidence, signed the same way:

- `sbom-scf-backend.cdx.json`, `sbom-scf-frontend.cdx.json`: a CycloneDX SBOM (software bill of
  materials) of each image, read from the digest in `release-images.json`.
- `image-scan-gate.json`: the result of the vulnerability scan gate for that release: every finding
  and its outcome, including whether it would hold a release, was waived by a VEX statement, or has
  no fix yet and was accepted. The rules, and what `accepted` means, are in `SECURITY.md` under
  "Vulnerabilities in the release images".
- `vex-*.json`: the OpenVEX documents the scan applied, which say why a waived finding does not
  affect the images. A release with no waivers has none.
- `scan-gitleaks.json`, `scan-osv.json`, `scan-semgrep.json`, `scan-zap.json`, `scan-zizmor.json`:
  one record per scan of the release's source commit, taken from the CI run on that commit in the
  private repository: secrets (Gitleaks), dependency advisories (OSV-Scanner), static analysis
  (Semgrep), a baseline web scan of the running stack (ZAP) and the GitHub workflows (zizmor). Each
  gives the scanner's version, the commit, the run, the outcome (`pass`, `fail`, `reported`,
  `error`, or `missing` when no scan ran) and how many findings it reported at each severity. They
  name no finding: the scanned source is private. The run id is a reference for the maintainers;
  the run itself is not public. Releases before these files were added do not have them.
- `scan-summary.md`: the same records as one page.

```bash
gh release download "$TAG" --repo MarkAC007/scf-controls-platform-oss \
  --pattern 'sbom-*.cdx.json' --pattern image-scan-gate.json --pattern 'vex-*.json' \
  --pattern 'scan-*.json' --pattern scan-summary.md
for f in sbom-*.cdx.json image-scan-gate.json vex-*.json scan-*.json scan-summary.md; do
  [ -e "$f" ] || continue
  gh attestation verify "$f" --repo MarkAC007/scf-controls-platform-oss \
    --signer-workflow MarkAC007/scf-controls-platform-oss/.github/workflows/oss-release.yml
done
```

---

## Licensing

Copyright (C) 2025-present Ginga Ninja Holdings Limited, trading as ComplianceGenie.io.

- **Software (this repository):** [GNU Affero General Public License, version 3 only](https://www.gnu.org/licenses/agpl-3.0.html)
  (`SPDX-License-Identifier: AGPL-3.0-only`) — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
  If you run a modified version for users over a network, the AGPL requires you to offer
  them its source.
- **Names and logos:** the AGPL grants no rights to "ComplianceGenie.io", "SCF Controls
  Platform" or their logos; forks must rebrand. See [`TRADEMARKS.md`](TRADEMARKS.md).
- **Contributions:** licensed inbound under `AGPL-3.0-only`, the same terms as the project —
  see [`CONTRIBUTING.md`](CONTRIBUTING.md).
- **SCF catalogue content:** [Creative Commons Attribution-NoDerivatives 4.0 (CC BY-ND 4.0)](https://creativecommons.org/licenses/by-nd/4.0/),
  © the Secure Controls Framework. **Not** included here — download your own workbook from
  [securecontrolsframework.com](https://securecontrolsframework.com/).

This project is not affiliated with or endorsed by the Secure Controls Framework.
