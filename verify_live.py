"""Live verification: run the real app.py server and drive the owner workflows
with the supplied sample files. Exits non-zero on the first failed check."""
import csv
import io
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PORT = 8791
BASE = f'http://127.0.0.1:{PORT}'


def request(method, path, body=None, headers=None):
    headers = dict(headers or {})
    data = body.encode('utf-8') if isinstance(body, str) else body
    if data:
        headers.setdefault('Content-Type', 'text/csv')
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8')


def check(name, condition, detail=''):
    print(f'{("PASS" if condition else "FAIL")}  {name}' + (f'  [{detail}]' if detail else ''))
    if not condition:
        raise SystemExit(f'VERIFICATION FAILED at: {name}')


def reset_db():
    subprocess.run([sys.executable, 'app.py', 'reset-demo'], cwd=ROOT, check=True)


def start_server():
    proc = subprocess.Popen([sys.executable, 'app.py', '--port', str(PORT)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            status, _ = request('GET', '/api/overview')
            if status == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    raise SystemExit('server did not start')


def main():
    reset_db()
    proc = start_server()
    try:
        # 1. Baseline fresh demo
        status, body = request('GET', '/api/overview')
        ov = json.loads(body)
        check('baseline invoice_count', status == 200 and ov['summary']['invoice_count'] == 6)
        check('baseline open_count', ov['summary']['open_count'] == 5)
        check('baseline outstanding', abs(ov['summary']['outstanding'] - 3209.99) < 1e-9)

        # 2. Mixed invoice CSV: valid rows imported, bad row rejected with line number
        status, body = request('POST', '/api/import?kind=invoices',
                               (ROOT / 'samples' / 'invoices-mixed.csv').read_text('utf-8'))
        r = json.loads(body)
        check('mixed import HTTP 200', status == 200)
        check('mixed import imported=2', r['imported'] == 2, str(r))
        check('mixed import rejected=1 line=3',
              r['rejected'] == 1 and r['errors'][0]['line'] == 3, str(r['errors']))

        # 3. Retry same CSV is safe: identical rows skip, totals unchanged
        status, body = request('GET', '/api/overview')
        before = json.loads(body)['summary']['outstanding']
        status, body = request('POST', '/api/import?kind=invoices',
                               (ROOT / 'samples' / 'invoices-mixed.csv').read_text('utf-8'))
        r = json.loads(body)
        check('retry mixed import skipped=2 rejected=1',
              r['skipped'] == 2 and r['rejected'] == 1, str(r))
        status, body = request('GET', '/api/overview')
        after = json.loads(body)['summary']['outstanding']
        check('retry does not move totals', before == after, f'{before} vs {after}')

        # 4. Payment with amount equal to another invoice must attach to ITS reference
        status, body = request('POST', '/api/import?kind=payments',
                               (ROOT / 'samples' / 'payments.csv').read_text('utf-8'))
        r = json.loads(body)
        check('payments imported=3', r['imported'] == 3, str(r))
        status, body = request('GET', '/api/invoices?status=all')
        rows = {x['invoice_number']: x for x in json.loads(body)}
        check('PAY-201 credits MAPLE/INV-200 not HARBOR/INV-100',
              rows['INV-200']['paid'] == 1250.0 and rows['INV-100']['paid'] == 0.0)

        # 5. Wrong header is 400, never "success"
        status, body = request('POST', '/api/import?kind=invoices',
                               (ROOT / 'samples' / 'wrong-header.csv').read_text('utf-8'))
        check('wrong header returns 400 with error',
              status == 400 and 'error' in body, f'{status} {body[:60]}')

        # 6. Export agrees with the screen for every record
        status, body = request('GET', '/api/export')
        exported = {e['invoice_number']: e for e in csv.DictReader(io.StringIO(body))}
        status, body = request('GET', '/api/invoices?status=all')
        screen = {x['invoice_number']: x for x in json.loads(body)}
        agree = all(exported[n]['balance'] == f"{screen[n]['balance']:.2f}" and
                    exported[n]['paid'] == f"{screen[n]['paid']:.2f}"
                    for n in exported)
        check('export matches screen at cents', agree)

        # 7. open filter agrees with the overview open count
        status, body = request('GET', '/api/invoices?status=open')
        open_rows = json.loads(body)
        check('open view == overview open_count',
              len(open_rows) == ov['summary']['open_count'], f"{len(open_rows)} vs {ov['summary']['open_count']}")
        check('open view contains only open',
              all(x['status'] == 'open' for x in open_rows))

        # 8. Reconciliation improvement reports a clean register
        status, body = request('GET', '/api/reconciliation')
        rec = json.loads(body)
        check('reconciliation clean after imports', rec['issue_count'] == 0, str(rec))
        check('reconciliation reports unmatched payment',
              rec['unmatched_payments'] == 1, str(rec))

        # 9. Unmatched payment PAY-404 remains visible
        status, body = request('GET', '/api/overview')
        unm = [u['payment_id'] for u in json.loads(body)['unmatched_payments']]
        check('PAY-404 stays unmatched', 'PAY-404' in unm, str(unm))
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print('\nAll live checks passed.')


if __name__ == '__main__':
    main()