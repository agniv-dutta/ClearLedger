import csv
import io


def invoices(db, status='all'):
    if status not in ('all', 'open', 'paid'):
        raise ValueError('status must be all, open or paid')
    data = db.execute('''
        SELECT i.id, i.customer_id, c.name AS customer_name, i.invoice_number,
               i.amount, i.due_date, COALESCE(SUM(p.amount), 0) AS paid
        FROM invoices i JOIN customers c ON c.customer_id=i.customer_id
        LEFT JOIN payments p ON p.invoice_id=i.id
        GROUP BY i.id ORDER BY i.id
    ''').fetchall()
    result = []
    for row in data:
        item = dict(row)
        item['paid'] = round(item['paid'], 2)
        item['balance'] = round(item['amount'] - item['paid'], 2)
        item['status'] = 'paid' if item['balance'] <= 0 else 'open'
        result.append(item)
    if status != 'all':
        result = [r for r in result if r['status'] == status]
    return result


def overview(db):
    rows = invoices(db)
    unmatched = [dict(r) for r in db.execute('''SELECT payment_id, customer_id,
        invoice_number, amount FROM payments WHERE invoice_id IS NULL ORDER BY payment_id''')]
    return {'invoices': rows, 'unmatched_payments': unmatched, 'summary': {
        'invoice_count': len(rows),
        'open_count': sum(r['status'] == 'open' for r in rows),
        'outstanding': round(sum(max(0, r['balance']) for r in rows), 2),
    }}


def export_csv(db):
    output = io.StringIO(newline='')
    fields = ['customer_id', 'invoice_number', 'amount', 'paid', 'balance', 'status']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in invoices(db):
        item = {k: row[k] for k in fields}
        for key in ('amount', 'paid', 'balance'):
            item[key] = f"{item[key]:.2f}"
        writer.writerow(item)
    return output.getvalue()


def reconciliation(db):
    """Health check for a register the owner can trust. Flags data inconsistencies
    that are not visible on the normal screens: duplicate invoice identities and
    payments attached to the wrong invoice. Unmatched payments are reported for
    awareness rather than flagged as errors."""
    issues = []
    for row in db.execute('''SELECT customer_id, invoice_number, COUNT(*) AS n
                             FROM invoices
                             GROUP BY customer_id, invoice_number HAVING n > 1''').fetchall():
        issues.append({'kind': 'duplicate_invoice',
                       'customer_id': row['customer_id'],
                       'invoice_number': row['invoice_number'],
                       'detail': f'{row["n"]} invoices share this identity'})
    for row in db.execute('''SELECT p.payment_id, p.customer_id, p.invoice_number,
                                    i.customer_id AS inv_customer_id,
                                    i.invoice_number AS inv_invoice_number
                             FROM payments p JOIN invoices i ON i.id = p.invoice_id
                             WHERE p.customer_id != i.customer_id
                                OR p.invoice_number != i.invoice_number
                             ORDER BY p.payment_id''').fetchall():
        issues.append({'kind': 'allocation_mismatch',
                       'payment_id': row['payment_id'],
                       'detail': (f'{row["customer_id"]}/{row["invoice_number"]} is attached to '
                                  f'invoice {row["inv_customer_id"]}/{row["inv_invoice_number"]}')})
    unmatched = db.execute('SELECT COUNT(*) AS n FROM payments WHERE invoice_id IS NULL').fetchone()[0]
    return {'issue_count': len(issues), 'issues': issues, 'unmatched_payments': unmatched}
