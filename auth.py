import re

import firebase_admin
from firebase_admin import auth, credentials


class AuthManager:
    """Manage the authentication of users"""

    def __init__(self):
        self.cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(self.cred)

    def validate(self, email):
        """Validate the email of a user"""
        if re.match(r"^[a-z.]+\.pn@ucu\.edu\.ua$", email):
            return True
        return False

    def verify(self, token):
        """Verify the token of a user"""
        try:
            user = auth.verify_id_token(token)
        except Exception:
            return None
        email = user["email"]
        if self.validate(email):
            return email
        raise ValueError("Invalid email")
