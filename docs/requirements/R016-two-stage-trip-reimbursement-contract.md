# R016 — Two-stage trip reimbursement and inter-project contract

## Implementation status

**Field-test ready; official IVADO template confirmation remains.**

The app now implements the independent claim-program and payer controls, dual
Arvine/IVADO eligibility, canonical calculations, report-bundle generation,
contract `2.0.0` NDJSON validation, explicit settlement legs, synchronized
artifact approval, and producer/consumer compatibility tests. The generated
IVADO workbook is intentionally labelled as a contract-backed adapter until the
current official bilingual template and Arvine claimant/payee instruction are
confirmed.

## Decision

Keep `nlp-expenses` and `arvine-accounting-expenses` as separate projects.

- `nlp-expenses` owns receipt extraction, line-item review, alcohol identification,
  trip policy, payer identification, and the two report outputs.
- `arvine-accounting-expenses` owns BMO reconciliation, reimbursement-payment
  reconciliation, accounting-master mappings, and reviewed Tx staging.
- The boundary is the versioned NDJSON record schema at
  `contracts/trip-reimbursement-manifest.v2.schema.json`.

The projects must not infer each other's business rules from workbook layouts.

## User policy

The normal travel policy is:

1. The employee pays every trip expense personally.
2. Arvine Labs reimburses the employee for the full Arvine-approved amount.
3. For an IVADO-sponsored trip, Arvine Labs separately claims from IVADO Labs the
   IVADO-eligible amount.
4. Alcohol and other IVADO policy exclusions reduce only the IVADO claim. They do not
   reduce the Arvine-to-employee reimbursement unless Arvine policy separately rejects
   the expense.
5. A corporate BMO payment remains an explicit fallback payer. It creates no
   Arvine-to-employee reimbursement for that expense.

## Independent dimensions

`claim_program` is trip-level:

- `arvine_only`
- `ivado_sponsored`

`paid_by` is expense-level:

- `employee_personal`
- `arvine_corporate_bmo`

These values are never derived from one another.

## Derived amounts

Each receipt carries three CAD decisions:

- `arvine_reimbursable_cad`: amount Arvine owes the employee; zero for a corporate-paid
  receipt.
- `ivado_claimable_cad`: amount Arvine may claim from IVADO.
- `ivado_excluded_cad`: IVADO-only exclusions, including alcohol.

For an IVADO-sponsored trip, the report controls are:

```text
employee_reimbursement_total_cad
  = sum(arvine_reimbursable_cad)

ivado_claim_total_cad
  = sum(ivado_claimable_cad)

ivado_excluded_total_cad
  = sum(ivado_excluded_cad)

sum(total_cad)
  = employee_reimbursement_total_cad + corporate_paid_total_cad

sum(total_cad)
  = ivado_claim_total_cad + ivado_excluded_total_cad
```

All controls use a CAD 0.02 tolerance.

For an Arvine-only trip, `ivado_claim_total_cad` and
`ivado_excluded_total_cad` are both zero.

## Required report package

`nlp-expenses` produces one package from one reviewed dataset.

### 1. Arvine Labs report

This is always produced. Recommended tabs:

1. `Report`
   - report/trip identifiers;
   - employee and Arvine legal names;
   - claim program;
   - full employee reimbursement;
   - corporate-paid total;
   - IVADO claim and exclusions when applicable;
   - settlement status and references.
2. `Expense Lines`
   - one row per reviewed receipt or allocation;
   - `paid_by`;
   - original and authoritative CAD amounts;
   - taxes and tip;
   - Arvine reimbursement;
   - IVADO claimable and excluded amounts;
   - exclusion reason and receipt URL.
3. `Accounting Rows`
   - Arvine accounting components for employee-paid expenses;
   - employee reimbursement settlement only when paid/reconciled.
4. `Checks`
   - receipt and statement reconciliation;
   - report totals;
   - payer and settlement controls;
   - IVADO claim/exclusion controls.
5. Existing audit tabs
   - statement detail;
   - line-item review;
   - data contract/configuration.

### 2. IVADO report

This is produced only when `claim_program = ivado_sponsored`.

- Use the verified bilingual IVADO template.
- Populate it from the same reviewed expense rows.
- Include only `ivado_claimable_cad`.
- Remove alcohol and other IVADO exclusions from the submitted amounts.
- Preserve the original receipt sequence, receipt-present flag, foreign amount, CAD
  statement amount, location, GL category, tax columns, and approval fields required by
  the template.
- The IVADO total must equal `ivado_claim_total_cad`.

The IVADO template currently describes employee reimbursement and has no company/payee
field. The submission workflow must therefore store the confirmed IVADO instruction
that identifies Arvine Labs as the claimant/payee. Until confirmed, the generated form
must flag this as a review control rather than invent a field.

## Settlement legs

The manifest makes money movement explicit:

1. `employee_reimbursement`
   - payer: Arvine Labs
   - payee: employee
   - amount: `employee_reimbursement_total_cad`
2. `sponsor_reimbursement` for IVADO trips
   - payer: IVADO Labs
   - payee: Arvine Labs
   - amount: `ivado_claim_total_cad`

`arvine-accounting-expenses` matches only the first leg to the BMO reimbursement
transfer. The second leg remains a separate sponsor receivable/payment workflow and
must never settle the employee payable.

## Compatibility

- Contract version: `2.0.0`.
- The producer is authoritative.
- The consumer may accept legacy manifests, but new output must include
  `contract_version`.
- `reimbursement_total_cad` is a legacy alias for
  `employee_reimbursement_total_cad`.
- Any contract change requires a version bump, an updated schema copy in both
  repositories, and producer/consumer fixture tests.

## Acceptance criteria

- A fully personal-paid IVADO trip reimburses the employee for the full Arvine-approved
  amount.
- The IVADO form total excludes alcohol and ties to `ivado_claim_total_cad`.
- Corporate BMO fallback expenses do not increase the employee reimbursement.
- The same receipt cannot create both a corporate BMO expense settlement and an
  employee reimbursement.
- The accounting consumer can distinguish and audit both settlement legs.
- Contract copies in both repositories are byte-for-byte identical.
