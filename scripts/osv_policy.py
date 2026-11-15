#!/usr/bin/env python3
"""Decide whether an OSV-Scanner result blocks.

scripts/osv-scan.sh runs the scanner and hands its JSON output to this file.
The scanner only lists what it found; this file is the policy:

* a finding rated high or critical, with a fixed version published, blocks;
* a finding with no rating at all is treated as high, so it blocks too when a
  fix exists;
* everything else is reported and does not block;
* a waiver in ``osv-scanner.toml`` takes one finding out of the blocking set
  until its date. A waiver needs an id, a reason and a date. A waiver past its
  date blocks, whether or not the finding is still there, so the file cannot
  quietly fill with dead entries;
* scanner output that is missing, unreadable or does not cover every lockfile
  it was asked to scan is a failure, not a pass.

Exit status: 0 nothing blocks, 1 a finding or a waiver blocks, 2 the scanner
output or the waiver file cannot be trusted.

Standard library only, Python 3.11 or later (for ``tomllib``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# CVSS base score bands, and the ratings that block when a fix exists. "unrated"
# is in the blocking set on purpose: an advisory nobody has scored yet is not
# known to be minor. Take it out of BLOCKING_RATINGS to report those instead.
CRITICAL_AT = 9.0
HIGH_AT = 7.0
MEDIUM_AT = 4.0
BLOCKING_RATINGS = {"critical", "high", "unrated"}

# How GitHub advisories spell their own rating.
LABELS = {"CRITICAL": "critical", "HIGH": "high", "MODERATE": "medium", "MEDIUM": "medium", "LOW": "low"}
ORDER = ["unrated", "low", "medium", "high", "critical"]

# The only table this policy reads from osv-scanner.toml. The scanner itself
# understands more (PackageOverrides, for one), and none of it is applied here,
# so anything else in the file would look like a rule and do nothing.
WAIVER_TABLE = "IgnoredVulns"

PASS, BLOCKED, UNTRUSTED = 0, 1, 2


class Untrusted(Exception):
    """The scanner output or the waiver file cannot be relied on."""


@dataclass
class Waiver:
    id: str
    until: dt.date
    reason: str
    used: bool = False


@dataclass
class Finding:
    lockfile: str
    package: str
    version: str
    ids: list[str]
    names: set[str]
    rating: str
    score: str
    fixed: list[str]
    waiver: Waiver | None = None

    @property
    def blocks(self) -> bool:
        return self.rating in BLOCKING_RATINGS and bool(self.fixed) and self.waiver is None


@dataclass
class Verdict:
    findings: list[Finding] = field(default_factory=list)
    expired: list[Waiver] = field(default_factory=list)
    unused: list[Waiver] = field(default_factory=list)
    packages: dict[str, int] = field(default_factory=dict)

    @property
    def blocking(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.blocks]

    @property
    def status(self) -> int:
        return BLOCKED if self.blocking or self.expired else PASS


def _rating(score: str, labels: set[str]) -> str:
    """The worse of the CVSS band and the advisory's own label."""
    found = []
    try:
        value = float(score)
    except (TypeError, ValueError):
        value = None
    if value is not None and value > 0:
        found.append(
            "critical" if value >= CRITICAL_AT else "high" if value >= HIGH_AT
            else "medium" if value >= MEDIUM_AT else "low"
        )
    found += [LABELS[label.upper()] for label in labels if label and label.upper() in LABELS]
    return max(found, key=ORDER.index) if found else "unrated"


def _fixed_versions(vulnerabilities: list[dict], package: str) -> list[str]:
    wanted = package.lower().replace("_", "-")
    fixed = set()
    for vulnerability in vulnerabilities:
        for affected in vulnerability.get("affected", []):
            name = str(affected.get("package", {}).get("name", "")).lower().replace("_", "-")
            if name != wanted:
                continue
            for version_range in affected.get("ranges", []):
                if version_range.get("type") == "GIT":
                    continue  # a commit, not a version anyone can pin
                fixed |= {event["fixed"] for event in version_range.get("events", []) if "fixed" in event}
    return sorted(fixed)


