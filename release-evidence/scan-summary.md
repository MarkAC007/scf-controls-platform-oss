# Source scans

The scans that ran on commit `bc26d327872d13f0f53b79c61b877e3ba0ba6dde` of the private source repository, the commit this release was built from. Run ids refer to that repository's Actions runs: a reference for the maintainers, not a public link.

| Scan | Version | Outcome | Counts | Totals | Run |
| --- | --- | --- | --- | --- | --- |
| gitleaks | 8.24.3 | pass | leaks 0 | rules 208 | Gitleaks Secret Scan / Detect Secrets, run 44070149875 attempt 1 (push) |
| OSV-Scanner | 2.7.0 | pass | critical 0, high 0, medium 6, low 3, unrated 0 | packages 1322, blocking 0, waived 0, expired waivers 0 | CI / OSV Scan, run 44070149858 attempt 1 (push) |
| Semgrep | 1.184.0 | reported | error 0, warning 19, note 0 | rules 1074, suppressed in source 31 | Semgrep SAST / Static Analysis, run 44070149857 attempt 1 (push) |
| ZAP baseline | 2.17.0 | pass | high 0, medium 2, low 4, informational 4 | instances 36 | CI / Docker Build, run 44070149858 attempt 1 (push) |
| zizmor | 1.32.0 | pass | high 0, medium 0, low 0, informational 0, unknown 0 | — | CI / Workflow Lint, run 44070149858 attempt 1 (push) |

## What each scan covers

- **gitleaks** (`git: the commits the push added`): Secret detection over the commits the push to main added. Any secret found fails it.
- **OSV-Scanner** (`backend/requirements-dev.txt`, `backend/requirements.txt`, `docs-site/package-lock.json`, `scripts/requirements-importer.txt`, `webclient/package-lock.json`): Known vulnerabilities in the lockfiles. A high or critical finding with a fixed version fails it unless osv-scanner.toml waives it with a reason and a date; an expired waiver fails it.
- **Semgrep** (`.`): A full scan with the registry's auto rules. Report only on this commit: findings are counted and none fail it. Pull requests fail on findings on the lines they add.
- **ZAP baseline** (`http://localhost:5173`): A passive baseline scan of the frontend the CI stack serves. Any alert not dispositioned in .github/zap/rules.tsv fails it.
- **zizmor** (`.`): Offline lint of every workflow and action. Any finding not excused by an ignore comment with a reason fails it.

Verdict: every scan passed or, where it only reports, completed.
