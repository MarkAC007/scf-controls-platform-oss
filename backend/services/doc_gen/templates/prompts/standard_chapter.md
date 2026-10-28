You are writing one chapter of the Mandatory Requirements section of "{document_title}", a technical security standard for {org_name}. This chapter covers one SCF domain. Other chapters cover the standard's other domains, and the platform writes the standard's other sections.

ORGANISATION: {org_name}
INDUSTRY: {industry}

DOMAIN: {domain_id} — {domain_name}
PRINCIPLE: {domain_principle}
INTENT: {domain_intent}
CONTROLS IN SCOPE: {control_count}

CONTROLS DATA:

{control_sections}

---

Write the mandatory requirements for this domain:

- Group the {control_count} controls into 1-5 technical requirement areas, each with a "####" heading.
- Each requirement states a specific, testable parameter (a value, threshold, algorithm, interval or configuration setting), uses "shall" for mandatory items and "should" for recommended, and cites its SCF control identifier in square brackets, e.g. [{example_id}].
- Where the SCF control does not fix a parameter, state the parameter as an organisation-defined value and mark it clearly, e.g. "[organisation-defined: recommended 90 days]". Never invent a parameter and present it as an SCF requirement.
{requirements_instruction}
FORMATTING REQUIREMENTS:
- SCOPE — this is a hard constraint, not a style note. The CONTROLS DATA block above is the complete and exclusive set of controls this organisation has in scope for this domain. Do NOT cite, reference in brackets, or write requirements for any SCF control that does not appear in that block.
- Output ONLY "####" headings and their content. Do NOT output a "#", "##" or "###" heading, and do not repeat the domain name as a heading — the platform adds the chapter heading.
- Every requirement must be testable — no requirement may be satisfied by opinion.
- Formal UK English.
