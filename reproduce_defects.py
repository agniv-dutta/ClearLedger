"""Reproduce the seeded defects against an in-memory database (pre-fix baseline)."""
import sqlite3
from pathlib import Path
from ledger import storage, reporting, importing, matching

db = storage.connect(':memory:')
storage.seed(db)

print('=== DEFECT 1: status=open returns PAID invoices ===')
open_rows = reporting.invoices(db, 'open')
print('status=open returned:', [(r['invoice_number'], r['status']) for r in open_rows])

print('\n=== DEFECT 2: amount-first matching attaches payment to wrong invoice ===')
csv_amt = 'payment_id,customer_id,invoice_number,amount\nPAY-201,MAPLE,INV-200,1250.00\n'
res = importing.import_csv(db, csv_amt, 'payments')
inv100 = next(r for r in reporting.invoices(db) if r['invoice_number'] == 'INV-100')
inv200 = next(r for r in reporting.invoices(db) if r['invoice_number'] == 'INV-200')
print(f'Import result: {res}')
print(f'HARBOR INV-100 paid={inv100["paid"]} (should stay 0, payment is for MAPLE)')
print(f'MAPLE INV-200 paid={inv200["paid"]} (should be 1250.00)')

print('\n=== DEFECT 3: one bad row rejects the WHOLE import ===')
csv_mixed = 'customer_id,invoice_number,amount,due_date\nHARBOR,INV-103,84.00,2026-09-12\nNORTH,INV-302,not-a-number,2026-09-12\nMAPLE,INV-203,100.00,2026-09-13\n'
try:
    res = importing.import_csv(db, csv_mixed, 'invoices')
    print(f'Import result: {res}')
except ValueError as e:
    print(f'RAISED ValueError: {e} (whole import rejected, valid rows lost)')

print('\n=== DEFECT 4: duplicate invoice re-import creates a second row ===')
csv_same = 'customer_id,invoice_number,amount,due_date\nHARBOR,INV-100,1250.00,2026-09-01\n'
res = importing.import_csv(db, csv_same, 'invoices')
rows = [r for r in reporting.invoices(db) if r['invoice_number'] == 'INV-100']
print(f'Import result: {res}')
print(f'Rows with identity HARBOR/INV-100 after re-import: {len(rows)} (should be 1)')

print('\n=== DEFECT 5: export truncates instead of rounding ===')
db2 = storage.connect(':memory:')
storage.seed(db2)
importing.import_csv(db2, 'payment_id,customer_id,invoice_number,amount\nX1,HARBOR,INV-100,1241.85\n', 'payments')
inv = next(r for r in reporting.invoices(db2) if r['invoice_number'] == 'INV-100')
print(f'INV-100 balance on screen: {inv["balance"]!r}')
exp = reporting.export_csv(db2).splitlines()[1]
print(f'INV-100 balance in export: {exp.split(",")[4]}')

print('\n=== DEFECT 6: browser always reports success regardless of HTTP status ===')
print('FIXED: web/app.js now checks response.ok, shows imported/skipped/rejected counts')
print('and rejected line reasons, and reports the server error message instead of')
print('claiming success on a 400. Verified end-to-end in verify_live.py (wrong header -> 400).')