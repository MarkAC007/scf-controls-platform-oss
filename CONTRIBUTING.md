# Contributing to SCF Controls Platform

Thank you for your interest in contributing. This is a self-hosted, Docker-only GRC platform (FastAPI + React + PostgreSQL) built on the Secure Controls Framework. Contributions of all kinds are welcome — bug reports, fixes, documentation improvements, and feature proposals.

Please take a moment to read this guide before you start, as it explains how this repository is published and what that means for your contributions.

## How this repository is published

**Important:** This public repository is **published from a separate private source repository.**

Each release is produced as a sanitised snapshot of the upstream source and lands here via an automated **release pull request into `main`**. In other words, the public tree is *downstream* of the source.

What this means for you:

- We review and discuss contributions **here**, in the public repository.
- When a change is accepted, a maintainer **carries it upstream** into the private source repository, where it is shipped in the next release.
- Because every release replaces the public tree with a fresh snapshot, a change merged directly into public `main` would otherwise be **overwritten by the next release sync**. Carrying accepted changes upstream is how we make sure your work survives and ships.

We mention this so the flow is transparent: your pull request is the proposal and the review record, and a maintainer is responsible for getting the accepted change into the next release.

## Code of Conduct

This project is governed by our [Code of Conduct](CODE_OF_CONDUCT.md). By participating, you are expected to uphold it. Please report unacceptable behaviour to **mark@compliancegenie.io**.

## Licensing of contributions

The software in this repository is licensed under the **GNU Affero General Public License v3.0 (AGPL-3.0)** — see [LICENSE](LICENSE).

By submitting a contribution, you agree that **your contribution is licensed under AGPL-3.0** on the same terms as the rest of the project.

### A note on SCF catalogue content

The Secure Controls Framework (SCF) catalogue content is licensed separately under **Creative Commons Attribution-NoDerivatives 4.0 (CC BY-ND 4.0)** and is **never committed to this repository.** The platform imports the SCF workbook at setup time (see the README). Please do not include SCF catalogue data, derived control text, or the workbook itself in any pull request.

## Filing a good issue

Before opening an issue, please search [existing issues](../../issues) to avoid duplicates.

Use the issue forms when you open a new issue:

- **Bug report** — for something that is broken. Include your platform version, how you deploy it, what happened, steps to reproduce, expected vs actual behaviour, and any relevant logs.
- **Feature request** — for a new capability or improvement. Describe the problem or motivation, your proposed solution, and any alternatives you considered.

Clear, reproducible reports are far easier to act on and are much more likely to be resolved quickly.

> **Security vulnerabilities:** please do **not** open a public issue. Follow the private disclosure process in [SECURITY.md](SECURITY.md).

## Running the platform locally

Local development and running the stack are documented in the [README](README.md). In short, the platform runs entirely under Docker Compose:

1. `cp .env.example .env` and fill in the values.
2. Run the importer to load the SCF workbook.
3. `docker compose up -d`.

Please refer to the README for the authoritative, up-to-date instructions rather than relying on this summary.

## Pull request flow

1. **Open an issue first** for anything non-trivial, so we can agree on the approach before you invest time.
2. **Fork the repository** and create a topic branch for your change.
3. **Keep changes focused and minimal.** One logical change per pull request. Avoid drive-by refactors and unrelated formatting churn — they make review harder and slow everything down.
4. **Run the stack** via the README and verify your change works end to end.
5. **Make sure checks pass.** Your branch must pass CI, and the **gitleaks** (secret scanning) and **semgrep** (static analysis) checks. Please run these locally or on your branch before requesting review.
6. **Update documentation** if your change alters behaviour, configuration, or the API.
7. **Open the pull request** using the pull request template and complete the checklist, including the statement that you agree to license your contribution under AGPL-3.0.

A maintainer will review your pull request, discuss any changes, and — once accepted — carry it upstream for inclusion in the next release.

## Questions

If you are unsure about anything, open a [Discussion](../../discussions) or a draft pull request and ask. We would rather help early than have you spend time heading in the wrong direction.
