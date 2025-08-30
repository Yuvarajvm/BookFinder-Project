# aws_email.py
import os, sqlite3, secrets
from datetime import datetime, timedelta

from flask_mail import Mail, Message
from dotenv import load_dotenv

load_dotenv()


class AWSEmailService:
    """
    AWS SES email service using Flask-Mail + SQLite token store.
    """

    def __init__(self, app=None):
        if app:
            self.init_app(app)

    def init_app(self, app):
        app.config.update(
            MAIL_SERVER=os.getenv("AWS_SES_SMTP_SERVER"),
            MAIL_PORT=int(os.getenv("AWS_SES_SMTP_PORT", 587)),
            MAIL_USE_TLS=True,
            MAIL_USE_SSL=False,
            MAIL_USERNAME=os.getenv("AWS_SES_SMTP_USER"),
            MAIL_PASSWORD=os.getenv("AWS_SES_SMTP_PASS"),
            MAIL_DEFAULT_SENDER=os.getenv("AWS_DEFAULT_SENDER"),
        )
        self.mail = Mail(app)
        self.app_url = os.getenv("APP_URL", "http://localhost:5000")

    def init_reset_table(self):
        with sqlite3.connect("instance/bookfinder.db") as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS password_reset_tokens(
                       id INTEGER PRIMARY KEY AUTOINCREMENT,
                       user_id INTEGER NOT NULL,
                       token TEXT UNIQUE,
                       expires_at TIMESTAMP,
                       used INTEGER DEFAULT 0
                   )"""
            )

    def create_reset_token(self, user_id):
        token = secrets.token_urlsafe(32)
        expires = datetime.utcnow() + timedelta(hours=1)
        with sqlite3.connect("instance/bookfinder.db") as c:
            c.execute("DELETE FROM password_reset_tokens WHERE user_id=?", (user_id,))
            c.execute(
                "INSERT INTO password_reset_tokens(user_id,token,expires_at) VALUES(?,?,?)",
                (user_id, token, expires.isoformat()),
            )
        return token

    def verify_token(self, token):
        with sqlite3.connect("instance/bookfinder.db") as c:
            row = c.execute(
                "SELECT user_id,expires_at,used FROM password_reset_tokens WHERE token=?",
                (token,),
            ).fetchone()
        if not row or row[2] or datetime.utcnow() > datetime.fromisoformat(row[1]):
            return None
        return row[0]

    def mark_token_used(self, token):
        with sqlite3.connect("instance/bookfinder.db") as c:
            c.execute("UPDATE password_reset_tokens SET used=1 WHERE token=?", (token,))

    def _send(self, **kwargs):
        try:
            self.mail.send(Message(**kwargs))
            return True
        except Exception as e:
            print("Email error:", e)
            return False

    def send_password_reset(self, email, token):
        link = f"{self.app_url}/reset-password/{token}"
        return self._send(
            subject="🔐 BookFinder Password Reset",
            recipients=[email],
            body=f"Reset your password: {link} (expires in 1 hour)",
            html=f'Click <a href="{link}">here</a> to reset your password.<br>This link expires in 1 hour.',
        )

    def send_welcome_email(self, email, username):
        return self._send(
            subject=f"🎉 Welcome to BookFinder, {username}!",
            recipients=[email],
            body=f"Hi {username}, welcome to BookFinder!",
            html=f"<h3>Hi {username}!</h3><p>Welcome to BookFinder.</p>",
        )
