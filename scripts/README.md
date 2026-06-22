# Scripts

This directory ships a single tool for the open-source release: the **SCF catalogue importer**.

## SCF catalogue importer

`extract_scf_data.py` converts a user-supplied SCF controls workbook (`.xlsx`) into the JSON
the backend seeds on startup. The Secure Controls Framework content is licensed (CC BY-ND 4.0),
so the platform ships the importer **code only** — bring your own workbook.

Run it via the one-shot importer container:

```bash
docker compose --profile init run --rm catalog-importer
```

Mount your SCF `.xlsx` as described in the project README ("Bring your own SCF Excel catalogue").
The importer is version-agnostic (auto-detects the SCF release and resolves sheets dynamically).

`requirements-importer.txt` pins the importer's Python dependencies (pandas, openpyxl).