def load_waivers(path: Path) -> list[Waiver]:
    try:
        document = tomllib.loads(path.read_text())
    except FileNotFoundError:
        raise Untrusted(f"{path} is missing. An empty file means no waivers; no file means nobody checked.")
    except tomllib.TOMLDecodeError as error:
        raise Untrusted(f"{path} is not valid TOML: {error}")
    extra = sorted(set(document) - {WAIVER_TABLE})
    if extra:
        raise Untrusted(f"{path}: {extra} is not read by this policy. Only [[{WAIVER_TABLE}]] entries are.")
    waivers, seen = [], set()
    for number, entry in enumerate(document.get(WAIVER_TABLE, []), start=1):
        where = f"{path}: waiver {number}"
        extra = sorted(set(entry) - {"id", "ignoreUntil", "reason"})
        if extra:
            raise Untrusted(f"{where} has {extra}; a waiver is an id, an ignoreUntil date and a reason")
        identifier, until, reason = entry.get("id"), entry.get("ignoreUntil"), entry.get("reason")
        if not isinstance(identifier, str) or not identifier.strip():
            raise Untrusted(f"{where} has no id")
        if isinstance(until, dt.datetime):
            until = until.date()
        if not isinstance(until, dt.date):
            raise Untrusted(f"{where} ({identifier}) needs ignoreUntil as a date, written without quotes: 2027-01-31")
        if not isinstance(reason, str) or len(reason.strip()) < 10:
            raise Untrusted(f"{where} ({identifier}) needs a reason that says why it is safe to wait")
        if identifier in seen:
            raise Untrusted(f"{where}: {identifier} is waived twice")
        seen.add(identifier)
        waivers.append(Waiver(identifier, until, reason.strip()))
    return waivers


def load_results(path: Path, expected: list[str], strip: str = "") -> tuple[list[dict], dict[str, int]]:
    try:
        document = json.loads(path.read_text())
    except FileNotFoundError:
        raise Untrusted(f"no scanner output at {path}")
    except json.JSONDecodeError as error:
        raise Untrusted(f"the scanner output is not JSON: {error}")
    results = document.get("results") if isinstance(document, dict) else None
    if not isinstance(results, list):
        raise Untrusted("the scanner output has no results list")
    packages: dict[str, int] = {}
    for result in results:
        source = str(result.get("source", {}).get("path", ""))
        if strip and source.startswith(strip):
            source = source[len(strip):]
        # Written back so the report names the lockfile as the repository does.
        result.setdefault("source", {})["path"] = source
        packages[source] = packages.get(source, 0) + len(result.get("packages", []))
    for lockfile in expected:
        if not packages.get(lockfile):
            raise Untrusted(f"the scanner output lists no packages for {lockfile}; it was not scanned")
    return results, packages


def evaluate(results: list[dict], waivers: list[Waiver], today: dt.date) -> Verdict:
    verdict = Verdict()
    verdict.expired = [waiver for waiver in waivers if waiver.until < today]
    live = {waiver.id: waiver for waiver in waivers if waiver.until >= today}
    for result in results:
        lockfile = str(result.get("source", {}).get("path", ""))
        for entry in result.get("packages", []):
            package = entry.get("package", {})
            by_id = {vulnerability["id"]: vulnerability for vulnerability in entry.get("vulnerabilities", [])}
            for group in entry.get("groups", []):
                ids = list(group.get("ids", []))
                members = [by_id[identifier] for identifier in ids if identifier in by_id]
                labels = {str(member.get("database_specific", {}).get("severity", "")) for member in members}
                names = set(ids) | set(group.get("aliases", []))
                for member in members:
                    names |= set(member.get("aliases", []))
                finding = Finding(
                    lockfile=lockfile,
                    package=str(package.get("name", "?")),
                    version=str(package.get("version", "?")),
                    ids=ids,
                    names=names,
                    rating=_rating(group.get("max_severity", ""), labels),
                    score=str(group.get("max_severity", "") or "none"),
                    fixed=_fixed_versions(members, str(package.get("name", ""))),
                )
                for name in sorted(names):
                    if name in live:
                        finding.waiver = live[name]
                        live[name].used = True
                        break
                verdict.findings.append(finding)
    verdict.unused = [waiver for waiver in live.values() if not waiver.used]
    return verdict


