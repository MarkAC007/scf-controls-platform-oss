You are writing one chapter of the Policy Statements section of "{document_title}", a formal security policy for {org_name}. This chapter covers one SCF domain. Other chapters cover the policy's other domains, and the platform writes the policy's other sections.

ORGANISATION: {org_name}
INDUSTRY: {industry}

DOMAIN: {domain_id} — {domain_name}
PRINCIPLE: {domain_principle}
INTENT: {domain_intent}
CONTROLS IN SCOPE: {control_count}

CONTROLS DATA:

{control_sections}

---

Write the policy statements for this domain:

- Group the {control_count} controls into 1-5 logical subsections, each with a "####" heading.
- Each policy statement is a directive ("{org_name} shall...", "All personnel must..."), derived from the control description and assessment objectives, actionable and verifiable.
- Each statement cites its SCF control identifier in square brackets, e.g. [{example_id}].
{requirements_instruction}
FORMATTING REQUIREMENTS:
- SCOPE — this is a hard constraint, not a style note. The CONTROLS DATA block above is the complete and exclusive set of controls this organisation has in scope for this domain. Do NOT cite, reference in brackets, or write requirements for any SCF control that does not appear in that block. A control absent from the block is deliberately out of scope; asserting a requirement for it states something false about this organisation, which is an audit finding.
- Output ONLY "####" headings and their content. Do NOT output a "#", "##" or "###" heading, and do not repeat the domain name as a heading — the platform adds the chapter heading.
- Do not write an introduction, purpose, scope, roles, exceptions or review text; those belong to other sections.
- Formal UK English. When referencing a role in a directive, always append the word "function".
