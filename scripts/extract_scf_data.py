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

# C|P-CMM Maturity columns
COL_CMM_0 = 'C|P-CMM 0 Not Performed'
COL_CMM_1 = 'C|P-CMM 1 Performed Informally'
COL_CMM_2 = 'C|P-CMM 2 Planned & Tracked'
COL_CMM_3 = 'C|P-CMM 3 Well Defined'
COL_CMM_4 = 'C|P-CMM 4 Quantitatively Controlled'
COL_CMM_5 = 'C|P-CMM 5 Continuously Improving'

# Risk/Threat Mapping columns
COL_RISK_SUMMARY = 'Risk Threat Summary'
COL_THREAT_SUMMARY = 'Control Threat Summary'

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

    level_cols = [
        (COL_CMM_0, 'level_0'),
        (COL_CMM_1, 'level_1'),
        (COL_CMM_2, 'level_2'),
        (COL_CMM_3, 'level_3'),
        (COL_CMM_4, 'level_4'),
        (COL_CMM_5, 'level_5'),
    ]

    for col_name, key in level_cols:
        clean_col = col_map.get(col_name)
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


def extract_controls(xl: pd.ExcelFile, sheet_name: str) -> tuple[list, dict, list]:
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

    # Identify framework columns (columns after NIST CSF Function Grouping that have references)
    # Skip the first ~25 columns which are control metadata
    framework_start_idx = None
    for i, col in enumerate(df.columns):
        if 'AICPA' in col or 'TSC' in col:
            framework_start_idx = i
            break

    if framework_start_idx is None:
        framework_start_idx = 24  # Default fallback

    framework_columns = list(df.columns[framework_start_idx:])
    print(f"Found {len(framework_columns)} framework columns starting at index {framework_start_idx}")

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
    return controls, all_framework_mappings, framework_columns


def extract_evidence(xl: pd.ExcelFile, sheet_name: str) -> dict:
    """Extract Evidence Request List."""
    print("Reading evidence request list...")
    df = pd.read_excel(xl, sheet_name)

    # Clean column names
    df.columns = [clean_column_name(c) for c in df.columns]

    evidence = {}
    for _, row in df.iterrows():
        erl_id = str(row.get('ERL #', '')).strip()
        if not erl_id or pd.isna(row.get('ERL #')):
            continue

        evidence[erl_id] = {
            'evidence_id': erl_id,
            'area_of_focus': str(row.get('Area of Focus', '')).strip() if not pd.isna(row.get('Area of Focus')) else '',
            'artifact_title': str(row.get('Documentation Artifact', '')).strip() if not pd.isna(row.get('Documentation Artifact')) else '',
            'artifact_description': str(row.get('Artifact Description', '')).strip() if not pd.isna(row.get('Artifact Description')) else '',
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
            'principle': str(row.get('Cybersecurity & Data Privacy by Design (C|P) Principles', '')).strip() if not pd.isna(row.iloc[3]) else '',
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

    # Create mapping from column header to friendly name
    framework_names = {}

    for _, row in df.iterrows():
        col_header = str(row.get('Mapping Column Header', '')).strip()
        if not col_header or pd.isna(row.get('Mapping Column Header')):
            continue

        # Get the full authoritative source name
        source_name = str(row.get('Authoritative Source - Law, Regulation or Framework (LRF)', '')).strip()
        if pd.isna(row.get('Authoritative Source - Law, Regulation or Framework (LRF)')):
            source_name = col_header

        # Normalize the column header to match our framework IDs
        fw_id = normalize_framework_id(col_header)
        framework_names[fw_id] = source_name if source_name else col_header

    # Also add entries for the framework columns we found
    for col in framework_columns:
        fw_id = normalize_framework_id(col)
        if fw_id not in framework_names:
            framework_names[fw_id] = clean_column_name(col)

    print(f"Extracted {len(framework_names)} framework names")
    return framework_names


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
    controls, framework_mappings, framework_columns = extract_controls(
        xl, sheet_names['controls']
    )
    evidence = extract_evidence(xl, sheet_names['evidence'])
    domains = extract_domains(xl, sheet_names['domains'])
    assessment_objectives = extract_assessment_objectives(
        xl, sheet_names['assessment_objectives']
    )
    framework_names = extract_framework_names(
        xl, framework_columns, sheet_names['authoritative_sources']
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
