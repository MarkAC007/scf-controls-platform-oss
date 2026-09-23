#!/usr/bin/env python3
"""
Extract SCF v4 data from Excel catalog and convert to JSON format.

This script reads an SCF Excel catalog and produces:
- control_guidance.json: Main controls catalog
- erl.json: Evidence Request List
- controls_mapping.json: Framework mappings (legacy format)
- frameworks.json: Framework display names
- domains.json: Domain information

SCF extended fields:
- C|P-CMM Maturity Model (6 levels)
- Business Size Guidance (5 organization sizes)
- SCRM Focus (3 tiers)
- Risk/Threat Mapping (39 risk codes, 41 threat codes)
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

import pandas as pd
from collections import Counter


DOCKER_OUTPUT_DIR = Path('/app/data/json')
LOCAL_OUTPUT_DIR = Path('webclient/public/data')
CONTROL_SHEET_PATTERN = re.compile(r'^SCF\s+(\d{4}\.\d+)$')

# Column name constants for SCF extended fields
# Business Size Guidance columns
COL_BIZ_MICRO_SMALL = 'Possible Solutions & Considerations Micro-Small Business (<10 staff) BLS Firm Size Classes 1-2'
COL_BIZ_SMALL = 'Possible Solutions & Considerations Small Business (10-49 staff) BLS Firm Size Classes 3-4'
COL_BIZ_MEDIUM = 'Possible Solutions & Considerations Medium Business (50-249 staff) BLS Firm Size Classes 5-6'
COL_BIZ_LARGE = 'Possible Solutions & Considerations Large Business (250-999 staff) BLS Firm Size Classes 7-8'
COL_BIZ_ENTERPRISE = 'Possible Solutions & Considerations Enterprise (> 1,000 staff) BLS Firm Size Class 9'

# SCRM Focus columns (note: Excel has extra newlines that become single spaces when cleaned)
COL_SCRM_STRATEGIC = 'SCRM Focus TIER 1 STRATEGIC'
COL_SCRM_OPERATIONAL = 'SCRM Focus TIER 2 OPERATIONAL'
COL_SCRM_TACTICAL = 'SCRM Focus TIER 3 TACTICAL'

# CMM Maturity columns. SCF renamed these in the 2026.2 catalogue:
# 'C|P-CMM N <name>' -> 'SCR-CMM Level N <name>'. Candidates tried in order.
_CMM_LEVEL_NAMES = [
    'Not Performed',
    'Performed Informally',
    'Planned & Tracked',
    'Well Defined',
    'Quantitatively Controlled',
    'Continuously Improving',
]
COL_CMM_CANDIDATES = [
    [f'C|P-CMM {level} {name}', f'SCR-CMM Level {level} {name}']
    for level, name in enumerate(_CMM_LEVEL_NAMES)
]

# Legacy crosswalk column, introduced in the 2026.3 catalogue to carry the
# old->new control renumbering. Absent from 2026.2 and earlier, where every
# control simply keeps its id. The header is 'Legacy\nSCF #' in the workbook;
# clean_column_name collapses the newline.
COL_LEGACY_SCF_ID = 'Legacy SCF #'
LEGACY_NONE_SENTINEL = 'NONE'

# Risk/Threat Mapping columns
COL_RISK_SUMMARY = 'Risk Threat Summary'
COL_THREAT_SUMMARY = 'Control Threat Summary'

# Framework display-name columns. SCF renamed both the sheet ('Authoritative
# Sources' -> 'Focal Documents') and its columns in the 2026.2 catalogue:
# 'Mapping Column Header' -> 'SCF Column Header' and
# 'Authoritative Source - Law, Regulation or Framework (LRF)' ->
# 'Focal Document Name (FDN)'. Candidates are tried in order.
COL_FW_HEADER_CANDIDATES = [
    'Mapping Column Header',
    'SCF Column Header',
]
COL_FW_NAME_CANDIDATES = [
    'Authoritative Source - Law, Regulation or Framework (LRF)',
    'Focal Document Name (FDN)',
]
# The publisher's own stable identifier for a focal document, introduced on the
# Authoritative Sources sheet in 2026.1. It is the framework equivalent of the
# control sheet's 'Legacy SCF #' -- better, in fact, because it is a stable
# identity rather than a backward pointer: when 'US CA CCPA 2025' becomes
# 'USA California CCPA 2025' the mapping column header changes and the FDI does
# not. 2025.4 predates it, so every consumer must tolerate its absence.
COL_FW_FDI_CANDIDATES = [
    'Focal Document Identifier (FDI)',
]
COL_FW_GEOGRAPHY_CANDIDATES = [
    'Geography',
]

# Columns that sit inside the positional framework range but are NOT frameworks.
#
# The controls sheet has no end-of-frameworks marker, so the importer takes
# every column from the first framework column to the end of the sheet. That
# sweeps in the per-risk and per-threat likelihood columns, the two summary
# columns, and the per-release errata column — in every release, not just one.
# The cost is not cosmetic. Each becomes a framework id an organisation can be
# scoped to; each one whose name moves between releases ("Errata 2026.2" ->
# "Errata 2026.3") manufactures a phantom retirement plus a phantom addition;
# and the count of them changes per release (39 -> 65 "Risk R-*" in 2026.3),
# which inflates framework churn with pure noise. Measured on the four shipped
# workbooks: 86 / 86 / 87 / 114 pseudo-framework columns in 2025.4 / 2026.1 /
# 2026.2 / 2026.3 respectively, against 269 / 250 / 252 / 270 genuine ones —
# so better than a quarter of the positional slice was never a framework.
#
# These are structural families, not a denylist of specific names.
NON_FRAMEWORK_COLUMN_PATTERNS = [
    (re.compile(r'^risk\s+r-', re.I), 'risk_likelihood'),
    (re.compile(r'^threat\s+(mt|nt)-', re.I), 'threat_likelihood'),
    (re.compile(r'^(risk|control)\s+threat\s+summary$', re.I), 'risk_threat_summary'),
    (re.compile(r'^errata\b', re.I), 'errata'),
    # SCF's own requirement-tier designators, not authoritative source documents.
    # The Focal Documents sheet does not list them in any release 2025.4-2026.3,
    # and every runtime consumer already hides them behind its own denylist
    # (`identify_`, `minimum_security_requirements_mcr_dsr`).
    (re.compile(r'^identify\s+(minimum\s+compliance|discretionary\s+security)\s+requirements',
                re.I), 'requirement_tier'),
    (re.compile(r'^minimum\s+security\s+requirements\b', re.I), 'requirement_tier'),
]


def non_framework_reason(column: str) -> str | None:
    """Which non-framework family ``column`` belongs to, or None."""
    return next(
        (why for rx, why in NON_FRAMEWORK_COLUMN_PATTERNS if rx.search(column)),
        None,
    )


# The same families as NON_FRAMEWORK_COLUMN_PATTERNS, matched against a
# NORMALISED id rather than a raw column header. Needed because the live side
# of a diff has only ids: a platform seeded before the column partition existed
# carries 'risk_r_1' and 'errata_2026_2' in its framework mappings, and those
# must not be reported as framework retirements when a clean extraction drops
# them. They were never frameworks; their disappearance is a correction.
NON_FRAMEWORK_ID_PATTERNS = [
    (re.compile(r'^risk_r_'), 'risk_likelihood'),
    (re.compile(r'^threat_(mt|nt)_'), 'threat_likelihood'),
    (re.compile(r'^(risk|control)_threat_summary$'), 'risk_threat_summary'),
    (re.compile(r'^errata(_|$)'), 'errata'),
    # The requirement-tier family, which the column patterns also exclude. Its
    # three ids are stable across 2025.4-2026.3 and are spelled out rather than
    # matched loosely, because a bare `^identify_` prefix would swallow any
    # genuine focal document whose name begins with that word.
    (re.compile(r'^identify_(minimum_compliance|discretionary_security)'
                r'_requirements(_|$)'), 'requirement_tier'),
    (re.compile(r'^minimum_security_requirements(_|$)'), 'requirement_tier'),
]


def non_framework_id_reason(framework_id: str) -> str | None:
    """Which non-framework family ``framework_id`` belongs to, or None."""
    return next(
        (why for rx, why in NON_FRAMEWORK_ID_PATTERNS if rx.match(framework_id)),
        None,
    )


def partition_framework_columns(
    candidate_columns: list,
    focal_document_headers: set | None = None,
) -> tuple[list, dict]:
    """Split the positional range into framework columns and exclusions.

    A column is excluded when it matches a ``NON_FRAMEWORK_COLUMN_PATTERNS``
    family AND the workbook's own Focal Documents / Authoritative Sources sheet
    does not list it as a mapping column. That second clause is the safety
    valve: the sheet is the workbook's own declaration of what a framework is,
    so it overrides our pattern wherever the two disagree. Verified across all
    four shipped workbooks — the sheet lists no column matching any pattern, so
    the veto never fires on today's data, but a future release that promotes one
    of these shapes to a genuine focal document keeps working.

    Returns ``(framework_columns, {excluded_column: reason})``.
    """
    focal_document_headers = focal_document_headers or set()
    kept, excluded = [], {}
    for col in candidate_columns:
        reason = non_framework_reason(col)
        if reason and col not in focal_document_headers:
            excluded[col] = reason
        else:
            kept.append(col)
    return kept, excluded


def framework_column_start_index(columns: list) -> int:
    """First column of the positional framework range on a controls sheet.

    Framework mapping columns follow the ~25 control-metadata columns; the
    AICPA/TSC column is the reliable landmark for where they begin, with a
    positional fallback for a workbook that drops it.
    """
    for i, col in enumerate(columns):
        if 'AICPA' in col or 'TSC' in col:
            return i
    return 24  # Default fallback


def framework_columns_in(
    columns: list,
    focal_document_headers: set | None = None,
) -> tuple[list, dict]:
    """``(framework_columns, {excluded: reason})`` for a cleaned header list.

    The one implementation of "which columns of a controls sheet are
    frameworks", shared by ``extract_controls`` and ``framework_columns_for``.
    """
    positional = columns[framework_column_start_index(columns):]
    return partition_framework_columns(positional, focal_document_headers)


def framework_columns_for(
    xl: pd.ExcelFile,
    sheet_name: str,
    focal_document_headers: set | None = None,
) -> list:
    """The framework mapping columns of a controls sheet, without extracting it.

    For callers that want only the framework registry: reading the header row
    is cheap, parsing 1,451 control rows is not.
    """
    df = pd.read_excel(xl, sheet_name, nrows=0)
    columns = [clean_column_name(c) for c in df.columns]
    framework_columns, _excluded = framework_columns_in(columns, focal_document_headers)
    return framework_columns


def read_focal_document_headers(xl: pd.ExcelFile, sheet_name: str) -> set:
    """The mapping-column headers the workbook itself declares as frameworks."""
    try:
        df = pd.read_excel(xl, sheet_name)
    except Exception:  # pragma: no cover - an unreadable sheet is not fatal
        return set()
    df.columns = [clean_column_name(c) for c in df.columns]
    header_col = next(
        (c for c in COL_FW_HEADER_CANDIDATES if c in df.columns), None
    )
    if header_col is None:
        return set()
    return {
        str(v).strip()
        for v in df[header_col].dropna()
        if str(v).strip() and str(v).strip().lower() != 'nan'
    }


# Domains principle column, renamed in the 2026.2 catalogue. Candidates tried
# in order.
COL_DOMAIN_PRINCIPLE_CANDIDATES = [
    'Cybersecurity & Data Privacy by Design (C|P) Principles',
    'Security, Compliance & Resilience (SCR) Principles',
]

# Evidence Request List artifact columns, renamed in the 2026.3 catalogue
# ('Documentation Artifact' -> 'ERL Artifact', 'Artifact Description' ->
# 'Evidence Request List (ERL) Artifact Description'). Candidates tried in
# order; a missing column silently emptied every artifact title and
# description in 2026.3, so absence is warned about rather than tolerated.
COL_ERL_TITLE_CANDIDATES = [
    'Documentation Artifact',
    'ERL Artifact',
]
COL_ERL_DESCRIPTION_CANDIDATES = [
    'Artifact Description',
    'Evidence Request List (ERL) Artifact Description',
]

# Assessment Objectives columns
COL_AO_SCF_ID = 'SCF #'
COL_AO_ID = 'SCF AO #'
COL_AO_TEXT = 'SCF Assessment Objective (AO) In addition to relevant policies, standards and procedures, the assessor shall examine, interview, and/or test to determine if appropriately scoped evidence exists to support the claim that:'
COL_AO_PPTDF = 'PPTDF Applicability'
COL_AO_ORIGINS = 'SCF Assessment Objective (AO) Origin(s)'
COL_AO_NOTES = 'Notes / Errata'
COL_AO_RIGOR = 'Assessment Rigor (AR)'
COL_AO_SDP = 'SCF Defined Parameters (SDP)'
COL_AO_ODP = 'Organization Defined Parameters (ODP)'
COL_AO_CMMC_L1 = 'CMMC Level 1 AOs'
COL_AO_DHS_ZTCF = 'DHS ZTCF AOs'
COL_AO_NIST_53A = 'NIST 800-53A'
COL_AO_NIST_171A = 'NIST 800-171A'
COL_AO_NIST_171A_R3 = 'NIST 800-171A R3'
COL_AO_NIST_172A = 'NIST 800-172A'
COL_AO_ASSET_TYPE = 'Asset Type examine/interview/test'
COL_AO_PROCEDURE = 'Assessment Procedure'
COL_AO_EXPECTED = 'Expected Result(s)'


def clean_column_name(col: str) -> str:
    """Clean column name by removing newlines and extra whitespace."""
    return re.sub(r'\s+', ' ', col.strip().replace('\n', ' '))


def format_available_sheets(xl: pd.ExcelFile) -> str:
    """Format available sheet names for error output."""
    return ', '.join(repr(sheet) for sheet in xl.sheet_names)


def resolve_sheet(xl: pd.ExcelFile, pattern_or_name: str | re.Pattern[str]) -> str:
    """Resolve a sheet by exact name or regex pattern."""
    if isinstance(pattern_or_name, str):
        if pattern_or_name in xl.sheet_names:
            return pattern_or_name
        target = repr(pattern_or_name)
    else:
        for sheet_name in xl.sheet_names:
            if pattern_or_name.search(sheet_name):
                return sheet_name
        target = pattern_or_name.pattern

    raise ValueError(
        f"Could not resolve required sheet {target}. "
        f"Available sheets: {format_available_sheets(xl)}"
    )


def resolve_sheet_with_contains_fallback(
    xl: pd.ExcelFile,
    preferred_names: str | list[str],
    contains_texts: str | list[str],
) -> str:
    """Resolve a sheet by exact name(s), then case-insensitive contains fallback.

    ``preferred_names`` / ``contains_texts`` accept either a single string or a
    list of candidates, tried in order. A list lets one logical sheet survive SCF
    workbook renames (e.g. 'Authoritative Sources' → 'Focal Documents' in 2026.2).
    """
    if isinstance(preferred_names, str):
        preferred_names = [preferred_names]
    if isinstance(contains_texts, str):
        contains_texts = [contains_texts]

    for name in preferred_names:
        if name in xl.sheet_names:
            return name

    lowered_sheets = [(s, s.lower()) for s in xl.sheet_names]
    for contains_text in contains_texts:
        needle = contains_text.lower()
        for sheet_name, lowered in lowered_sheets:
            if needle in lowered:
                return sheet_name

    raise ValueError(
        f"Could not resolve required sheet from {preferred_names!r} "
        f"or a sheet containing any of {contains_texts!r}. "
        f"Available sheets: {format_available_sheets(xl)}"
    )


def detect_catalog_version(xl: pd.ExcelFile) -> tuple[str, str]:
    """Detect catalog version from the main SCF controls sheet."""
    for sheet_name in xl.sheet_names:
        match = CONTROL_SHEET_PATTERN.match(sheet_name)
        if match:
            return match.group(1), sheet_name

    raise ValueError(
        "Could not detect SCF catalog version from a sheet named "
        f"'SCF <version>'. Available sheets: {format_available_sheets(xl)}"
    )


def resolve_catalog_sheets(xl: pd.ExcelFile) -> dict[str, str]:
    """Resolve all required workbook sheets."""
    catalog_version, controls_sheet = detect_catalog_version(xl)
    evidence_pattern = re.compile(
        rf'^Evidence Request List\s+{re.escape(catalog_version)}$'
    )
    assessment_pattern = re.compile(
        rf'^Assessment Objectives\s+{re.escape(catalog_version)}$'
    )

    return {
        'catalog_version': catalog_version,
        'controls': controls_sheet,
        'evidence': resolve_sheet(xl, evidence_pattern),
        'domains': resolve_sheet_with_contains_fallback(
            xl, 'SCF Domains & Principles', 'Domains'
        ),
        'assessment_objectives': resolve_sheet(xl, assessment_pattern),
        # SCF renamed this sheet 'Authoritative Sources' -> 'Focal Documents' in
        # the 2026.2 catalogue; accept either so old and new workbooks both import.
        'authoritative_sources': resolve_sheet_with_contains_fallback(
            xl,
            ['Authoritative Sources', 'Focal Documents'],
            ['Authoritative Sources', 'Focal Documents'],
        ),
    }


def default_output_dir() -> Path:
    """Return the preferred output directory for extracted JSON files."""
    parent = DOCKER_OUTPUT_DIR.parent
    if DOCKER_OUTPUT_DIR.exists() or (parent.exists() and os.access(parent, os.W_OK)):
        return DOCKER_OUTPUT_DIR
    return LOCAL_OUTPUT_DIR


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description='Extract SCF catalog data from an Excel workbook.'
    )
    parser.add_argument(
        'excel_path',
        nargs='?',
        default='temp/scf-2025-4.xlsx',
        help='Path to the SCF Excel workbook.',
    )
    parser.add_argument(
        '--output-dir',
        default=None,
        help='Directory where extracted JSON files should be written.',
    )
    return parser.parse_args()


def parse_pptdf(pptdf_str: str | None) -> dict:
    """Parse PPTDF applicability string into boolean flags.

    Each control has exactly one primary PPTDF applicability value.
    """
    result = {
        'people': False,
        'process': False,
        'technology': False,
        'data': False,
        'facility': False
    }
    if not pptdf_str or pd.isna(pptdf_str):
        return result

    pptdf_str = str(pptdf_str).strip().lower()
    if pptdf_str == 'people':
        result['people'] = True
    elif pptdf_str == 'process':
        result['process'] = True
    elif pptdf_str == 'technology':
        result['technology'] = True
    elif pptdf_str == 'data':
        result['data'] = True
    elif pptdf_str == 'facility':
        result['facility'] = True

    return result


def parse_erl_refs(erl_str: str | None) -> list:
    """Parse Evidence Request List references from comma-separated string."""
    if not erl_str or pd.isna(erl_str):
        return []

    # Split by common delimiters
    refs = re.split(r'[,;\n]+', str(erl_str))
    return [ref.strip() for ref in refs if ref.strip()]


def parse_legacy_ids(legacy_str: str | None) -> list:
    """Parse the ``Legacy SCF #`` crosswalk cell into predecessor SCF ids.

    SCF ships this column from 2026.3 onward to carry the old->new control
    renumbering. A cell holds one predecessor, several (newline-separated —
    a merge, where two retired controls collapse into this one), or the
    literal sentinel ``NONE`` meaning the control has no predecessor and is
    genuinely new. ``NONE`` yields an empty list so callers can treat
    "no predecessor" and "column absent" identically.
    """
    if legacy_str is None or pd.isna(legacy_str):
        return []

    ids = re.split(r'[,;\n]+', str(legacy_str))
    return [
        cleaned
        for cleaned in (ref.strip() for ref in ids)
        if cleaned and cleaned.upper() != LEGACY_NONE_SENTINEL
    ]


def parse_control_mappings(control_str: str | None) -> list:
    """Parse control mappings from string."""
    if not control_str or pd.isna(control_str):
        return []

    # Split by common delimiters
    mappings = re.split(r'[,;\n]+', str(control_str))
    return [m.strip() for m in mappings if m.strip()]


def parse_framework_refs(ref_str: str | None) -> list:
    """Parse framework references from newline or comma-separated string."""
    if not ref_str or pd.isna(ref_str):
        return []

    # Split by newlines first, then commas
    refs = []
    for line in str(ref_str).split('\n'):
        for ref in line.split(','):
            ref = ref.strip()
            if ref:
                refs.append(ref)

    return refs


def parse_cmm_maturity(row: pd.Series, col_map: dict) -> dict | None:
    """Parse C|P-CMM Maturity guidance from row.

    Returns a dictionary with level_0 through level_5, or None if no data.
    """
    result = {}
    has_data = False

    for level, candidates in enumerate(COL_CMM_CANDIDATES):
        key = f'level_{level}'
        # Resolve the era-specific column name (2025.x vs 2026.2 layouts)
        clean_col = next(
            (col_map[c] for c in candidates if c in col_map), None
        )
        if clean_col and clean_col in row.index:
            value = row.get(clean_col)
            if not pd.isna(value) and str(value).strip():
                result[key] = str(value).strip()
                has_data = True

    return result if has_data else None


def parse_business_size_guidance(row: pd.Series, col_map: dict) -> dict | None:
    """Parse Business Size Guidance from row.

    Returns a dictionary with micro_small, small, medium, large, enterprise, or None if no data.
    """
    result = {}
    has_data = False

    size_cols = [
        (COL_BIZ_MICRO_SMALL, 'micro_small'),
        (COL_BIZ_SMALL, 'small'),
        (COL_BIZ_MEDIUM, 'medium'),
        (COL_BIZ_LARGE, 'large'),
        (COL_BIZ_ENTERPRISE, 'enterprise'),
    ]

    for col_name, key in size_cols:
        clean_col = col_map.get(col_name)
        if clean_col and clean_col in row.index:
            value = row.get(clean_col)
            if not pd.isna(value) and str(value).strip():
                result[key] = str(value).strip()
                has_data = True

    return result if has_data else None


def parse_scrm_focus(row: pd.Series, col_map: dict) -> dict | None:
    """Parse SCRM Focus tiers from row.

    Returns a dictionary with tier1_strategic, tier2_operational, tier3_tactical as booleans.
    """
    result = {}
    has_data = False

    # SCRM columns contain 'X' or similar markers when applicable
    tier_cols = [
        (COL_SCRM_STRATEGIC, 'tier1_strategic'),
        (COL_SCRM_OPERATIONAL, 'tier2_operational'),
        (COL_SCRM_TACTICAL, 'tier3_tactical'),
    ]

    for col_name, key in tier_cols:
        clean_col = col_map.get(col_name)
        if clean_col and clean_col in row.index:
            value = row.get(clean_col)
            if not pd.isna(value) and str(value).strip():
                # Any non-empty value indicates this tier applies
                result[key] = True
                has_data = True
            else:
                result[key] = False

    return result if has_data else None


def parse_risk_threat_mapping(row: pd.Series, col_map: dict) -> dict | None:
    """Parse Risk and Threat code mappings from row.

    Returns a dictionary with risk_codes and threat_codes arrays, or None if no data.
    Risk codes are in format R-XX-N (e.g., R-AC-1, R-GV-3)
    Threat codes are in format NT-N or MT-N (e.g., NT-1, MT-15)
    """
    result = {}
    has_data = False

    # Parse risk codes from Risk Threat Summary column
    risk_col = col_map.get(COL_RISK_SUMMARY)
    if risk_col and risk_col in row.index:
        risk_value = row.get(risk_col)
        if not pd.isna(risk_value) and str(risk_value).strip():
            # Split by newlines and filter valid risk codes
            risk_codes = [code.strip() for code in str(risk_value).split('\n') if code.strip()]
            # Validate format: R-XX-N
            risk_codes = [c for c in risk_codes if re.match(r'^R-[A-Z]{2}-\d+$', c)]
            if risk_codes:
                result['risk_codes'] = risk_codes
                has_data = True

    # Parse threat codes from Control Threat Summary column
    threat_col = col_map.get(COL_THREAT_SUMMARY)
    if threat_col and threat_col in row.index:
        threat_value = row.get(threat_col)
        if not pd.isna(threat_value) and str(threat_value).strip():
            # Split by newlines and filter valid threat codes
            threat_codes = [code.strip() for code in str(threat_value).split('\n') if code.strip()]
            # Validate format: NT-N or MT-N
            threat_codes = [c for c in threat_codes if re.match(r'^[NM]T-\d+$', c)]
            if threat_codes:
                result['threat_codes'] = threat_codes
                has_data = True

    return result if has_data else None


def normalize_framework_id(col_name: str) -> str:
    """Convert framework column name to a normalized ID."""
    # Remove newlines and clean up
    name = clean_column_name(col_name)

    # Create a slug-like ID
    slug = re.sub(r'[^a-zA-Z0-9]+', '_', name.lower())
    slug = re.sub(r'_+', '_', slug).strip('_')

    return slug


def extract_controls(
    xl: pd.ExcelFile,
    sheet_name: str,
    focal_document_headers: set | None = None,
) -> tuple[list, dict, list, dict]:
    """Extract controls from SCF sheet."""
    print("Reading controls sheet...")
    df = pd.read_excel(xl, sheet_name)

    # Store original column names before cleaning for extended field mapping
    original_columns = list(df.columns)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    # Create mapping from original (with newlines) to cleaned column names
    # This helps us find the extended field columns
    col_map = {}
    for orig, cleaned in zip(original_columns, df.columns):
        # Map the constant names to cleaned column names
        orig_cleaned = clean_column_name(orig)
        col_map[orig_cleaned] = cleaned

    framework_start_idx = framework_column_start_index(list(df.columns))
    framework_columns, excluded_columns = framework_columns_in(
        list(df.columns), focal_document_headers
    )
    print(
        f"Found {len(framework_columns)} framework columns starting at index "
        f"{framework_start_idx}"
    )
    if excluded_columns:
        tally = Counter(excluded_columns.values())
        breakdown = ', '.join(f'{n} {why}' for why, n in sorted(tally.items()))
        print(
            f"  excluded {len(excluded_columns)} non-framework columns from the "
            f"framework range ({breakdown})"
        )

    controls = []
    all_framework_mappings = {}

    for _, row in df.iterrows():
        scf_id = str(row.get('SCF #', '')).strip()
        if not scf_id or pd.isna(row.get('SCF #')):
            continue

        # Parse PPTDF applicability
        pptdf = parse_pptdf(row.get('PPTDF Applicability'))

        # Parse evidence requests
        evidence_requests = parse_erl_refs(row.get('Evidence Request List (ERL) #'))

        # Parse NIST CSF function
        nist_csf = str(row.get('NIST CSF Function Grouping', '')).strip()
        if pd.isna(row.get('NIST CSF Function Grouping')) or not nist_csf:
            nist_csf = None

        # Parse control weighting
        weighting = row.get('Relative Control Weighting')
        if pd.isna(weighting):
            weighting = None
        else:
            try:
                weighting = int(weighting)
            except (ValueError, TypeError):
                weighting = None

        # Build framework mappings for this control
        framework_mappings = {}
        for fw_col in framework_columns:
            refs = parse_framework_refs(row.get(fw_col))
            if refs:
                fw_id = normalize_framework_id(fw_col)
                framework_mappings[fw_id] = refs

        # Store in separate mappings dict for legacy format
        if framework_mappings:
            all_framework_mappings[scf_id] = framework_mappings

        # Parse SCF extended fields
        cmm_maturity = parse_cmm_maturity(row, col_map)
        business_size_guidance = parse_business_size_guidance(row, col_map)
        scrm_focus = parse_scrm_focus(row, col_map)
        risk_threat_mapping = parse_risk_threat_mapping(row, col_map)

        control = {
            'scf_id': scf_id,
            'legacy_scf_ids': parse_legacy_ids(row.get(COL_LEGACY_SCF_ID)),
            'scf_domain': str(row.get('SCF Domain', '')).strip(),
            'control_name': str(row.get('SCF Control', '')).strip(),
            'control_description': str(row.get('Secure Controls Framework (SCF) Control Description', '')).strip(),
            'control_question': str(row.get('SCF Control Question', '')).strip() if not pd.isna(row.get('SCF Control Question')) else None,
            'validation_cadence': str(row.get('Conformity Validation Cadence', '')).strip() if not pd.isna(row.get('Conformity Validation Cadence')) else None,
            'control_weighting': weighting,
            'nist_csf_function': nist_csf,
            'pptdf_applicability': pptdf,
            'evidence_requests': evidence_requests,
            'framework_mappings': framework_mappings,
            # SCF extended fields
            'cmm_maturity': cmm_maturity,
            'business_size_guidance': business_size_guidance,
            'scrm_focus': scrm_focus,
            'risk_threat_mapping': risk_threat_mapping,
        }

        controls.append(control)

    print(f"Extracted {len(controls)} controls")
    return controls, all_framework_mappings, framework_columns, excluded_columns


def extract_evidence(xl: pd.ExcelFile, sheet_name: str) -> dict:
    """Extract Evidence Request List."""
    print("Reading evidence request list...")
    df = pd.read_excel(xl, sheet_name)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    title_col = next((c for c in COL_ERL_TITLE_CANDIDATES if c in df.columns), None)
    desc_col = next(
        (c for c in COL_ERL_DESCRIPTION_CANDIDATES if c in df.columns), None
    )
    if title_col is None:
        print(
            f"  WARNING: no artifact title column on '{sheet_name}' "
            f"(looked for {COL_ERL_TITLE_CANDIDATES}); titles will be empty"
        )
    if desc_col is None:
        print(
            f"  WARNING: no artifact description column on '{sheet_name}' "
            f"(looked for {COL_ERL_DESCRIPTION_CANDIDATES}); "
            f"descriptions will be empty"
        )

    def cell(row, col):
        if col is None:
            return ''
        value = row.get(col)
        return '' if pd.isna(value) else str(value).strip()

    evidence = {}
    for _, row in df.iterrows():
        erl_id = str(row.get('ERL #', '')).strip()
        if not erl_id or pd.isna(row.get('ERL #')):
            continue

        evidence[erl_id] = {
            'evidence_id': erl_id,
            'area_of_focus': str(row.get('Area of Focus', '')).strip() if not pd.isna(row.get('Area of Focus')) else '',
            'artifact_title': cell(row, title_col),
            'artifact_description': cell(row, desc_col),
            'control_mappings': parse_control_mappings(row.get('SCF Control Mappings'))
        }

    print(f"Extracted {len(evidence)} evidence items")
    return evidence


def extract_domains(xl: pd.ExcelFile, sheet_name: str) -> list:
    """Extract domain information."""
    print("Reading domains...")
    df = pd.read_excel(xl, sheet_name)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    # Resolve the era-specific principle column name (2025.x vs 2026.2 layouts)
    principle_col = next(
        (c for c in COL_DOMAIN_PRINCIPLE_CANDIDATES if c in df.columns), None
    )
    if principle_col is None:
        print(
            f"Warning: sheet {sheet_name!r} has no recognised principles column "
            f"(looked for {COL_DOMAIN_PRINCIPLE_CANDIDATES}); principle will be empty"
        )

    domains = []
    for _, row in df.iterrows():
        domain_name = str(row.get('SCF Domain', '')).strip()
        if not domain_name or pd.isna(row.get('SCF Domain')):
            continue

        # Handle # column which may have non-breaking spaces
        order = row.iloc[0]  # First column is the order number
        if pd.isna(order):
            order = len(domains) + 1
        else:
            try:
                order = int(str(order).strip().replace('\xa0', ''))
            except ValueError:
                order = len(domains) + 1

        domains.append({
            'order': order,
            'name': domain_name,
            'identifier': str(row.get('SCF Identifier', '')).strip(),
            'principle': str(row.get(principle_col, '')).strip() if principle_col and not pd.isna(row.get(principle_col)) else '',
            'principle_intent': str(row.get('Principle Intent', '')).strip() if not pd.isna(row.iloc[4]) else ''
        })

    print(f"Extracted {len(domains)} domains")
    return domains


def extract_assessment_objectives(xl: pd.ExcelFile, sheet_name: str) -> list:
    """Extract Assessment Objectives from SCF sheet."""
    print("Reading assessment objectives...")
    df = pd.read_excel(xl, sheet_name)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    objectives = []
    for _, row in df.iterrows():
        ao_id = str(row.get(clean_column_name(COL_AO_ID), '')).strip()
        if not ao_id or pd.isna(row.get(clean_column_name(COL_AO_ID))):
            continue

        scf_id = str(row.get(clean_column_name(COL_AO_SCF_ID), '')).strip()

        # Parse PPTDF applicability
        pptdf = parse_pptdf(row.get(clean_column_name(COL_AO_PPTDF)))

        # Parse assessment rigor (numeric)
        rigor = row.get(clean_column_name(COL_AO_RIGOR))
        if pd.isna(rigor):
            rigor = None
        else:
            try:
                rigor = int(rigor)
            except (ValueError, TypeError):
                rigor = None

        # Helper to get string value or None
        def get_str(col_const):
            col_name = clean_column_name(col_const)
            val = row.get(col_name)
            if pd.isna(val) or not str(val).strip():
                return None
            return str(val).strip()

        objective = {
            'ao_id': ao_id,
            'scf_id': scf_id,
            'objective_text': get_str(COL_AO_TEXT) or '',
            'pptdf_applicability': pptdf,
            'ao_origins': get_str(COL_AO_ORIGINS),
            'notes': get_str(COL_AO_NOTES),
            'assessment_rigor': rigor,
            'scf_defined_parameters': get_str(COL_AO_SDP),
            'org_defined_parameters': get_str(COL_AO_ODP),
            'cmmc_level1_ao': get_str(COL_AO_CMMC_L1),
            'dhs_ztcf_ao': get_str(COL_AO_DHS_ZTCF),
            'nist_800_53a': get_str(COL_AO_NIST_53A),
            'nist_800_171a': get_str(COL_AO_NIST_171A),
            'nist_800_171a_r3': get_str(COL_AO_NIST_171A_R3),
            'nist_800_172a': get_str(COL_AO_NIST_172A),
            'asset_type': get_str(COL_AO_ASSET_TYPE),
            'assessment_procedure': get_str(COL_AO_PROCEDURE),
            'expected_results': get_str(COL_AO_EXPECTED),
        }

        objectives.append(objective)

    print(f"Extracted {len(objectives)} assessment objectives")
    return objectives


def extract_framework_names(
    xl: pd.ExcelFile,
    framework_columns: list,
    sheet_name: str,
) -> dict:
    """Extract framework display names from the Authoritative Sources /
    Focal Documents sheet (renamed in SCF 2026.2)."""
    print("Reading framework names...")
    df = pd.read_excel(xl, sheet_name)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    # Resolve era-specific column names (2025.x vs 2026.2 layouts)
    header_col = next((c for c in COL_FW_HEADER_CANDIDATES if c in df.columns), None)
    name_col = next((c for c in COL_FW_NAME_CANDIDATES if c in df.columns), None)
    if header_col is None or name_col is None:
        print(
            f"Warning: sheet {sheet_name!r} has no recognised framework-name "
            f"columns (looked for {COL_FW_HEADER_CANDIDATES} / "
            f"{COL_FW_NAME_CANDIDATES}); falling back to column headers"
        )

    # Create mapping from column header to friendly name
    framework_names = {}

    if header_col is not None:
        for _, row in df.iterrows():
            col_header = str(row.get(header_col, '')).strip()
            if not col_header or pd.isna(row.get(header_col)):
                continue

            # Get the full authoritative source / focal document name
            source_name = ''
            if name_col is not None and not pd.isna(row.get(name_col)):
                source_name = str(row.get(name_col, '')).strip()
            if not source_name:
                source_name = clean_column_name(col_header)

            # Normalize the column header to match our framework IDs
            fw_id = normalize_framework_id(col_header)
            framework_names[fw_id] = source_name

    # Also add entries for the framework columns we found. ``framework_columns``
    # has already had the non-framework families stripped out by
    # ``partition_framework_columns``; the guard below is belt-and-braces so a
    # future caller that passes the raw positional slice cannot reintroduce
    # pseudo-frameworks through this door.
    for col in framework_columns:
        if non_framework_reason(col):
            continue
        fw_id = normalize_framework_id(col)
        if fw_id not in framework_names:
            framework_names[fw_id] = clean_column_name(col)

    print(f"Extracted {len(framework_names)} framework names")
    return framework_names


def extract_framework_registry(
    xl: pd.ExcelFile,
    framework_names: dict,
    sheet_name: str,
) -> dict:
    """The framework registry, keyed by framework id.

    ``frameworks.json`` is ``{id: display_name}`` and is consumed by the
    webclient, so its shape is fixed. This is the richer sidecar: it carries the
    publisher's Focal Document Identifier alongside the name, which is what lets
    a later release's diff recognise ``usa_california_ccpa_2025`` as the same
    document as ``us_ca_ccpa_2025`` instead of an unrelated addition.

    Every id in ``framework_names`` appears here. ``focal_document_id`` is None
    for ids the sheet does not list, and for every id in a pre-2026.1 workbook
    where the column does not exist at all.
    """
    registry = {
        fw_id: {'name': name, 'focal_document_id': None, 'geography': None}
        for fw_id, name in framework_names.items()
    }
    try:
        df = pd.read_excel(xl, sheet_name)
    except Exception:  # pragma: no cover - an unreadable sheet is not fatal
        return registry
    df.columns = [clean_column_name(c) for c in df.columns]
    header_col = next((c for c in COL_FW_HEADER_CANDIDATES if c in df.columns), None)
    fdi_col = next((c for c in COL_FW_FDI_CANDIDATES if c in df.columns), None)
    geo_col = next((c for c in COL_FW_GEOGRAPHY_CANDIDATES if c in df.columns), None)
    if header_col is None or fdi_col is None:
        print(
            f"Note: sheet {sheet_name!r} carries no focal-document identifier "
            f"column; framework succession for this workbook falls back to "
            f"derived matching"
        )
        return registry

    for _, row in df.iterrows():
        header = row.get(header_col)
        if pd.isna(header) or not str(header).strip():
            continue
        fw_id = normalize_framework_id(str(header))
        if fw_id not in registry:
            continue
        fdi = row.get(fdi_col)
        if not pd.isna(fdi) and str(fdi).strip():
            registry[fw_id]['focal_document_id'] = str(fdi).strip()
        if geo_col is not None and not pd.isna(row.get(geo_col)):
            registry[fw_id]['geography'] = str(row.get(geo_col)).strip()

    with_fdi = sum(1 for v in registry.values() if v['focal_document_id'])
    print(
        f"Framework registry: {len(registry)} entries, {with_fdi} carrying a "
        f"focal-document identifier"
    )
    return registry


def extract_framework_registry_only(excel_path) -> tuple[str, dict]:
    """``(catalog_version, framework_registry)`` for one workbook.

    The registry alone — no controls, no evidence, nothing written to disk. Used
    by ``cli.admin backfill-framework-registry`` to populate
    ``catalog_framework_registries`` on an install that was seeded before the
    table existed, where the live catalogue rows are already correct and only
    the focal-document identifiers are missing.
    """
    xl = pd.ExcelFile(excel_path)
    sheets = resolve_catalog_sheets(xl)
    focal_headers = read_focal_document_headers(xl, sheets['authoritative_sources'])
    framework_columns = framework_columns_for(
        xl, sheets['controls'], focal_headers
    )
    framework_names = extract_framework_names(
        xl, framework_columns, sheets['authoritative_sources']
    )
    registry = extract_framework_registry(
        xl, framework_names, sheets['authoritative_sources']
    )
    return str(sheets['catalog_version']), registry


def extract_to_dir(excel_path, output_dir):
    """Extract an SCF Excel workbook into seeder JSON in ``output_dir``.

    Importable entry point shared by the CLI ``main()`` below and the backend's
    live catalogue-import Celery task (backend/tasks_catalog.py). Raises
    ``ValueError`` if the workbook has no recognisable SCF catalogue sheet.
    Returns the ``catalog_meta`` dict (counts + resolved version).
    """
    excel_path = Path(excel_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Reading SCF catalog from: {excel_path}")
    xl = pd.ExcelFile(excel_path)
    sheet_names = resolve_catalog_sheets(xl)

    catalog_version = sheet_names['catalog_version']

    # Extract all data
    focal_document_headers = read_focal_document_headers(
        xl, sheet_names['authoritative_sources']
    )
    controls, framework_mappings, framework_columns, excluded_columns = extract_controls(
        xl, sheet_names['controls'], focal_document_headers
    )
    evidence = extract_evidence(xl, sheet_names['evidence'])
    domains = extract_domains(xl, sheet_names['domains'])
    assessment_objectives = extract_assessment_objectives(
        xl, sheet_names['assessment_objectives']
    )
    framework_names = extract_framework_names(
        xl, framework_columns, sheet_names['authoritative_sources']
    )
    framework_registry = extract_framework_registry(
        xl, framework_names, sheet_names['authoritative_sources']
    )

    # Write control_guidance.json
    control_guidance = {'controls': controls}
    with open(output_dir / 'control_guidance.json', 'w') as f:
        json.dump(control_guidance, f, indent=2)
    print(f"Wrote {output_dir / 'control_guidance.json'}")

    # Write erl.json (Evidence Request List)
    with open(output_dir / 'erl.json', 'w') as f:
        json.dump(evidence, f, indent=2)
    print(f"Wrote {output_dir / 'erl.json'}")

    # Write controls_mapping.json (legacy format for backward compatibility)
    with open(output_dir / 'controls_mapping.json', 'w') as f:
        json.dump(framework_mappings, f, indent=2)
    print(f"Wrote {output_dir / 'controls_mapping.json'}")

    # Write frameworks.json (display names)
    with open(output_dir / 'frameworks.json', 'w') as f:
        json.dump(framework_names, f, indent=2)
    print(f"Wrote {output_dir / 'frameworks.json'}")

    # Write framework_registry.json (names + focal-document identifiers).
    # Additive: frameworks.json above keeps its {id: name} shape for the
    # webclient, this carries the succession signal for the diff engine.
    with open(output_dir / 'framework_registry.json', 'w') as f:
        json.dump(framework_registry, f, indent=2)
    print(f"Wrote {output_dir / 'framework_registry.json'}")

    # Write domains.json
    with open(output_dir / 'domains.json', 'w') as f:
        json.dump(domains, f, indent=2)
    print(f"Wrote {output_dir / 'domains.json'}")

    # Write assessment_objectives.json
    assessment_objectives_file = {'objectives': assessment_objectives}
    with open(output_dir / 'assessment_objectives.json', 'w') as f:
        json.dump(assessment_objectives_file, f, indent=2)
    print(f"Wrote {output_dir / 'assessment_objectives.json'}")

    # Write catalog_meta.json
    catalog_meta = {
        'catalog_version': catalog_version,
        'source_filename': excel_path.name,
        'controls': len(controls),
        'domains': len(domains),
        'evidence': len(evidence),
        'assessment_objectives': len(assessment_objectives),
        'frameworks': len(framework_names),
        # Every column the framework partition refused, and why. Without this the
        # exclusion is invisible: a column that silently stops being ingested
        # looks identical to a column the publisher removed.
        'framework_columns_excluded': {
            col: reason for col, reason in sorted(excluded_columns.items())
        },
    }
    with open(output_dir / 'catalog_meta.json', 'w') as f:
        json.dump(catalog_meta, f, indent=2)
    print(f"Wrote {output_dir / 'catalog_meta.json'}")

    # Print summary
    print("\n=== Summary ===")
    print(f"Controls: {len(controls)}")
    print(f"Evidence items: {len(evidence)}")
    print(f"Domains: {len(domains)}")
    print(f"Assessment Objectives: {len(assessment_objectives)}")
    print(f"Frameworks: {len(framework_names)}")

    # Count extended fields coverage
    cmm_count = sum(1 for c in controls if c.get('cmm_maturity'))
    biz_count = sum(1 for c in controls if c.get('business_size_guidance'))
    scrm_count = sum(1 for c in controls if c.get('scrm_focus'))
    risk_count = sum(1 for c in controls if c.get('risk_threat_mapping') and c['risk_threat_mapping'].get('risk_codes'))
    threat_count = sum(1 for c in controls if c.get('risk_threat_mapping') and c['risk_threat_mapping'].get('threat_codes'))

    print("\n=== Extended Fields Coverage ===")
    print(f"C|P-CMM Maturity: {cmm_count}/{len(controls)} controls ({cmm_count*100//len(controls)}%)")
    print(f"Business Size Guidance: {biz_count}/{len(controls)} controls ({biz_count*100//len(controls)}%)")
    print(f"SCRM Focus: {scrm_count}/{len(controls)} controls ({scrm_count*100//len(controls)}%)")
    print(f"Risk Codes: {risk_count}/{len(controls)} controls ({risk_count*100//len(controls)}%)")
    print(f"Threat Codes: {threat_count}/{len(controls)} controls ({threat_count*100//len(controls)}%)")

    # Show sample control
    if controls:
        print("\n=== Sample Control ===")
        sample = controls[0]
        for key, value in sample.items():
            if isinstance(value, dict) and len(value) > 5:
                print(f"  {key}: ({len(value)} items)")
            elif isinstance(value, list) and len(value) > 5:
                print(f"  {key}: [{len(value)} items]")
            else:
                print(f"  {key}: {value}")

    return catalog_meta


def main():
    args = parse_args()
    excel_path = Path(args.excel_path)

    if not excel_path.exists():
        print(f"Error: Excel file not found: {excel_path}")
        sys.exit(1)

    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir()
    try:
        extract_to_dir(excel_path, output_dir)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
