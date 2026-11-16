# Source scans

The scans that ran on commit `febe4662cb579c345b4debd49aa658725f35c65d` of the private source repository, the commit this release was built from. Run ids refer to that repository's Actions runs: a reference for the maintainers, not a public link.

| Scan | Version | Outcome | Counts | Totals | Run |
| --- | --- | --- | --- | --- | --- |
| gitleaks | 8.24.3 | pass | leaks 0 | rules 208 | Gitleaks Secret Scan / Detect Secrets, run 43885359573 attempt 1 (push) |
| OSV-Scanner | — | error | — | — | CI / OSV Scan, run 43885359801 attempt 2 (push) |
| Semgrep | 1.184.0 | reported | error 0, warning 19, note 0 | rules 1074, suppressed in source 31 | Semgrep SAST / Static Analysis, run 43885359602 attempt 1 (push) |
| ZAP baseline | 2.17.0 | pass | high 0, medium 2, low 4, informational 4 | instances 33 | CI / Docker Build, run 43885359801 attempt 2 (push) |
| zizmor | — | error | — | — | CI / Workflow Lint, run 43885359801 attempt 2 (push) |

## What each scan covers

- **gitleaks** (`git: the commits the push added`): Secret detection over the commits the push to main added. Any secret found fails it.
- **OSV-Scanner** (—): Known vulnerabilities in the lockfiles. A high or critical finding with a fixed version fails it unless osv-scanner.toml waives it with a reason and a date; an expired waiver fails it.
- **Semgrep** (`.`): A full scan with the registry's auto rules. Report only on this commit: findings are counted and none fail it. Pull requests fail on findings on the lines they add.
- **ZAP baseline** (`http://localhost:5173`): A passive baseline scan of the frontend the CI stack serves. Any alert not dispositioned in .github/zap/rules.tsv fails it.
- **zizmor** (—): Offline lint of every workflow and action. Any finding not excused by an ignore comment with a reason fails it.

## Not a pass

- **OSV-Scanner**: error. the summary is from attempt 1, the job's latest is attempt 2.
- **zizmor**: error. the summary is from attempt 1, the job's latest is attempt 2.

Verdict: REPORT ONLY. This would block the release; the check blocks from 2026-12-14.
