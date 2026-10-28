You are writing the top-level Information Security Policy for {org_name}. It is the master policy of the ISMS (ISO/IEC 27001, clause 5.2), approved by top management, and sits above the organisation's topic policies.

ORGANISATION: {org_name}
INDUSTRY: {industry}
DATE: {created_date}

ISMS SCOPE (as recorded by the organisation — use only these facts):
- Organisation: {scope_description}
- Boundaries: {scope_boundaries}
- Locations: {scope_locations}
- Products and services: {scope_services}
- Exclusions: {scope_exclusions}

INFORMATION SECURITY OBJECTIVES (as recorded by the organisation):
{objectives}

PROGRAMME FACTS:
- Controls in scope: {control_count} across {domain_count} domains; {implemented_count} implemented ({coverage}%)
- Systems in the Systems Registry: {system_count}
- Suppliers in the Vendor Inventory: {vendor_count}
- Risks recorded: {risk_count}; acceptable risk level: {acceptable_risk}
- Frameworks and obligations in scope: {frameworks}

DOMAINS IN SCOPE:
{domain_summaries}

THE ORGANISATION'S POLICY SET (exact titles):
{policy_set}

---

Generate the Information Security Policy in Markdown with these sections:

1. **Document Control** — Populate this EXACT table:

| Field | Value |
|-------|-------|
| **Document Identifier** | ISP-{year} |
| **Version** | {doc_version} |
| **Status** | Draft |
| **Effective Date** | {created_date} |
| **Document Owner** | Chief Information Security Officer function |
| **Review Cycle** | Annual |
| **Next Review Date** | {next_review_date} |
| **Classification** | Internal |
| **Approved By** | Top management |

   Then a **Change History** table with columns: Version | Date | Author | Description of Changes.
{change_history_instruction}

2. **Management Commitment** — Top management's commitment to information security, to satisfying applicable requirements, and to continual improvement of the ISMS. 2-3 paragraphs, suitable for signature.

3. **Purpose** — Why the policy exists and how it supports the organisation's purpose.

4. **Scope** — Restate the ISMS scope above. Where a scope fact is shown as "[To be completed]", write "[To be completed]" in the document rather than inventing it.

5. **Information Security Objectives** — State that objectives are set and how. List the recorded objectives above; if none are recorded, write "[To be completed]".

6. **Principles** — 5-8 principles that underpin the topic policies (for example least privilege, defence in depth, accountability, a risk-based approach).

7. **Governance and Responsibilities** — Top management, the CISO function, policy owners and all personnel, using "function" language.

8. **Risk Management** — The risk-based approach: assessment, treatment and acceptance against the acceptable risk level.

9. **Policy Framework** — The document hierarchy, and a table of the organisation's policy set using the exact titles above.

10. **Compliance** — Obligations in scope (use only the frameworks listed above), consequences of non-compliance, and exceptions.

11. **Review** — Annual review and the triggers for out-of-cycle review.

FORMATTING REQUIREMENTS:
- Never invent a fact about the organisation (a location, service, regulator, objective, person or date). Where something is not given above, write "[To be completed]".
- Do NOT cite SCF control identifiers in square brackets; this policy sits above the controls.
- Use Markdown, ## for sections, ### for subsections. Formal UK English.
- Do NOT include a title — it is added by the platform. Start directly with the Document Control section.
- When referencing a role, always append the word "function".
