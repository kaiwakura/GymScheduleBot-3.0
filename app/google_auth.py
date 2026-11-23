"""Google sign-in and the user's stored Google connection.

The connection (OAuth tokens, the GymScheduleBot calendar's id, sync bookkeeping) is
encrypted with Fernet before it goes into the session cookie. The browser can't read
it, and neither can anyone who copies the cookie without the server's key. The cookie
itself is HttpOnly, Secure and SameSite=Lax (see main.py).

Everything reads and writes the connection through load_connection, save_connection
and clear_connection. Moving to server-side sessions later only means changing those.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

import requests
from cryptography.fernet import Fernet, InvalidToken
from google.auth.exceptions import RefreshError, TransportError
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow

log = logging.getLogger(__name__)

# The narrowest scopes that do the job:
SCOPES = [
    # when each of your calendars is busy, never what the events are
    "https://www.googleapis.com/auth/calendar.events.freebusy",
    # the names of your calendars, for the "which calendars count" checklist
    "https://www.googleapis.com/auth/calendar.calendarlist.readonly",
    # create the GymScheduleBot calendar and manage events on it, and nothing else
    "https://www.googleapis.com/auth/calendar.app.created",
]
TOKEN_URI = "https://oauth2.googleapis.com/token"
REVOKE_URI = "https://oauth2.googleapis.com/revoke"
SESSION_KEY = "google"

# Google may return granted scopes in a different order or format than requested.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")


class GoogleAuthError(Exception):
    """The user's Google access expired, was revoked, or is missing a permission."""


@dataclass
class GoogleConnection:
    token: str | None
    refresh_token: str | None
    expiry: str | None = None  # ISO, UTC
    calendar_id: str | None = None  # the GymScheduleBot calendar
    last_sync: str | None = None  # ISO, when workouts were last synced
    # Days GymScheduleBot itself removed a workout from, so a later plan for that day
    # isn't mistaken for restoring a workout the user deleted.
    removed_days: list[str] = field(default_factory=list)


# --------------------------------------------------------------- config ---
def is_configured() -> bool:
    """Google needs OAuth credentials plus the keys that protect stored tokens."""
    needed = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "TOKEN_ENCRYPTION_KEY")
    if not all(os.getenv(name) for name in needed):
        return False
    if not os.getenv("SESSION_SECRET") and not local_development():
        log.warning("Google Calendar is off: set SESSION_SECRET so sessions survive restarts")
        return False
    return _fernet() is not None


def local_development() -> bool:
    return os.getenv("OAUTHLIB_INSECURE_TRANSPORT") == "1"


def _fernet() -> Fernet | None:
    try:
        return Fernet(os.environ["TOKEN_ENCRYPTION_KEY"].encode())
    except (KeyError, ValueError):
        log.warning("TOKEN_ENCRYPTION_KEY is missing or isn't a valid Fernet key")
        return None


# ----------------------------------------------------- stored connection ---
def load_connection(session: dict) -> GoogleConnection | None:
    blob = session.get(SESSION_KEY)
    fernet = _fernet() if blob else None
    if not blob or fernet is None:
        return None
    try:
        data = json.loads(fernet.decrypt(blob.encode()))
        return GoogleConnection(**data)
    except (InvalidToken, ValueError, TypeError):
        # The key changed or the cookie was tampered with: treat it as disconnected.
        session.pop(SESSION_KEY, None)
        return None


def save_connection(session: dict, connection: GoogleConnection) -> None:
    fernet = _fernet()
    if fernet is None:
        raise GoogleAuthError("Token encryption isn't configured.")
    session[SESSION_KEY] = fernet.encrypt(json.dumps(asdict(connection)).encode()).decode()


def clear_connection(session: dict) -> None:
    session.pop(SESSION_KEY, None)


# ---------------------------------------------------------------- sign-in ---
def _flow(state: str | None = None) -> Flow:
    client_config = {
        "web": {
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": TOKEN_URI,
        }
    }
    redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8000/auth/google/callback")
    return Flow.from_client_config(client_config, scopes=SCOPES, state=state, redirect_uri=redirect_uri)


def start_login() -> tuple[str, str, str | None]:
    """Returns (url to send the user to, state, PKCE code verifier)."""
    flow = _flow()
    # offline + consent: Google returns a refresh token, so the user stays connected.
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    return url, state, flow.code_verifier


def finish_login(code: str, state: str, code_verifier: str | None) -> GoogleConnection:
    """Exchange the code Google sent back for tokens."""
    flow = _flow(state)
    flow.code_verifier = code_verifier
    flow.fetch_token(code=code)
    creds = flow.credentials
    granted = set(getattr(creds, "granted_scopes", None) or SCOPES)
    if not set(SCOPES) <= granted:
        # Google lets people untick individual permissions on the consent screen.
        raise GoogleAuthError("GymScheduleBot needs all three Google Calendar permissions to work.")
    return GoogleConnection(creds.token, creds.refresh_token, _iso(creds.expiry))


def _iso(expiry: datetime | None) -> str | None:
    # google-auth uses naive UTC datetimes for expiry.
    return expiry.replace(tzinfo=UTC).isoformat() if expiry else None


# ----------------------------------------------------------- credentials ---
def credentials(connection: GoogleConnection) -> tuple[Credentials, bool]:
    """Working credentials for this connection, refreshed if the access token expired.

    Returns (credentials, changed). When `changed` is True the caller should save the
    connection again, since it now holds a new access token.
    """
    expiry = datetime.fromisoformat(connection.expiry).replace(tzinfo=None) if connection.expiry else None
    creds = Credentials(
        token=connection.token,
        refresh_token=connection.refresh_token,
        token_uri=TOKEN_URI,
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
        expiry=expiry,
    )
    if creds.valid and expiry and expiry - timedelta(minutes=1) > datetime.now(UTC).replace(tzinfo=None):
        return creds, False
    if not connection.refresh_token:
        raise GoogleAuthError("Google access expired.")
    try:
        creds.refresh(GoogleRequest())
    except RefreshError as exc:  # revoked, expired (7 days in Google's testing mode), or password changed
        raise GoogleAuthError("Google access expired or was revoked.") from exc
    except TransportError:
        raise  # network trouble: not an auth problem, the caller keeps the connection
    connection.token, connection.expiry = creds.token, _iso(creds.expiry)
    return creds, True


def revoke(connection: GoogleConnection) -> bool:
    """Ask Google to forget this app's access. Best effort: disconnecting works either way."""
    token = connection.refresh_token or connection.token
    if not token:
        return False
    try:
        response = requests.post(REVOKE_URI, params={"token": token}, timeout=10)
        return response.status_code == 200
    except requests.RequestException:
        return False
