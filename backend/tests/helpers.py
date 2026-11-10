"""Small reusable helpers for API tests."""


def auth(token: str) -> dict[str, str]:
    """Build a bearer authorization header."""
    return {"Authorization": f"Bearer {token}"}
