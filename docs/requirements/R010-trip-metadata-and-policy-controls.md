# R010 — Trip metadata and policy controls

**Status:** Superseded by R016

The original requirement proposed company/sponsor identifiers, routes,
project/cost-centre fields, approver, payment method, policy profiles, limits,
settlement details, and related controls on every trip.

Field testing against IVADO's actual workbook showed that those fields do not
drive the required expense output, and most are not consumed by
`arvine-accounting-expenses`. R016 therefore replaced this requirement with a
minimal trip record:

- claim program and traveller are required;
- dates and business purpose are optional;
- default payer is editable;
- payer and number of employees sharing a bill are reviewed per receipt;
- statement coverage settings remain in the statement-reconciliation step;
- accounting/tax assumptions remain in the accounting settings;
- no approver, legal identifiers, settlement references, policy profile, or
  IVADO-template confirmation is required for report generation.

Existing stored metadata remains readable for backward compatibility, but it is
not presented as required input and is not emitted in contract 3.0.
