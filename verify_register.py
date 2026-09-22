"""Existing-register workflow: restore the owner's database, import new records,
restart, and verify both original and new data survive. Uses the real app.py."""
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
PORT = 8792
BASE = f'http://127.0.0.1:{PORT}'


def request(method, path, body=None):
    data = body.encode('utf-8') if isinstance(body, str) else None
    headers = {'Content-Type': 'text/csv'} if data else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode('utf-8')
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode('utf-8')


def start_server():
    proc = subprocess.Popen([sys.executable, 'app.py', '--port', str(PORT)],
                            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            if request('GET', '/api/overview')[0] == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    raise SystemExit('server did not start')


def check(name, condition, detail=''):
    print(f'{("PASS" if condition else "FAIL")}  {name}' + (f'  [{detail}]' if detail else ''))
    if not condition:
        raise SystemExit(f'VERIFICATION FAILED at: {name}')


def main():
    subprocess.run([sys.executable, 'restore_fixture.py', '--replace'], cwd=ROOT, check=True)

    proc = start_server()
    try:
        status, body = request('GET', '/api/overview')
        ov = json.loads(body)['summary']
        check('restored invoice_count=9', ov['invoice_count'] == 9, str(ov['invoice_count']))
        check('restored open_count=7', ov['open_count'] == 7, str(ov['open_count']))
        check('restored outstanding=3698.19',
              abs(ov['outstanding'] - 3698.19) < 1e-9, str(ov['outstanding']))
        status, body = request('GET', '/api/overview')
        unmatched = [u['payment_id'] for u in json.loads(body)['unmatched_payments']]
        check('KEEP-U1 stays unmatched', unmatched == ['KEEP-U1'], str(unmatched))

        status, body = request('POST', '/api/import?kind=invoices',
                               'customer_id,invoice_number,amount,due_date\nHARBOR,NEW-1,777.77,2026-10-01\n')
        check('new invoice import', json.loads(body)['imported'] == 1)
        status, body = request('POST', '/api/import?kind=payments',
                               'payment_id,customer_id,invoice_number,amount\nNEW-P1,HARBOR,NEW-1,277.77\n')
        check('new payment import', json.loads(body)['imported'] == 1)
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # Simulate an app restart: new server process against the same database.
    proc = start_server()
    try:
        status, body = request('GET', '/api/invoices?status=all')
        rows = {x['invoice_number']: x for x in json.loads(body)}
        check('KEEP records present after restart', all(n in rows for n in
              ('KEEP-700', 'KEEP-702', 'INV-100', 'INV-200', 'INV-300', 'INV-101', 'INV-201', 'INV-301')))
        check('NEW-1 present after restart', 'NEW-1' in rows, str(rows.get('NEW-1')))
        check('NEW-1 balance after restart', rows['NEW-1']['balance'] == 500.0,
              str(rows['NEW-1']['balance']))
        status, body = request('GET', '/api/export')
        exported = list(csv.DictReader(io.StringIO(body)))
        check('export still consistent after restart', len(exported) == 10)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    print('\nExisting-register workflow passed.')


if __name__ == '__main__':
    main()