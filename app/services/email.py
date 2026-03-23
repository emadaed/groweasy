# app/services/email.py
from flask import current_app
from flask_mail import Message

def send_email(recipients, subject, body):
    if not recipients:
        return False
    try:
        # Get the mail extension from the current app
        mail = current_app.extensions['mail']
        msg = Message(subject, recipients=recipients)
        msg.body = body
        mail.send(msg)
        return True
    except Exception as e:
        current_app.logger.error(f"Email send failed: {e}")
        return False
