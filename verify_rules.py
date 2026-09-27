"""BUSINESS_RULES.md compliance checks against a live app.py server.
Self-contained: resets the demo database, starts the server, runs checks, stops it.
Exits non-zero on the first failed rule."""
import csv
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8793
BASE = f'http://127.0.0.1:{PORT}'


def request(method, path, body=None, text=True):
    data = body.encode('utf-8') if isinstance(body, str) else None
    headers = {'Content-Type': 'text/csv'} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return resp.status, (raw.decode('utf-8') if text else raw)


def get_json(path):
    return json.loads(request('GET', path)[1])


def post_csv(kind, csv_text):
    status, body = request('POST', f'/api/import?kind={kind}', csv_text)
    return status, json.loads(body)


def check(name, condition, details=''):
    print(f'{"PASS" if condition else "FAIL"}  {name}' + (f'  [{details}]' if details else ''))
    if not condition:
        raise SystemExit(f'RULE FAILED: {name}')


def start_server():
    subprocess.run([sys.executable, 'app.py', 'reset-demo'], cwd=ROOT, check=True)
    proc = subprocess.Popen([sys.executable, 'app.py', '--port', str(PORT)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            if get_json('/api/overview')['summary']['invoice_count'] == 6:
                return proc
        except Exception:
            time.sleep(0.2)
    raise SystemExit('server did not start')


def main():
    proc = start_server()
    try:
        print('1. INVOICE IDENTITY')
        _, r = post_csv('invoices',
                        'customer_id,invoice_number,amount,due_date\nHARBOR,DUP001,1000.00,2026-10-01\nHARBOR,DUP001,1000.00,2026-10-01\n')
        check('identical duplicate skips, does not duplicate totals',
              r['imported'] == 1 and r['skipped'] == 1, str(r))

        print('\n2. IDENTITY CONFLICT DETECTION')
        _, r = post_csv('invoices',
                        'customer_id,invoice_number,amount,due_date\nHARBOR,CONFLICT,1000.00,2026-10-01\nHARBOR,CONFLICT,2000.00,2026-10-01\n')
        check('reused identity with changed details rejected, original preserved',
              r['imported'] == 1 and r['rejected'] == 1, str(r))

        print('\n3. MONEY PRECISION (TWO DECIMALS, PRESERVED CENTS)')
        post_csv('invoices', 'customer_id,invoice_number,amount,due_date\nHARBOR,CENTS-1,1400.12,2026-10-01\n')
        post_csv('payments', 'payment_id,customer_id,invoice_number,amount\nCP-1,HARBOR,CENTS-1,444.45\n')
        inv = next(i for i in get_json('/api/invoices?status=all') if i['invoice_number'] == 'CENTS-1')
        cents = lambda v: v is not None and round(abs(v * 100 - round(v * 100)), 9) < 1e-6
        check('calculated sums preserve cents (955.67, not truncated)', inv['balance'] == 955.67,
              repr(inv['balance']))
        check('JSON money values are numbers with cents', all(cents(v) for v in
              (inv['amount'], inv['paid'], inv['balance'])))

        print('\n4. STATUS FILTERING')
        open_rows = get_json('/api/invoices?status=open')
        paid_rows = get_json('/api/invoices?status=paid')
        all_rows = get_json('/api/invoices?status=all')
        check('open filter: only positive balances', all(x['balance'] > 0 for x in open_rows))
        check('paid filter: only zero/negative balances', all(x['balance'] <= 0 for x in paid_rows))
        ov = get_json('/api/overview')['summary']
        check('open view count == overview open_count', len(open_rows) == ov['open_count'],
              f'{len(open_rows)} vs {ov["open_count"]}')

        print('\n5. OVERPAYMENT ISOLATION')
        before = get_json('/api/overview')['summary']['outstanding']
        post_csv('payments', 'payment_id,customer_id,invoice_number,amount\nOV-1,HARBOR,INV-101,10.00\n')
        rows = {x['invoice_number']: x for x in get_json('/api/invoices?status=all')}
        check('overpaid invoice goes negative and is paid',
              rows['INV-101']['balance'] == -10.0 and rows['INV-101']['status'] == 'paid')
        check('overpayment does not reduce any other outstanding',
              rows['INV-100']['balance'] == 1250.0 and
              get_json('/api/overview')['summary']['outstanding'] == before)

        print('\n6. EXPORT CONSISTENCY')
        status, body = request('GET', '/api/export')
        exported = {e['invoice_number']: e for e in csv.DictReader(io.StringIO(body))}
        check('export header present and rows > 1', status == 200 and len(exported) > 1)
        agree = all(exported[n]['balance'] == f"{rows[n]['balance']:.2f}" and
                    exported[n]['paid'] == f"{rows[n]['paid']:.2f}" for n in exported)
        check('export money agrees with screen at cents', agree)

        print('\n7. PARTIAL IMPORT FEEDBACK')
        _, r = post_csv('invoices', 'customer_id,invoice_number,amount,due_date\nHARBOR,INV-103,84.00,2026-09-12\nNORTH,INV-302,bad,2026-09-12\nMAPLE,INV-203,100.00,2026-09-13\n')
        check('mixed CSV: imported=2 rejected=1, reason on line 3',
              r['imported'] == 2 and r['rejected'] == 1 and r['errors'][0]['line'] == 3, str(r))

        print('\n8. IMPROVEMENT: RECONCILIATION')
        post_csv('payments', 'payment_id,customer_id,invoice_number,amount\nPAY-FLOAT,MAPLE,WAIT-999,33.33\n')
        rec = get_json('/api/reconciliation')
        check('reconciliation endpoint reports clean register', rec['issue_count'] == 0, str(rec))
        check('reconciliation reports unmatched payments', rec['unmatched_payments'] == 1, str(rec))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print('\nAll business-rule checks passed.')


if __name__ == '__main__':
    main()