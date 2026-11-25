"""Google OAuth 2.0 sign-in with an email allowlist (stdlib only).

The protection switches on only when GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
are configured. Without them the site runs open (convenient for local preview).

Flow:
  /auth/login    -> redirect to Google consent (prompt=select_account)
  /auth/callback -> exchange code, verify id_token via Google tokeninfo,
                    check the allowlist, set a signed session cookie
  /auth/logout   -> clear the cookie
  /auth/me       -> JSON {enabled, email} for the header chip

Only emails on the allowlist may enter. The allowlist comes from the
ALLOWED_EMAILS env var and/or data/allowed_users.json. When the allowlist is
empty, any verified Google account is admitted (so the owner can sign in first);
add emails to restrict access to selected users.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = BASE_DIR / ".cache"

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
ALLOWED_EMAILS_RAW = os.environ.get("ALLOWED_EMAILS", "")

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
TOKENINFO_ENDPOINT = "https://oauth2.googleapis.com/tokeninfo"

SESSION_COOKIE = "nsehub_session"
SESSION_TTL = 7 * 86400
STATE_TTL = 600
UA = "NSEScannerHub/1.0"


def enabled() -> bool:
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


# --------------------------------------------------------------------------- #
# Session secret (persisted locally so restarts keep sessions)
# --------------------------------------------------------------------------- #

def _session_secret() -> str:
    env = os.environ.get("SESSION_SECRET", "").strip()
    if env:
        return env
    path = CACHE_DIR / "session_secret.txt"
    try:
        if path.exists():
            value = path.read_text(encoding="utf-8").strip()
            if value:
                return value
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32)
        path.write_text(value, encoding="utf-8")
        return value
    except Exception:  # noqa: BLE001
        return secrets.token_hex(32)


SECRET = _session_secret()


# --------------------------------------------------------------------------- #
# Allowlist
# --------------------------------------------------------------------------- #

def _allowlist() -> set[str]:
    emails: set[str] = set()
    for chunk in ALLOWED_EMAILS_RAW.replace(";", ",").replace(" ", ",").split(","):
        chunk = chunk.strip().lower()
        if chunk:
            emails.add(chunk)
    path = DATA_DIR / "allowed_users.json"
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, str) and item.strip():
                        emails.add(item.strip().lower())
            elif isinstance(data, dict):
                for item in data.get("emails", []):
                    if isinstance(item, str) and item.strip():
                        emails.add(item.strip().lower())
    except Exception:  # noqa: BLE001
        pass
    return emails


def is_allowed(email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return False
    allow = _allowlist()
    return True if not allow else email in allow


def allowlist_status() -> dict:
    allow = _allowlist()
    return {"restricted": bool(allow), "count": len(allow)}


# --------------------------------------------------------------------------- #
# Signed tokens (session cookie + OAuth state)
# --------------------------------------------------------------------------- #

def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def _sign(payload: str) -> str:
    return hmac.new(SECRET.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).hexdigest()


def _make_token(data: dict) -> str:
    payload = _b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8"))
    return payload + "." + _sign(payload)


def _read_token(token: str) -> dict | None:
    if not token or "." not in token:
        return None
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    try:
        data = json.loads(_b64decode(payload))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or data.get("exp", 0) < time.time():
        return None
    return data


def make_session(email: str) -> str:
    return _make_token({"email": email.strip().lower(), "exp": int(time.time()) + SESSION_TTL})


def session_email(cookie_header: str | None) -> str | None:
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == SESSION_COOKIE:
            data = _read_token(value)
            if data:
                return data.get("email")
    return None


def make_state() -> str:
    return _make_token({"nonce": secrets.token_hex(8), "exp": int(time.time()) + STATE_TTL})


def valid_state(state: str | None) -> bool:
    return _read_token(state or "") is not None


# --------------------------------------------------------------------------- #
# OAuth helpers
# --------------------------------------------------------------------------- #

def base_url(headers) -> str:
    proto = (headers.get("X-Forwarded-Proto") or "http").split(",")[0].strip()
    host = headers.get("Host") or f"127.0.0.1:{os.environ.get('PORT', '8080')}"
    return f"{proto}://{host}"


def redirect_uri(headers) -> str:
    return base_url(headers) + "/auth/callback"


def login_redirect(headers) -> str:
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri(headers),
        "response_type": "code",
        "scope": "openid email profile",
        "state": make_state(),
        "access_type": "online",
        "prompt": "select_account",
    }
    return AUTH_ENDPOINT + "?" + urllib.parse.urlencode(params)


def _post_form(url: str, fields: dict) -> dict:
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/x-www-form-urlencoded", "User-Agent": UA, "Accept": "application/json",
    })
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", "replace"))


def exchange_code(code: str, headers) -> dict:
    return _post_form(TOKEN_ENDPOINT, {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri(headers),
        "grant_type": "authorization_code",
    })


def verify_id_token(id_token: str) -> dict:
    """Validate the id_token server-side via Google's tokeninfo endpoint."""
    url = TOKENINFO_ENDPOINT + "?" + urllib.parse.urlencode({"id_token": id_token})
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        info = json.loads(response.read().decode("utf-8", "replace"))
    if info.get("aud") != GOOGLE_CLIENT_ID:
        raise ValueError("Token audience does not match this application.")
    if str(info.get("email_verified")).lower() not in ("true", "1"):
        raise ValueError("Google account email is not verified.")
    email = (info.get("email") or "").strip().lower()
    if not email:
        raise ValueError("Google did not return an email address.")
    return {"email": email, "name": info.get("name") or email}


def session_cookie_header(email: str) -> str:
    token = make_session(email)
    return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}"


def clear_cookie_header() -> str:
    return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0"