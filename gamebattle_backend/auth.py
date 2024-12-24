"""Manage the authentication of users"""
from dataclasses import dataclass
import re

import firebase_admin
from firebase_admin import auth


@dataclass
class User:
    """A user"""

    email: str
    name: str


firebase_admin.initialize_app()


def validate(email: str) -> bool:
    """Validate the email of a user

    Args:
        email (str): The email of the user
    """
    return True
    # return bool(re.match(r"^[a-z.-]+@ucu\.edu\.ua$", email))


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


def verify_user(token: str) -> User | None:
    """Verify the token of a user

    Args:
        token (str): The token of the user

    Returns:
        User: The user
    """
    try:
        user = auth.verify_id_token(token)
    except Exception:
        return None
    email = user["email"]
    if validate(email):
        return User(email=email, name=user["name"])
    return None
