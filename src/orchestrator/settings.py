import json
import os
import re
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class Settings:
    terminal_service_url: str
    service_token: str
    mongo_url: str
    mongo_database: str
    admin_emails: frozenset[str]
    access_list: tuple[re.Pattern[str], ...] | None
    cors_origins: tuple[str, ...]
    firebase_credentials: dict
    firebase_project_id: str
    s3_endpoint: str
    s3_region: str
    s3_access_key: str
    s3_secret_key: str
    s3_avatars_bucket: str
    avatar_cache_ttl: timedelta
    required_comparisons: int
    llm_base_url: str
    llm_api_key: str | None
    llm_model: str

    @classmethod
    def from_env(cls) -> "Settings":
        admin_emails = _admin_emails(os.environ.get("ADMIN_EMAILS", "[]"))
        return cls(
            terminal_service_url=os.environ.get(
                "TERMINAL_SERVICE_URL", "http://localhost:8080"
            ),
            service_token=_required("SERVICE_TOKEN"),
            mongo_url=_required("MONGO_URL"),
            mongo_database=os.environ.get("MONGO_DATABASE", "gamebattle"),
            admin_emails=admin_emails,
            access_list=_access_list(os.environ.get("ACCESS_LIST"), admin_emails),
            cors_origins=_cors_origins(os.environ.get("CORS_ORIGINS")),
            firebase_credentials=_json_object("FIREBASE_CREDENTIALS"),
            firebase_project_id=_required("FIREBASE_PROJECT_ID"),
            s3_endpoint=_required("S3_ENDPOINT"),
            s3_region=os.environ.get("S3_REGION", "us-east-1"),
            s3_access_key=_required("S3_ACCESS_KEY"),
            s3_secret_key=_required("S3_SECRET_KEY"),
            s3_avatars_bucket=os.environ.get("S3_AVATARS_BUCKET", "avatars"),
            avatar_cache_ttl=timedelta(
                seconds=_positive_int(os.environ.get("AVATAR_CACHE_TTL_SECONDS"), 86_400)
            ),
            required_comparisons=_positive_int(os.environ.get("REQUIRED_COMPARISONS"), 10),
            llm_base_url=os.environ.get("LLM_BASE_URL", "").strip()
            or "https://api.cerebras.ai/v1",
            llm_api_key=os.environ.get("LLM_API_KEY", "").strip() or None,
            llm_model=os.environ.get("LLM_MODEL", "").strip() or "gpt-oss-120b",
        )


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} must be set")
    return value


def _cors_origins(raw: str | None) -> tuple[str, ...]:
    """Origins allowed to call the API from a browser: a JSON list or a
    comma-separated string. Defaults to the local UI. ``*`` must be asked for."""

    if raw is None or not raw.strip():
        return ("http://localhost:3000",)
    text = raw.strip()
    if text.startswith("["):
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("CORS_ORIGINS must be a JSON list or comma-separated") from error
        if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
            raise ValueError("CORS_ORIGINS must be a JSON list of strings")
        items = data
    else:
        items = text.split(",")
    origins = tuple(item.strip().rstrip("/") for item in items if item.strip())
    if not origins:
        raise ValueError("CORS_ORIGINS must not be empty")
    return origins


def _json_object(name: str) -> dict:
    """The Firebase service-account key, passed as the JSON itself."""

    try:
        data = json.loads(_required(name))
    except json.JSONDecodeError as error:
        raise ValueError(f"{name} must be the service-account JSON") from error
    if not isinstance(data, dict):
        raise ValueError(f"{name} must be the service-account JSON")
    return data


def _positive_int(raw: str | None, default: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise ValueError(f"expected a positive integer, got {raw!r}") from error
    if value <= 0:
        raise ValueError(f"expected a positive integer, got {value}")
    return value


def _admin_emails(raw: str) -> frozenset[str]:
    if not raw.strip():
        return frozenset()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError("ADMIN_EMAILS must be a JSON list of strings") from error
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError("ADMIN_EMAILS must be a JSON list of strings")
    return frozenset(data)


def _access_list(
    raw: str | None, admins: frozenset[str]
) -> tuple[re.Pattern[str], ...] | None:
    """Email regexes allowed to use the service, plus every admin address.

    ``None`` (unset or empty) means everyone is allowed. Patterns must match the
    whole address, ignoring case.
    """

    if raw is None or not raw.strip():
        return None
    message = "ACCESS_LIST must be a JSON list of regular expressions"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(message) from error
    if not isinstance(data, list) or not all(isinstance(item, str) for item in data):
        raise ValueError(message)
    sources = [*data, *(re.escape(email) for email in sorted(admins))]
    try:
        return tuple(re.compile(source, re.IGNORECASE) for source in sources)
    except re.error as error:
        raise ValueError(f"ACCESS_LIST has an invalid regular expression: {error}") from error
