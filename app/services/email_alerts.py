# app/services/email_alerts.py
from datetime import datetime, timedelta
from sqlalchemy import text
from app.services.db import DB_ENGINE
from app.services.email import send_email

def send_overdue_invoice_reminders(account_id, owner_emails):
    print(f"Checking overdue invoices for account {account_id}")
    """
    Find unpaid invoices with due_date < today and last_reminder_sent > 7 days ago.
    Send a reminder to the client (if client_email exists) and update last_reminder_sent.
    Returns number of reminders sent.
    """
    today = datetime.now().date()
    cutoff = today - timedelta(days=7)

    with DB_ENGINE.connect() as conn:
        rows = conn.execute(text("""
            SELECT 
                id, 
                invoice_number, 
                client_name, 
                invoice_data::jsonb->>'client_email' AS client_email,
                due_date, 
                grand_total
            FROM user_invoices
            WHERE account_id = :aid
              AND status != 'paid'
              AND due_date IS NOT NULL
              AND due_date < :today
              AND (last_reminder_sent IS NULL OR last_reminder_sent < :cutoff)
        """), {"aid": account_id, "today": today, "cutoff": cutoff}).fetchall()
        print(f"Found {len(rows)} overdue invoices")

    if not rows:
        return 0

    sent_count = 0
    for row in rows:
        inv_id, inv_num, client_name, client_email, due_date, total = row
        if not client_email:
            continue

        subject = f"Invoice {inv_num} Overdue"
        body = f"""
Dear {client_name},

Invoice {inv_num} of amount {total} was due on {due_date}.
Please arrange payment at your earliest convenience.

Thank you,
Groweasy
"""
        if send_email([client_email], subject, body):
            sent_count += 1
            with DB_ENGINE.begin() as conn:
                conn.execute(text("""
                    UPDATE user_invoices
                    SET last_reminder_sent = NOW()
                    WHERE id = :id
                """), {"id": inv_id})
                print(f"Sent reminder for invoice {inv_num} to {client_email}")            

    return sent_count
