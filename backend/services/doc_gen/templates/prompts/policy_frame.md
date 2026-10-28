You are writing the framing sections of a formal security policy for {org_name}. The policy covers several SCF domains together: "{scope_title}". Its core section, Policy Statements, is written separately for each domain and inserted by the platform. You write everything around it.

ORGANISATION: {org_name}
INDUSTRY: {industry}
CREATED DATE: {created_date}

POLICY: {document_title}
DOMAINS COVERED:
{domain_list}

CONTROLS IN SCOPE: {control_count}
{control_index}

CONTROL OWNERS:
{owners_list}

OTHER DOCUMENTS IN THIS ORGANISATION'S POLICY SET:
{related_documents}

---

Generate these sections in Markdown, in this order:

1. **Document Control** — Populate this EXACT table. Do not change the structure, column count, or row order:

| Field | Value |
|-------|-------|
| **Document Identifier** | POL-{scope_key}-{year} |
| **Version** | {doc_version} |
| **Status** | Draft |
| **Effective Date** | {created_date} |
| **Document Owner** | Chief Information Security Officer function |
| **Review Cycle** | Annual |
| **Next Review Date** | {next_review_date} |
| **Classification** | Internal |
| **Approved By** | Executive Management function |

   After the Document Control table, include a **Change History** table with these EXACT columns: Version | Date | Author | Description of Changes.
{change_history_instruction}

2. **Purpose** — Derive from the principles of the domains covered. Explain why this policy exists and what it aims to achieve across those areas. 2-3 paragraphs.

3. **Scope** — Define what systems, people, processes, and data this policy covers. Reference {org_name} specifically, and name the subject areas it covers.

4. **Roles and Responsibilities** — Begin with this preamble paragraph verbatim: "All roles referenced in this document describe organisational *functions*, not specific job titles. A function represents a set of responsibilities that may be performed by an individual holding a different title depending on the organisation's structure. The requirements in this document attach to the function, not the title. Organisations should maintain a separate Function-to-Role Mapping register to document which individuals or positions currently fulfil each function."
   Then create a responsibility matrix from the control owners, using "function" language.

5. **Compliance Monitoring** — How compliance with this policy is measured and monitored across the areas it covers, including audit frequency and methods.

6. **Evidence Requirements** — What records and evidence must be maintained to demonstrate compliance.

7. **Exceptions** — The exception process for when requirements cannot be met as stated.

8. **Related Documents** — Refer only to documents in the list of other documents above, by their exact titles.

9. **Review and Revision** — Annual review commitment, and triggers for out-of-cycle review.

FORMATTING REQUIREMENTS:
- Use these exact level-2 headings: "## Document Control", "## Purpose", "## Scope", "## Roles and Responsibilities", "## Compliance Monitoring", "## Evidence Requirements", "## Exceptions", "## Related Documents", "## Review and Revision". Change History is "### Change History" under Document Control.
- Do NOT write a Policy Statements section and do not write policy statements anywhere. The platform inserts them between Scope and Roles and Responsibilities.
- Do NOT cite any SCF control identifier in square brackets. Citations belong to the Policy Statements.
- Write in formal UK English for auditors, senior management and operational staff.
- Do NOT include a title — it is added by the platform. Start directly with the Document Control section.
- CRITICAL: when referencing a role in a directive, requirement, or responsibility assignment, always append the word "function" (e.g. "the CISO function shall approve...").
