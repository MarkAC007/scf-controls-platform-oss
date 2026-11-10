You are writing one chapter of the Procedure Steps section of "{document_title}", a formal operational procedure for {org_name}. This chapter covers one activity. Other chapters cover the procedure's other activities, and the platform writes the procedure's other sections.

ORGANISATION: {org_name}
INDUSTRY: {industry}

ACTIVITY: {domain_name}
WHAT IT COVERS: {domain_principle}
CONTEXT: {domain_intent}
CONTROLS IN SCOPE: {control_count}

CONTROLS DATA:

{control_sections}
{existing_steps}
---

A policy says what must be true. This chapter says who does what, when, and what record proves it happened. Write the procedure steps for this activity:

- Group the {control_count} controls into logical operational sequences, each with a "####" heading that names the sequence.
- For each sequence give, in this order:
  - **Trigger** — what causes the sequence to run (an event, a schedule, a request)
  - **Responsible function** — who performs it
  - **Steps** — numbered, imperative, each independently verifiable
  - **Record produced** — the artefact that evidences execution
  - **Controls satisfied** — SCF control identifiers in square brackets, e.g. [{example_id}]
- End with a final "#### Records and Verification" subsection: a Markdown table with columns Record | Owner function | How a reviewer verifies it, one row per record the sequences produce, with the check derived from the assessment objectives.

FORMATTING REQUIREMENTS:
- SCOPE — this is a hard constraint, not a style note. The CONTROLS DATA block above is the complete and exclusive set of controls this organisation has in scope for this activity. Do NOT cite, reference in brackets, or write steps for any SCF control that does not appear in that block. A control absent from the block is deliberately out of scope; writing a step for it states something false about this organisation, which is an audit finding. Every control in the block must be cited by at least one sequence.
- Do NOT restate policy. No statements of intent ("the organisation shall ensure..."); every sentence is a step someone performs, or the trigger, owner or record of one.
- Do NOT name a policy, standard or other procedure.
- Output ONLY "####" headings and their content. Do NOT output a "#", "##" or "###" heading, and do not repeat the activity name as a heading — the platform adds the chapter heading.
- Do not write an introduction, purpose, scope, prerequisites, exception handling or review text; those belong to other sections.
- Formal UK English. When naming a role in a responsibility assignment, always append the word "function".
