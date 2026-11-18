# SPDX-License-Identifier: AGPL-3.0-only
"""Security objectives (clause 6.2) and ISMS metrics (clause 9.1).

* ``library``  — the eight live measures and their calculators.
* ``status``   — the performance status rules (pure).
* ``wording``  — plain-English text for metrics and targets (pure).
* ``starters`` — the twelve starter objectives.
* ``schemas``  — request and response shapes for the API.
* ``service``  — the database work used by the API, the nightly task and
  the document generator.

Import the submodules directly; this package imports nothing so that the
document generator can reach ``service`` lazily without an import cycle
(``library`` imports ``services.doc_gen.context``).
"""