def report(verdict: Verdict) -> str:
    def row(finding: Finding) -> str:
        return (
            f"| {finding.lockfile} | {finding.package} {finding.version} | {finding.ids[0]} | "
            f"{finding.rating} ({finding.score}) | {', '.join(finding.fixed) or 'none'} |"
        )

    head = ["| Lockfile | Package | Advisory | Rating (CVSS) | Fixed in |", "|---|---|---|---|---|"]
    lines = ["## OSV scan", ""]
    scanned = ", ".join(f"{path} ({count})" for path, count in sorted(verdict.packages.items()))
    lines += [f"Packages scanned: {scanned}.", ""]

    blocking = verdict.blocking
    if blocking:
        lines += [f"### Blocking: {len(blocking)} high or critical finding(s) with a fix", "", *head]
        lines += [row(finding) for finding in sorted(blocking, key=lambda f: (f.lockfile, f.package))]
        lines += ["", "Move the package to a fixed version, or add a dated waiver to `osv-scanner.toml`.", ""]
    if verdict.expired:
        lines += [f"### Blocking: {len(verdict.expired)} waiver(s) past their date", ""]
        lines += [f"- `{waiver.id}` ran out on {waiver.until}: {waiver.reason}" for waiver in verdict.expired]
        lines += ["", "Remove each one, or renew it with a new date and a reason that still holds.", ""]
    if not blocking and not verdict.expired:
        lines += ["Nothing blocks.", ""]

    waived = [finding for finding in verdict.findings if finding.waiver]
    if waived:
        lines += [f"### Waived: {len(waived)}", ""]
        lines += [
            f"- {finding.package} {finding.version}, `{finding.waiver.id}`, {finding.rating}, "
            f"until {finding.waiver.until}: {finding.waiver.reason}"
            for finding in waived
        ]
        lines += [""]
    if verdict.unused:
        lines += ["### Waivers that match nothing (remove them)", ""]
        lines += [f"- `{waiver.id}`, until {waiver.until}" for waiver in verdict.unused]
        lines += [""]

    rest = [finding for finding in verdict.findings if not finding.blocks and not finding.waiver]
    if rest:
        lines += [f"### Reported, not blocking: {len(rest)}", "", *head]
        lines += [
            row(finding)
            for finding in sorted(rest, key=lambda f: (-ORDER.index(f.rating), f.lockfile, f.package))
        ]
        lines += [""]
    return "\n".join(lines)


def verdict_json(verdict: Verdict, scanner: str) -> dict:
    """The verdict as counts, for the release evidence (scripts/release/scan_summary.py).

    Counts and lockfile paths only: no package, advisory or waiver is named.
    """
    ratings = {rating: 0 for rating in reversed(ORDER)}
    for finding in verdict.findings:
        ratings[finding.rating] += 1
    return {
        "status": verdict.status,
        "scanner": scanner,
        "packages": dict(sorted(verdict.packages.items())),
        "ratings": ratings,
        "blocking": len(verdict.blocking),
        "waived": sum(1 for finding in verdict.findings if finding.waiver),
        "expired_waivers": len(verdict.expired),
        "unused_waivers": len(verdict.unused),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", required=True, type=Path, help="the scanner's JSON output")
    parser.add_argument("--waivers", required=True, type=Path, help="osv-scanner.toml")
    parser.add_argument(
        "--expect", action="append", required=True, help="a lockfile path the output must cover; give one per lockfile"
    )
    parser.add_argument("--strip-prefix", default="", help="removed from the front of each lockfile path in the output")
    parser.add_argument("--today", type=dt.date.fromisoformat, default=None, help="for tests; defaults to today, UTC")
    parser.add_argument("--json", type=Path, default=None,
                        help="also write the verdict as counts to this file (only when the scan can be trusted)")
    parser.add_argument("--scanner", default="", help="the scanner image, recorded in the --json file")
    arguments = parser.parse_args(argv)
    today = arguments.today or dt.datetime.now(dt.timezone.utc).date()

    try:
        waivers = load_waivers(arguments.waivers)
        results, packages = load_results(arguments.results, arguments.expect, arguments.strip_prefix)
    except Untrusted as error:
        print(f"OSV scan cannot be trusted: {error}", file=sys.stderr)
        return UNTRUSTED

    verdict = evaluate(results, waivers, today)
    verdict.packages = packages
    if arguments.json is not None:
        arguments.json.parent.mkdir(parents=True, exist_ok=True)
        arguments.json.write_text(json.dumps(verdict_json(verdict, arguments.scanner), indent=2) + "\n")
    text = report(verdict)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return verdict.status


if __name__ == "__main__":
    sys.exit(main())
