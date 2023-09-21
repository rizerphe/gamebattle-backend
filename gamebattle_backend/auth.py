"""Manage the authentication of users"""
import re

import firebase_admin
from firebase_admin import auth


firebase_admin.initialize_app()


def validate(email: str) -> bool:
    """Validate the email of a user

    Args:
        email (str): The email of the user
    """
    return bool(re.match(r"^[a-z.]+\.pn@ucu\.edu\.ua$", email))


def verify(token: str) -> str | None:
    """Verify the token of a user

    Args:
        token (str): The token of the user

    Returns:
        str: The email of the user
    """
    try:
        user = auth.verify_id_token(token)
    except Exception:
        return None
    email = user["email"]
    if validate(email):
        return email
    return None
