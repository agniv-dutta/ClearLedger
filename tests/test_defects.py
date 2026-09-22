"""Regression tests for the six seeded defects. Each test fails before the fix
and passes after it, exercising the intended behaviour from BUSINESS_RULES.md."""
import http.client
import shutil
import sqlite3
import tempfile
import threading
import unittest
from io import StringIO
from pathlib import Path

from ledger import importing, reporting, storage
from ledger.http_app import make_server

ROOT = Path(__file__).resolve().parent.parent
INVOICE_HEADER = 'customer_id,invoice_number,amount,due_date'
PAYMENT_HEADER = 'payment_id,customer_id,invoice_number,amount'


class TestDefects(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = storage.connect(Path(self.tmp.name) / 'demo.sqlite3')
        storage.seed(self.db)

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    # --- Defect 1: status=open returned the paid invoices ---
    def test_open_filter_returns_only_open(self):
        open_rows = reporting.invoices(self.db, 'open')
        self.assertEqual(len(open_rows), 5)
        self.assertTrue(all(r['status'] == 'open' for r in open_rows))
        self.assertTrue(all(r['invoice_number'] != 'INV-101' for r in open_rows))
        paid_rows = reporting.invoices(self.db, 'paid')
        self.assertEqual(len(paid_rows), 1)
        self.assertTrue(all(r['status'] == 'paid' for r in paid_rows))

    # --- Defect 2: amount-first matching attached a payment to the wrong invoice ---
    def test_payment_attaches_to_exact_invoice_not_amount_match(self):
        csv_ = f'{PAYMENT_HEADER}\nPAY-201,MAPLE,INV-200,1250.00\n'
        result = importing.import_csv(self.db, csv_, 'payments')
        self.assertEqual(result['imported'], 1)
        by_number = {r['invoice_number']: r for r in reporting.invoices(self.db)}
        self.assertEqual(by_number['INV-100']['paid'], 0.0)
        self.assertEqual(by_number['INV-200']['paid'], 1250.0)

    def test_payment_without_exact_invoice_stays_unmatched(self):
        csv_ = f'{PAYMENT_HEADER}\nPAY-X,NORTH,INV-999,100.00\n'
        result = importing.import_csv(self.db, csv_, 'payments')
        self.assertEqual(result['imported'], 1)
        by_number = {r['invoice_number']: r for r in reporting.invoices(self.db)}
        self.assertEqual(by_number['INV-301']['paid'], 0.0)
        unmatched = reporting.overview(self.db)['unmatched_payments']
        self.assertEqual([u['payment_id'] for u in unmatched], ['PAY-X'])

    # --- Defect 3: one bad row rejected the whole import ---
    def test_invalid_row_rejects_only_that_row(self):
        csv_ = ('customer_id,invoice_number,amount,due_date\n'
                'HARBOR,INV-103,84.00,2026-09-12\n'
                'NORTH,INV-302,not-a-number,2026-09-12\n'
                'MAPLE,INV-203,100.00,2026-09-13\n')
        result = importing.import_csv(self.db, csv_, 'invoices')
        self.assertEqual(result['imported'], 2)
        self.assertEqual(result['rejected'], 1)
        self.assertEqual(result['skipped'], 0)
        self.assertEqual(result['errors'][0]['line'], 3)
        self.assertIn('amount', result['errors'][0]['reason'].lower())
        numbers = [r['invoice_number'] for r in reporting.invoices(self.db)]
        self.assertIn('INV-103', numbers)
        self.assertNotIn('INV-302', numbers)
        self.assertIn('INV-203', numbers)

    def test_invalid_header_rejects_whole_import(self):
        with self.assertRaises(ValueError):
            importing.import_csv(self.db, 'customer,invoice,value\nHARBOR,INV-110,50.00\n', 'invoices')
        self.assertEqual(len(reporting.invoices(self.db)), 6)

    # --- Defect 4: re-importing an invoice duplicated it and moved totals ---
    def test_identical_invoice_reimport_skipped_without_changes(self):
        csv_ = f'{INVOICE_HEADER}\nHARBOR,INV-100,1250.00,2026-09-01\n'
        first = importing.import_csv(self.db, csv_, 'invoices')
        self.assertEqual(first['imported'], 0, 'seed INV-100 already exists')
        before = reporting.overview(self.db)['summary']['outstanding']
        second = importing.import_csv(self.db, csv_, 'invoices')
        self.assertEqual(second['skipped'], 1)
        count = len([r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100'])
        self.assertEqual(count, 1)
        self.assertEqual(reporting.overview(self.db)['summary']['outstanding'], before)

    def test_reused_identity_with_different_details_rejected(self):
        csv_ = f'{INVOICE_HEADER}\nHARBOR,INV-100,999.99,2026-09-01\n'
        result = importing.import_csv(self.db, csv_, 'invoices')
        self.assertEqual(result['rejected'], 1)
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'INV-100')
        self.assertEqual(row['amount'], 1250.00)

    def test_identical_payment_reimport_skipped(self):
        csv_ = f'{PAYMENT_HEADER}\nSEED-1,HARBOR,INV-101,300.00\n'
        result = importing.import_csv(self.db, csv_, 'payments')
        self.assertEqual(result['skipped'], 1)
        self.assertEqual(len(reporting.overview(self.db)['unmatched_payments']), 0)

    # --- Defect 5: export truncated instead of rounding to cents ---
    def test_export_rounds_to_cents_like_screen(self):
        importing.import_csv(self.db,
                             f'{INVOICE_HEADER}\nHARBOR,ROUND-1,1400.12,2026-09-20\n', 'invoices')
        importing.import_csv(self.db,
                             f'{PAYMENT_HEADER}\nRP-1,HARBOR,ROUND-1,444.45\n', 'payments')
        row = next(r for r in reporting.invoices(self.db) if r['invoice_number'] == 'ROUND-1')
        self.assertEqual(row['balance'], 955.67)
        exported = list(csv_rows(reporting.export_csv(self.db)))
        exported_row = next(e for e in exported if e['invoice_number'] == 'ROUND-1')
        self.assertEqual(exported_row['balance'], '955.67')

    def test_all_export_money_matches_screen_at_cents(self):
        for r in reporting.invoices(self.db):
            exported = {k: r[k] for k in ('customer_id', 'invoice_number')} or None
        exported = list(csv_rows(reporting.export_csv(self.db)))
        for row in reporting.invoices(self.db):
            e = next(x for x in exported if x['invoice_number'] == row['invoice_number'])
            self.assertEqual(e['paid'], f'{row["paid"]:.2f}')
            self.assertEqual(e['balance'], f'{row["balance"]:.2f}')

    # --- Defect 6: HTTP import feedback never reported failure or counts ---
    def test_http_import_failure_is_not_success(self):
        port, db_path = self._start_server()
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        try:
            conn.request('POST', '/api/import?kind=invoices', body='customer,invoice,value\nX,1,2\n',
                         headers={'Content-Type': 'text/csv'})
            resp = conn.getresponse()
            payload = resp.read().decode()
            self.assertEqual(resp.status, 400)
            self.assertIn('error', payload)
        finally:
            conn.close()

    def test_http_mixed_import_returns_counts_and_line_errors(self):
        port, db_path = self._start_server()
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
        try:
            body = ('customer_id,invoice_number,amount,due_date\n'
                    'HARBOR,INV-103,84.00,2026-09-12\n'
                    'NORTH,INV-302,bad,2026-09-12\n'
                    'MAPLE,INV-203,100.00,2026-09-13\n')
            conn.request('POST', '/api/import?kind=invoices', body=body,
                         headers={'Content-Type': 'text/csv'})
            resp = conn.getresponse()
            payload = json_like(resp.read().decode())
            self.assertEqual(resp.status, 200)
            self.assertEqual(payload['imported'], 2)
            self.assertEqual(payload['rejected'], 1)
            self.assertEqual(payload['errors'][0]['line'], 3)
        finally:
            conn.close()

    # --- Additional designed edge cases ---
    def test_overpayment_leaves_other_invoices_untouched(self):
        importing.import_csv(self.db,
                             f'{PAYMENT_HEADER}\nOV-1,HARBOR,INV-101,10.00\n', 'payments')
        by_number = {r['invoice_number']: r for r in reporting.invoices(self.db)}
        self.assertEqual(by_number['INV-101']['balance'], -10.0)
        self.assertEqual(by_number['INV-101']['status'], 'paid')
        self.assertEqual(by_number['INV-100']['balance'], 1250.0)
        summary = reporting.overview(self.db)['summary']
        self.assertEqual(summary['outstanding'], 3209.99)

    def test_empty_file_with_valid_header_is_success(self):
        result = importing.import_csv(self.db, f'{INVOICE_HEADER}\n', 'invoices')
        self.assertEqual(result, {'imported': 0, 'skipped': 0, 'rejected': 0, 'errors': []})

    def test_reconciliation_clean_register_reports_no_issues(self):
        data = reporting.reconciliation(self.db)
        self.assertEqual(data['issue_count'], 0)
        self.assertEqual(data['issues'], [])
        self.assertEqual(data['unmatched_payments'], 0)

    def test_reconciliation_flags_wrong_allocation(self):
        self.db.execute(
            "UPDATE payments SET invoice_id = "
            "(SELECT id FROM invoices WHERE customer_id='HARBOR' AND invoice_number='INV-100') "
            "WHERE payment_id='SEED-2'")
        self.db.commit()
        data = reporting.reconciliation(self.db)
        self.assertIn('allocation_mismatch', [i['kind'] for i in data['issues']])

    def test_reconciliation_flags_duplicate_identity(self):
        self.db.execute(
            "INSERT INTO invoices (customer_id, invoice_number, amount, due_date) "
            "VALUES ('HARBOR', 'INV-100', 1250.00, '2026-09-01')")
        self.db.commit()
        data = reporting.reconciliation(self.db)
        dup = next(i for i in data['issues'] if i['kind'] == 'duplicate_invoice')
        self.assertEqual((dup['customer_id'], dup['invoice_number']), ('HARBOR', 'INV-100'))

    def _start_server(self):
        db_path = Path(self.tmp.name) / 'http.sqlite3'
        db = storage.connect(db_path)
        storage.seed(db)
        db.close()
        server = make_server(db_path, ROOT / 'web', 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

        self.addCleanup(stop)
        return server.server_port, db_path


class TestExistingRegisterPreservation(unittest.TestCase):
    """BUSINESS_RULES.md requires repair to preserve the owner's register on a
    copy of fixtures/existing-register.sqlite3 and allow new imports + restart."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / 'register.sqlite3'
        shutil.copy2(ROOT / 'fixtures' / 'existing-register.sqlite3', self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_expected_starting_state_preserved(self):
        db = storage.connect(self.db_path)
        try:
            summary = reporting.overview(db)['summary']
            self.assertEqual(summary['invoice_count'], 9)
            self.assertEqual(summary['open_count'], 7)
            self.assertEqual(summary['outstanding'], 3698.19)
            unmatched = reporting.overview(db)['unmatched_payments']
            self.assertEqual([u['payment_id'] for u in unmatched], ['KEEP-U1'])
            keep = next(r for r in reporting.invoices(db) if r['invoice_number'] == 'HOME-0' or r['customer_id'] == 'NORTH' and r['invoice_number'] == 'KEEP-702')
            self.assertEqual(keep['balance'], 0.0)
        finally:
            db.close()

    def test_new_imports_and_restart_preserve_everything(self):
        db = storage.connect(self.db_path)
        try:
            result = importing.import_csv(
                db, f'{INVOICE_HEADER}\nHARBOR,NEW-1,777.77,2026-10-01\n', 'invoices')
            self.assertEqual(result['imported'], 1)
            result = importing.import_csv(
                db, f'{PAYMENT_HEADER}\nNEW-P1,HARBOR,NEW-1,277.77\n', 'payments')
            self.assertEqual(result['imported'], 1)
        finally:
            db.close()
        db = storage.connect(self.db_path)  # simulate restart
        try:
            summary = reporting.overview(db)['summary']
            self.assertEqual(summary['invoice_count'], 10)
            new = next(r for r in reporting.invoices(db) if r['invoice_number'] == 'NEW-1')
            self.assertEqual(new['amount'], 777.77)
            self.assertEqual(new['balance'], 500.00)
        finally:
            db.close()


def csv_rows(text):
    import csv
    return list(csv.DictReader(StringIO(text)))


def json_like(text):
    import json
    return json.loads(text)


if __name__ == '__main__':
    unittest.main()