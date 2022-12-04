import firebase_admin
from firebase_admin import auth, credentials


class AuthManager:
    """Manage the authentication of users"""

    def __init__(self):
        self.cred = credentials.Certificate("serviceAccountKey.json")
        firebase_admin.initialize_app(self.cred)

    def verify(self, token):
        """Verify the token of a user"""
        try:
            user = auth.verify_id_token(token)
            return user["email"]
        except Exception:
            return None
