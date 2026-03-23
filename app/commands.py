# app/commands.py
import click
from flask import current_app
from flask.cli import with_appcontext
from datetime import datetime, timedelta
from app.services.db import DB_ENGINE
from sqlalchemy import text
from app.services.email import send_email

def register_commands(app):
    @app.cli.command('send-invoice-reminders')
    @with_appcontext
    def send_invoice_reminders():
        """Send reminders for overdue invoices."""
        today = datetime.now().date()
        # Find unpaid invoices with due_date < today and no reminder sent in the last 7 days (optional)
        with DB_ENGINE.connect() as conn:
            invoices = conn.execute(text("""
                SELECT id, invoice_number, client_name, client_email, due_date, grand_total
                FROM user_invoices
                WHERE status != 'paid'
                  AND due_date IS NOT NULL
                  AND due_date < :today
                  AND (last_reminder_sent IS NULL OR last_reminder_sent < :cutoff)
                ORDER BY due_date ASC
            """), {
                "today": today,
                "cutoff": today - timedelta(days=7)   # send at most once per week
            }).fetchall()

        count = 0
        for inv in invoices:
            inv_id, inv_num, client_name, client_email, due_date, total = inv
            if not client_email:
                continue
            # Send reminder to client
            subject = f"Invoice {inv_num} Overdue"
            body = f"""
Dear {client_name},

Invoice {inv_num} of amount {total} was due on {due_date}.
Please arrange payment at your earliest convenience.

Thank you,
Groweasy
"""
            if send_email([client_email], subject, body):
                count += 1
                # Update last_reminder_sent
                with DB_ENGINE.begin() as conn:
                    conn.execute(text("""
                        UPDATE user_invoices
                        SET last_reminder_sent = :now
                        WHERE id = :id
                    """), {"now": datetime.now(), "id": inv_id})

        click.echo(f"Sent {count} overdue invoice reminders.")
