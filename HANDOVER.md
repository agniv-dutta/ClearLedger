# Handover

- Name: (add your name)
- Email used for this application: (add your email)
- Chosen track: A — Repair the register
- Why this track: It is a focused audit-and-repair exercise: find seeded defects, prove they matter, fix them with regression coverage, and add one small safety improvement. That matches the reliability work I want to show.
- Approximate total time, including setup and handover: ~3.5 hours

## Run and verify

No dependencies beyond Python 3.10+.

```bash
python app.py            # start; open http://127.0.0.1:8787
python -m unittest discover -s tests -v      # 24 tests
python reproduce_defects.py                  # defect-by-defect before/after demo
python verify_live.py                        # live server + samples/ workflows
python verify_register.py                    # owner's register: restore, import, restart
```

`verify_register.py` copies `fixtures/existing-register.sqlite3`, imports a new invoice and payment, restarts the app, and checks every original plus new record. `fixtures/` is unchanged.

## What I delivered

Six seeded defects, each reproduced first (see `reproduce_defects.py`), fixed with a regression test in `tests/test_defects.py`:

| # | Defect | Location | Owner symptom |
| --- | --- | --- | --- |
| 1 | `status=open` was mapped to `paid` | `ledger/reporting.py` | "Open-invoice view doesn't agree with the overview" |
| 2 | Payment matched by amount before identity | `ledger/matching.py` | "Retry imports move numbers" (1250.00 payment went to the wrong customer) |
| 3 | One bad CSV row rejected the whole import | `ledger/importing.py` | "Import said complete, but records missing" |
| 4 | Re-importing an invoice created a duplicate row | `ledger/storage.py` | "Retry imports move numbers" |
| 5 | Export truncated instead of rounding cents | `ledger/reporting.py` | "Downloaded report and screen don't agree" |
| 6 | UI always showed "Import complete" even on HTTP 400 | `web/app.js` | "Import said complete, but records missing" |

Improvement (beyond required repairs): **register reconciliation check** — `GET /api/reconciliation` plus a "Run reconciliation" button in the UI. It flags duplicate invoice identities and payments attached to the wrong invoice, and reports unmatched-payment count, so the owner can verify trust before the busy week. Checks live in `tests/test_defects.py`.

## Evidence and limits

**One failing-before/passing-after reproduction** (`python reproduce_defects.py`; also `verify_live.py`):

```bash
curl -s -X POST 'http://127.0.0.1:8787/api/import?kind=payments' \
  --data-binary @samples/payments.csv -H 'Content-Type: text/csv'
# Before: PAY-201 (MAPLE/INV-200, 1250.00) credited HARBOR/INV-100 (amount matched first).
# After:  credits MAPLE/INV-200=1250.00, HARBOR/INV-100 stays 0.
```

**Additional changed-input case I designed**: retry of `samples/invoices-mixed.csv` after a successful first import — before the fix totals moved; after, identical rows skip (skipped=2, rejected=1 for the intentionally bad row) and outstanding stays GBP-unchanged at INR 3393.99. Also a pin-code precision case: invoice 1400.12 − payment 444.45 used to export 955.66 while the screen showed 955.67; export now matches at cents for every record (checked in `verify_live.py` and the unit tests).

**Existing-register check**: `verify_register.py` and `TestExistingRegisterPreservation` preserve 9 invoices, 7 open, outstanding 3698.19, KEEP-U1 unmatched, survive restart, and accept new imports (10 records after restart).

**Known limits**: historical misallocations already in a database are flagged by reconciliation but not auto-corrected (out of scope per BUSINESS_RULES.md). I assumed unmatched payments are informational, not errors. I did not test concurrent writers, malformed quoting, or >2 MB files; these are explicitly out of scope.

## Tools and judgment

1. **AI-generated reproduction script** (`reproduce_defects.py`): I wrote it to confirm each suspected defect before touching code; it showed defect 5 needed a real cents case, so I replaced my trivial example with a computed failing one (1400.12 vs 444.45) before fixing.
2. **Pre-fix regression tests**: I chose to write `tests/test_defects.py` to fail on the shipped code first; two of my early tests were wrong (server DB unseeded; overpayment math ignoring the seed payment) and I corrected the tests, not the code.
3. **Live-server verification** (`verify_live.py`): rather than only unit tests, I drove the real `app.py` with the sample files via HTTP to confirm the API contract, retry safety, export/screen parity and reconciliation end-to-end.