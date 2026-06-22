# Security Policy

We take the security of the SCF Controls Platform seriously. Thank you for helping keep the project and its users safe.

## Supported Versions

This repository is published as periodic sanitised snapshots from an upstream source repository. **Security support is provided for the latest released snapshot only.** If you are running an older snapshot, please update to the most recent release before reporting an issue, as it may already be fixed.

| Version                  | Supported          |
| ------------------------ | ------------------ |
| Latest released snapshot | :white_check_mark: |
| Older snapshots          | :x:                |

## Reporting a Vulnerability

**Please do not report security vulnerabilities through public GitHub issues, discussions, or pull requests.** Public disclosure before a fix is available puts users at risk.

Instead, please use one of the private channels below.

### Primary channel — GitHub private vulnerability reporting

Use GitHub's built-in private reporting:

1. Go to the **Security** tab of this repository.
2. Click **Report a vulnerability**.
3. Complete the advisory form with as much detail as you can.

This keeps the report private between you and the maintainers until a fix is ready.

### Fallback channel — email

If you cannot use GitHub private reporting, email **mark@compliancegenie.io**. Where possible, please avoid including sensitive proof-of-concept details in plain text until we have established a secure channel.

### What to include

To help us triage quickly, please include:

- A description of the vulnerability and its potential impact.
- The affected version (release snapshot) and component (backend, frontend, importer, deployment, etc.).
- Steps to reproduce, or a proof of concept.
- Any relevant logs, configuration, or screenshots.

## Our commitment

- We aim to **acknowledge your report within 5 business days** (best effort).
- We will keep you informed as we investigate and work on a fix.
- We will coordinate disclosure with you and credit you for the finding if you wish.

Because releases are produced from an upstream source repository, fixes are applied upstream and shipped in a subsequent release snapshot to this public repository.

There is currently **no paid bug-bounty programme** for this project. We are grateful nonetheless for responsible disclosure.
