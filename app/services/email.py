# app/services/email.py
from flask import current_app
from flask_mail import Message
from app import mail

def send_email(recipients, subject, body):
    """Send an email to a list of recipients."""
    if not recipients:
        return False
    try:
        msg = Message(subject, recipients=recipients)
        msg.body = body
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Email send failed: {e}")
        return False
