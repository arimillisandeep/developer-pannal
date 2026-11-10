
def main():
    from app.services.email import send_email

    recipient = input("Enter your test email address: ").strip()
    try:
        result = send_email(
            to_email=recipient,
            subject="WORKCODE Email Test",
            body=(
                "Hello!\n\n"
                "This is a test email from WORKCODE.\n"
                "Your Gmail API email service is working successfully."
            ),
        )
        if result:
            print("SUCCESS: Email sent. Check your inbox.")
    except Exception as error:
        print("FAILED:", error)


if __name__ == "__main__":
    main()
