You are writing the framing sections of a formal operational procedure for {org_name}. The procedure covers several related activities together: "{scope_title}". Its core section, Procedure Steps, is written separately for each activity and inserted by the platform. You write everything around it.

ORGANISATION: {org_name}
INDUSTRY: {industry}
CREATED DATE: {created_date}

PROCEDURE: {document_title}
ACTIVITIES COVERED, ONE CHAPTER EACH:
{domain_list}

ACTIVITIES WITH NO CONTROLS IN THIS ORGANISATION'S SCOPE:
{not_applicable}

CONTROLS IN SCOPE: {control_count}
{control_index}

CONTROL OWNERS:
{owners_list}

OTHER DOCUMENTS IN THIS ORGANISATION'S DOCUMENT SET:
{related_documents}

---

A policy says what must be true. A procedure says who does what, when, and what record proves it happened. Generate these sections in Markdown, in this order:

1. **Document Control** — Populate this EXACT table. Do not change the structure, column count, or row order:

| Field | Value |
|-------|-------|
| **Document Identifier** | PRO-{scope_key}-{year} |
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

2. **Purpose** — The operational outcome this procedure delivers across the activities it covers. One or two paragraphs. Name a policy this procedure implements only if its exact title is in the list of other documents above; otherwise say it implements {org_name}'s information security policies, without naming one.

3. **Scope and Applicability** — The systems, teams and circumstances the procedure governs at {org_name}, and the activities it covers, by the names given above.

4. **Prerequisites** — Access, tooling, approvals and information required before any of the activities can be carried out.

5. **Exception Handling** — What to do when a step cannot be completed, who authorises a deviation, and how the deviation is recorded.

6. **Records and Retention** — Where the records this procedure produces are kept, who owns them, and how long they are retained. Each activity's chapter lists its own records; state the rules that apply to all of them.

7. **Verification** — How a reviewer samples the records to confirm the procedure was followed, and how often.

8. **Related Documents** — Refer only to documents in the list of other documents above, by their exact titles.

FORMATTING REQUIREMENTS:
- Use these exact level-2 headings: "## Document Control", "## Purpose", "## Scope and Applicability", "## Prerequisites", "## Exception Handling", "## Records and Retention", "## Verification", "## Related Documents". Change History is "### Change History" under Document Control.
- Do NOT write a Procedure Steps section and do not write procedure steps anywhere. The platform inserts them between Prerequisites and Exception Handling.
- Do NOT restate policy. Write no statements of intent or requirement; describe how the work is organised and evidenced.
- Do NOT write anything about the activities with no controls in scope. The platform records them in the Scope and Applicability section.
- Do NOT cite any SCF control identifier in square brackets. Citations belong to the Procedure Steps.
- Do NOT assert that a policy, procedure or standard exists unless its exact title is in the list of other documents.
- Formal UK English.
- Do NOT include a title — it is added by the platform. Start directly with the Document Control section.
- When referencing a role in a responsibility assignment, always append the word "function".
