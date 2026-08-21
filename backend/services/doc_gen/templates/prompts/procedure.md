You are generating a formal {domain_name} procedure document for {org_name}.

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

A policy says what must be true. This procedure says who does what, when, and what record proves it happened. Generate it in Markdown with these sections:

1. **Document Control** — Populate this EXACT table:

| Field | Value |
|-------|-------|
| **Document Identifier** | PRO-{domain_id}-{year} |
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

2. **Purpose and Relationship to Policy** — State which policy this procedure implements and what operational outcome it delivers.

3. **Scope and Applicability** — Systems, teams and circumstances the procedure governs.

4. **Prerequisites** — Access, tooling, approvals and information required before the procedure can be executed.

5. **Procedure Steps** — The core section. Group the {control_count} controls into logical operational sequences. For each sequence provide numbered steps in this shape:
   - **Trigger** — what causes this sequence to run (event, schedule, request)
   - **Responsible function** — who performs it
   - **Steps** — numbered, imperative, each independently verifiable
   - **Record produced** — the artefact that evidences execution
   - **Controls satisfied** — SCF control identifiers in square brackets

6. **Exception Handling** — What to do when a step cannot be completed, who authorises a deviation, and how the deviation is recorded.

7. **Records and Retention** — Table of records produced, their owner, storage location and retention period.

8. **Verification** — How a reviewer confirms the procedure was followed, derived from the assessment objectives.

9. **Related Documents** — The parent policy and adjacent procedures.

FORMATTING REQUIREMENTS:
- Markdown, ## for main sections, ### for subsections
- Every step must be imperative and independently verifiable — a reader must be able to tell whether it was done
- SCF control identifiers in square brackets
- Formal UK English
- Do NOT include a title — it is added by the platform
- Start directly with the Document Control section
- Always append "function" when naming a role in a responsibility assignment
