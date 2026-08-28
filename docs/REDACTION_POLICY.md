# Default redaction policy

This policy defines the automatic boxes produced by the detector. It is
deliberately role-aware rather than a rule that hides every identifier visible
on a tax form: the client's private data is removed, the issuing institution's
data stays readable so the document remains useful.

## Automatically redact

- Client person names: taxpayer, spouse, dependent, beneficiary, partner,
  shareholder, employee — every distinct spelling, every occurrence
- The client person's SSN, ITIN, or TIN, including partially masked values such
  as `***-**-1234`
- Any SSN-shaped value (3-2-4 digits, masked forms included), regardless of its
  field label — the deterministic safety net; EINs (2-7) never match it
- Only the street line of the client's address (house number + street, or PO
  Box); a unit/apartment line counts as a street line. Also the street line of
  a property address securing a mortgage
- Private client identifiers: policy, member, payroll, case, brokerage, and
  retirement account-holder IDs
- Personal email addresses and phone numbers belonging to the client
- Dates of birth and death

## Keep visible by default

- All payer/payor, employer, issuer, lender, and financial-institution
  information: names, organizations, addresses, phones, and EINs
- Account numbers
- City, state, ZIP, and postal code; only the street line is hidden
- Public form numbers, OMB numbers, control numbers, line numbers, government
  agency names, amounts, percentages, tax years, and non-birth/death dates

Labels vary between forms, so the detector decides by role — who the value
identifies — rather than by label matching. A mortgage form's "payer/borrower"
is the client person even though the label says payer; a bank issuing the form
stays visible whatever its label says. When a label is genuinely ambiguous
about whose number a field holds, the SSN-shape safety net makes the failure
direction closed: an SSN-shaped value is always covered.

## Enforcement

One universal prompt states the positive targets and hard exclusions for every
page of every form type; there are no per-form rules. The model returns exact
printed strings, which are anchored to word geometry (so boxes are
pixel-accurate), values found anywhere in a document are re-anchored on all of
its pages, and the SSN-shape net runs independently of the model. After
rendering, the flattened output is re-OCR'd and the job fails closed if any
SSN-shaped value remains visible.

The operator remains authoritative. The review screen can delete an automatic
box or add a manual box for any information that should be hidden for a
particular disclosure.
