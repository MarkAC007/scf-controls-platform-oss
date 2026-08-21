You are generating a formal {domain_name} policy document for {org_name}.

ORGANISATION: {org_name}
CREATED DATE: {created_date}

DOMAIN: {domain_id} — {domain_name}
PRINCIPLE: {domain_principle}
INTENT: {domain_intent}
CONTROLS IN SCOPE: {control_count}

CONTROL OWNERS:
{owners_list}

CONTROLS DATA:

{control_sections}

---

Generate a formal security policy document in Markdown with these sections:

1. **Document Control** — Populate this EXACT table. Do not change the structure, column count, or row order:

| Field | Value |
|-------|-------|
| **Document Identifier** | POL-{domain_id}-{year} |
| **Version** | {doc_version} |
| **Status** | Draft |
| **Effective Date** | {created_date} |
| **Document Owner** | Chief Information Security Officer function |
| **Review Cycle** | Annual |
| **Next Review Date** | {next_review_date} |
| **Classification** | Internal |
| **Approved By** | Executive Management function |

   Use this exact table structure. Do not add or remove rows. For the Document Owner and Approved By fields, use "function" language (e.g. "Chief Information Security Officer function", not a job title).

   After the Document Control table, include a **Change History** table with these EXACT columns: Version | Date | Author | Description of Changes.
{change_history_instruction}

2. **Purpose** — Derive from the domain principle statement and intent. Explain why this policy exists and what it aims to achieve. 2-3 paragraphs.

3. **Scope** — Define what systems, people, processes, and data this policy covers. Reference {org_name} specifically. Be specific about applicability.

4. **Policy Statements** — This is the core section. Group the {control_count} controls into logical subsections (3-7 subsections). Each policy statement should:
   - Be written as a directive ("{org_name} shall...", "All personnel must...")
   - Reference the specific SCF control identifier in square brackets
   - Be derived from the control description and assessment objectives
   - Be actionable and verifiable

5. **Roles and Responsibilities** — Begin with this preamble paragraph verbatim: "All roles referenced in this document describe organisational *functions*, not specific job titles. A function represents a set of responsibilities that may be performed by an individual holding a different title depending on the organisation's structure. The requirements in this document attach to the function, not the title. Organisations should maintain a separate Function-to-Role Mapping register to document which individuals or positions currently fulfil each function."
   Then create a responsibility matrix from the control owners, using "function" language.

6. **Compliance Monitoring** — Derive from the assessment objectives. Define how compliance with this policy is measured and monitored. Include audit frequency and methods.

7. **Evidence Requirements** — Define what records and evidence must be maintained to demonstrate compliance. Derive from the assessment objectives.

8. **Exceptions** — Define the exception process for when controls cannot be implemented as stated.

9. **Related Documents** — Cross-reference other relevant domain policies that interact with this one.

10. **Review and Revision** — Annual review commitment, triggers for out-of-cycle review.

FORMATTING REQUIREMENTS:
- Use Markdown with proper heading hierarchy (## for main sections, ### for subsections)
- Include SCF control identifiers as references in square brackets
- Write in formal UK English
- Target audience: auditors, senior management, and operational staff
- Aim for 4-8 pages when rendered
- Do NOT include a title — it is added by the platform
- Start directly with the Document Control section
- CRITICAL: when referencing a role in a directive, requirement, or responsibility assignment, always append the word "function" (e.g. "the CISO function shall approve..."). This decouples the policy from a specific organisational structure and makes the document portable.
