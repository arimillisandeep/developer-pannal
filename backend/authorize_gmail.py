
import os
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE = os.path.join(BASE_DIR, "token.json")


def authorize_gmail():
    if not os.path.exists(CREDENTIALS_FILE):
        raise FileNotFoundError("credentials.json not found in backend folder")

    flow = InstalledAppFlow.from_client_secrets_file(
        CREDENTIALS_FILE,
        SCOPES
    )

    credentials = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w", encoding="utf-8") as token:
        token.write(credentials.to_json())

    print("Gmail authorization successful!")
    print("token.json created successfully.")


if __name__ == "__main__":
    authorize_gmail()
