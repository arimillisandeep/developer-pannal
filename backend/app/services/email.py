
"""Reusable Gmail API email service for WORKCODE."""

import base64
import logging
from email.message import EmailMessage
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
TOKEN_FILE = BACKEND_DIR / "token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.send"]


def get_gmail_service():
    """Authenticate and return Gmail API service."""

    if not TOKEN_FILE.exists():
        raise RuntimeError("token.json not found. Run authorize_gmail.py first.")

    credentials = Credentials.from_authorized_user_file(
        str(TOKEN_FILE), SCOPES
    )

    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
        TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")

    if not credentials.valid:
        raise RuntimeError("Gmail credentials are invalid. Run authorize_gmail.py again.")

    return build("gmail", "v1", credentials=credentials)


def send_email(to_email: str, subject: str, body: str) -> bool:
    """Send a plain-text email using Gmail API."""

    try:
        message = EmailMessage()
        message["To"] = to_email
        message["Subject"] = subject
        message.set_content(body)

        encoded_message = base64.urlsafe_b64encode(
            message.as_bytes()
        ).decode("utf-8")

        service = get_gmail_service()

        service.users().messages().send(
            userId="me",
            body={"raw": encoded_message},
        ).execute()

        logger.info("Email sent successfully to %s", to_email)
        return True

    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        raise RuntimeError("Email delivery failed")
