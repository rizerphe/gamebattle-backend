import random
import string
from dataclasses import dataclass

import flask
import flask_cors
import flask_restful

from containers import Manager as GameManager

app = flask.Flask(__name__)
cors = flask_cors.CORS(app)
api = flask_restful.Api(app)


@dataclass
class Limits:
    games_per_user: int = 5


class AuthManager:
    def __init__(self, limits: Limits = Limits()):
        self.sessions = {}
        self.limits = limits

    def create_session(self, user):
        id_ = "".join(random.choice(string.ascii_letters) for _ in range(16))
        self.sessions[id_] = user
        return id_

    def get_user(self, session_id):
        return self.sessions.get(session_id)


class Sessions(flask_restful.Resource):
    def __init__(self, auth_manager: AuthManager):
        self.auth_manager = auth_manager

    def post(self):
        user = flask.request.json["user"]
        return {"session": self.auth_manager.create_session(user)}


class Games(flask_restful.Resource):
    def __init__(self, auth_manager: AuthManager, game_manager: GameManager):
        self.auth_manager = auth_manager
        self.game_manager = game_manager

    def get(self, session_id):
        user = self.auth_manager.get_user(session_id)
        if user is None:
            return {"error": "invalid session id"}, 401
        return {
            "games": [
                {
                    "name": game.name,
                    "author": game.author,
                    "start_time": game.start_time,
                }
                for game in self.game_manager.get_games(user)
            ]
        }

    def post(self, session_id):
        user = self.auth_manager.get_user(session_id)
        if user is None:
            return {"error": "invalid session id"}, 401
        if (
            len(self.game_manager.get_games(user))
            >= self.auth_manager.limits.games_per_user
        ):
            return {"error": "too many games"}, 403
        game = self.game_manager.start_game(user)
        return {
            "name": game.name,
            "author": game.author,
            "start_time": game.start_time,
        }


class Game(flask_restful.Resource):
    def __init__(self, auth_manager: AuthManager, game_manager: GameManager):
        self.auth_manager = auth_manager
        self.game_manager = game_manager

    def get(self, session_id, game_name):
        user = self.auth_manager.get_user(session_id)
        if user is None:
            return {"error": "invalid session id"}, 401
        game = self.game_manager.get_game(user, game_name)
        if game is None:
            return {"error": "game not found"}, 404
        return {
            "name": game.name,
            "author": game.author,
            "start_time": game.start_time,
            "output": game.output(),
        }

    def delete(self, session_id, game_name):
        user = self.auth_manager.get_user(session_id)
        if user is None:
            return {"error": "invalid session id"}, 401
        game = self.game_manager.get_game(user, game_name)
        if game is None:
            return {"error": "game not found"}, 404
        game.kill()
        return {"message": "game killed"}

    def post(self, session_id, game_name):
        user = self.auth_manager.get_user(session_id)
        if user is None:
            return {"error": "invalid session id"}, 401
        game = self.game_manager.get_game(user, game_name)
        if game is None:
            return {"error": "game not found"}, 404
        data = flask.request.get_json()
        if data is None:
            return {"error": "invalid json"}, 400
        if "text" not in data:
            return {"error": "missing text"}, 400
        return game.stdin(data["text"])


auth_manager = AuthManager()
game_manager = GameManager()

api.add_resource(
    Sessions, "/sessions", resource_class_kwargs={"auth_manager": auth_manager}
)
api.add_resource(
    Games,
    "/games/<session_id>",
    resource_class_kwargs={"auth_manager": auth_manager, "game_manager": game_manager},
)
api.add_resource(
    Game,
    "/games/<session_id>/<game_name>",
    resource_class_kwargs={"auth_manager": auth_manager, "game_manager": game_manager},
)

if __name__ == "__main__":
    app.run(debug=True)