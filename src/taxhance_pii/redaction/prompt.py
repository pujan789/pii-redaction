"""The one universal detection prompt. Every page of every form type goes
through this prompt — form-specific routing is deliberately gone."""
from __future__ import annotations

from taxhance_pii.domain import PiiCategory

PROMPT_VERSION = "tax-pii-v8"

CATEGORY_MAP: dict[str, PiiCategory] = {
    "client_name": PiiCategory.PERSON_NAME,
    "client_tin": PiiCategory.SSN,
    "address_line": PiiCategory.STREET_ADDRESS,
    "private_id": PiiCategory.OTHER_PRIVATE_ID,
    "email": PiiCategory.EMAIL,
    "phone": PiiCategory.PHONE,
    "dob": PiiCategory.DATE_OF_BIRTH,
}

SYSTEM_PROMPT = (
    "You are a precise PII detector for US tax and accounting documents. "
    "You read one page rendered as a layout-preserving text grid (whitespace "
    "mirrors the page layout, so values sit near their labels exactly as "
    "printed). You return only JSON."
)

USER_TEMPLATE = """Find every CLIENT-SIDE private value on this tax document page. The client \
is the recipient of the document: the taxpayer, employee, payer/borrower, partner, \
shareholder, spouse, dependent, or beneficiary.

Report these categories:
- client_name: each distinct client person name (taxpayer, spouse, dependent, beneficiary, \
partner, shareholder, employee). Report each distinct spelling once.
- client_tin: the client person's SSN, ITIN, or TIN, including partially masked values \
like ***-**-1234 or XXX-XX-1234 — the value near labels such as "Employee's social \
security number", "RECIPIENT'S TIN", "Partner's SSN", or any TIN field whose value \
identifies the client person rather than an institution.
- address_line: ONLY the street line of the client's address (house number + street, \
or PO Box). A unit/apartment printed on its own line is its own address_line item. \
NEVER include the city/state/ZIP text in an item. Also the street line of a property \
address securing a mortgage.
- private_id: private client identifiers (policy, member, payroll, case, brokerage, \
retirement account holder IDs). Not form numbers, not control numbers, not account numbers.
- email: an email address belonging to the client personally.
- phone: a phone number belonging to the client personally — never a company or agency \
phone number.
- dob: date of birth or death.

DO NOT report (these stay visible):
- payer / employer / issuer / lender / financial-institution names, street addresses, \
phone numbers, and their EINs (e.g. values near "Employer ID number" or "PAYER'S TIN" \
when the payer is an institution)
- city/state/ZIP lines, account numbers, money amounts, dates other than birth/death, \
form numbers, OMB numbers, control numbers, tax years, generic labels
Note: labels vary by form. Decide by WHO the value identifies — a private person who \
receives this document (client side, report it) or an institution/business issuing it \
(keep it). A mortgage form's "payer/borrower" is the client person; a bank issuing the \
form is not, whatever its label says.

Rules:
- Copy each value EXACTLY as it appears in the grid, character for character, \
including run-together words, punctuation, and masking characters.
- Report printed VALUES only. Never report a field label, heading, or instruction text.
- One item per value from a single line of the grid. Never merge several lines into one item.
- If the same value repeats, report it once.
- If nothing qualifies, return {{"items": []}}.

Return ONLY the JSON object. Example of the format (with made-up sample values):
{{"items": [{{"text": "JOHN Q SAMPLE", "category": "client_name"}}, \
{{"text": "***-**-9999", "category": "client_tin"}}, \
{{"text": "123 EXAMPLE AVE", "category": "address_line"}}]}}

PAGE GRID:
{page_grid}"""

RESPONSE_JSON_SCHEMA: dict = {
    "type": "json_schema",
    "json_schema": {
        "name": "pii_items",
        "schema": {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "maxItems": 100,
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "maxLength": 200},
                            "category": {
                                "type": "string",
                                "enum": sorted(CATEGORY_MAP),
                            },
                        },
                        "required": ["text", "category"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        },
    },
}


def build_messages(page_grid: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": USER_TEMPLATE.format(page_grid=page_grid)},
    ]
