"""Dashboard login: username + password (scrypt hash) + 6-digit authenticator code (TOTP).

Defence in depth: the app binds to 127.0.0.1 and is reached only through Cloudflare Tunnel +
Cloudflare Access; this login is the second lock. Failed logins are rate-limited per IP.
"""

import base64
import hashlib
import hmac
import secrets
import time
from collections import defaultdict

import pyotp

from instapost.config import get_settings

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60
_failures: dict[str, list[float]] = defaultdict(list)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**15, r=8, p=1, maxmem=64 * 2**20)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def check_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), n=2**15, r=8,
                            p=1, maxmem=64 * 2**20)
    return hmac.compare_digest(digest, base64.b64decode(digest_b64))


def locked_out(ip: str) -> bool:
    now = time.time()
    _failures[ip] = [t for t in _failures[ip] if now - t < LOCKOUT_SECONDS]
    return len(_failures[ip]) >= MAX_FAILURES


def verify_login(ip: str, username: str, password: str, code: str) -> bool:
    s = get_settings()
    if locked_out(ip):
        return False
    ok = (
        bool(s.admin_password_hash and s.admin_totp_secret)
        and hmac.compare_digest(username, s.admin_username)
        and check_password(password, s.admin_password_hash)
        and pyotp.TOTP(s.admin_totp_secret).verify(code.strip(), valid_window=1)
    )
    if not ok:
        _failures[ip].append(time.time())
    return ok


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
