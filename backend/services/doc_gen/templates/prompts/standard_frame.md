You are writing the framing sections of a technical security standard for {org_name}. The standard covers several SCF domains together: "{scope_title}". Its core section, Mandatory Requirements, is written separately for each domain and inserted by the platform. You write everything around it.

ORGANISATION: {org_name}
INDUSTRY: {industry}
CREATED DATE: {created_date}

STANDARD: {document_title}
DOMAINS COVERED:
{domain_list}

CONTROLS IN SCOPE: {control_count}
{control_index}

OTHER DOCUMENTS IN THIS ORGANISATION'S POLICY SET:
{related_documents}

---

A policy states intent; a standard states the mandatory parameters that make the intent measurable. Generate these sections in Markdown, in this order:

1. **Document Control** — Populate this EXACT table:

| Field | Value |
|-------|-------|
| **Document Identifier** | STD-{scope_key}-{year} |
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

4. **Compliance Measurement** — How conformance with the requirements is tested and what a passing result looks like.

5. **Deviation and Waiver** — The process for authorised deviation, who approves, expiry and review.

6. **Related Documents** — Refer only to documents in the list of other documents above, by their exact titles.

FORMATTING REQUIREMENTS:
- Use these exact level-2 headings: "## Document Control", "## Purpose", "## Scope", "## Compliance Measurement", "## Deviation and Waiver", "## Related Documents". Change History is "### Change History" under Document Control.
- Do NOT write a Mandatory Requirements section and do not state requirements anywhere. The platform inserts them between Scope and Compliance Measurement.
- Do NOT cite any SCF control identifier in square brackets.
- Formal UK English.
- Do NOT include a title — it is added by the platform. Start directly with the Document Control section.
