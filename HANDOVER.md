# Handover

- Name: (add your name)
- Email used for this application: (add your email)
- Chosen track: A — Repair the register
- Why this track: A focused audit-and-repair exercise: find seeded defects, prove they matter, fix them with regression coverage, and add a small safety improvement. That matches the reliability work I want to show.
- Approximate total time, including setup and handover: ~3.5 hours

## Run and verify

No dependencies beyond Python 3.10+.

```bash
python app.py            # start; open http://127.0.0.1:8787
python -m unittest discover -s tests -v      # 24 tests
python reproduce_defects.py                  # defect-by-defect before/after demo
python verify_rules.py                       # BUSINESS_RULES.md compliance checks
python verify_live.py                        # live server + samples/ workflows
python verify_register.py                    # owner's register: restore, import, restart
```

`verify_register.py` copies `fixtures/existing-register.sqlite3`, imports a new invoice and payment, restarts the app, and checks every original plus new record. `fixtures/` is unchanged.

## What I delivered

Six seeded defects, each reproduced first (see `reproduce_defects.py`), fixed with a regression test in `tests/test_defects.py`:

| # | Defect | Location | Owner symptom |
| --- | --- | --- | --- |
| 1 | `status=open` was mapped to `paid` | `ledger/reporting.py` | "Open-invoice view doesn't agree with the overview" |
| 2 | Payment matched by amount before identity | `ledger/matching.py` | "Retry imports move numbers" |
| 3 | One bad CSV row rejected the whole import | `ledger/importing.py` | "Import said complete, but records missing" |
| 4 | Re-importing an invoice created a duplicate row | `ledger/storage.py` | "Retry imports move numbers" |
| 5 | Export truncated instead of rounding cents | `ledger/reporting.py` | "Downloaded report and screen don't agree" |
| 6 | UI always showed "Import complete" even on HTTP 400 | `web/app.js` | "Import said complete, but records missing" |

Improvement (beyond required repairs): **register reconciliation check** — `GET /api/reconciliation` plus a "Run reconciliation" button. It flags duplicate invoice identities and wrong-invoice payment attachments, and reports unmatched-payment count, so the owner can verify trust before the busy week. Checks live in `tests/test_defects.py`.

## Evidence and limits

**Failing-before/passing-after** (also `verify_live.py`):

```bash
curl -s -X POST 'http://127.0.0.1:8787/api/import?kind=payments' \
  --data-binary @samples/payments.csv -H 'Content-Type: text/csv'
# Before: PAY-201 (MAPLE/INV-200, 1250.00) credited HARBOR/INV-100 (amount matched first).
# After:  credits MAPLE/INV-200=1250.00, HARBOR/INV-100 stays 0.
```

**Changed-input case I designed**: retry `samples/invoices-mixed.csv` after a successful import — before, totals moved; after, identical rows skip (skipped=2, rejected=1) and outstanding stays INR 3393.99. Also invoice 1400.12 − payment 444.45 exported 955.66 while the screen showed 955.67; export now matches the screen at cents for every record (in `verify_rules.py`, `verify_live.py`, unit tests).

**Existing-register check**: `verify_register.py` and `TestExistingRegisterPreservation` confirm 9 invoices, 7 open, outstanding 3698.19, KEEP-U1 unmatched; new imports survive a restart.

**Known limits**: historical misallocations are flagged but not auto-corrected (out of scope). Unmatched payments are informational, not errors. Excluded by design: concurrent writers, malformed quoting, >2 MB files.

## Tools and judgment

All verification used Python stdlib (unittest, http.client, sqlite3, subprocess) against the real `app.py`; no third-party packages. I used the opencode AI coding agent (Claude-powered) to draft and review code.

1. **AI-drafted reproduction script** (`reproduce_defects.py`): I asked for a script confirming each suspected defect before touching code. My first defect-5 example did not trigger the truncation, so I searched numerically for a failing cents case (1400.12 − 444.45) and verified it before fixing.
2. **Failing tests first** (`tests/test_defects.py`): regression tests were written against the shipped code so each failed before its fix. Two early tests were wrong (HTTP test DB unseeded; overpayment math ignoring the seed payment) — I corrected the tests, not the code, so the suite stayed a true spec check.
3. **Live-server checks** (`verify_rules.py`, `verify_live.py`, `verify_register.py`): I drove the real server over HTTP rather than relying on unit tests alone, covering retry safety, export/screen parity, status filters and restart persistence with the supplied `samples/` and `fixtures/`.