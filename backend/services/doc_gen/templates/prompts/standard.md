You are generating a formal {domain_name} technical standard for {org_name}.

ORGANISATION: {org_name}
CREATED DATE: {created_date}

DOMAIN: {domain_id} — {domain_name}
PRINCIPLE: {domain_principle}
INTENT: {domain_intent}
CONTROLS IN SCOPE: {control_count}

CONTROLS DATA:

{control_sections}

---

A policy states intent; a standard states the mandatory parameters that make the intent measurable. Generate it in Markdown with these sections:

1. **Document Control** — Populate this EXACT table:

| Field | Value |
|-------|-------|
| **Document Identifier** | STD-{domain_id}-{year} |
| **Version** | {doc_version} |
| **Status** | Draft |
| **Effective Date** | {created_date} |
| **Document Owner** | Chief Information Security Officer function |
| **Review Cycle** | Annual |
| **Next Review Date** | {next_review_date} |
| **Classification** | Internal |
| **Approved By** | Executive Management function |

   Then a **Change History** table with columns: Version | Date | Author | Description of Changes.
{change_history_instruction}

2. **Purpose** — What this standard fixes in place and why variance is not acceptable.

3. **Scope** — Which systems, environments and asset classes the standard binds.

4. **Mandatory Requirements** — The core section. Group the {control_count} controls into technical requirement areas. Each requirement must state:
   - A specific, testable parameter (a value, threshold, algorithm, interval or configuration setting)
   - The word "shall" for mandatory items and "should" for recommended
   - The SCF control identifier in square brackets
   Where the SCF control does not fix a parameter, state the parameter as an organisation-defined value and mark it clearly, e.g. "[organisation-defined: recommended 90 days]". Never invent a parameter and present it as an SCF requirement.

5. **Compliance Measurement** — For each requirement area, how conformance is tested and what a passing result looks like.

6. **Deviation and Waiver** — The process for authorised deviation, who approves, expiry and review.

7. **Related Documents** — Parent policy, implementing procedures, adjacent standards.

FORMATTING REQUIREMENTS:
- Markdown, ## for main sections, ### for subsections
- Every requirement must be testable — no requirement may be satisfied by opinion
- Distinguish clearly between SCF-derived requirements and organisation-defined parameters
- SCF control identifiers in square brackets
- Formal UK English
- Do NOT include a title — it is added by the platform
- Start directly with the Document Control section
