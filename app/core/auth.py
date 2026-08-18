"""Cookie-session auth for the admin dashboard.

Single admin user, credentials from the environment. Deliberately simple — this
protects one operator's dashboard, not a multi-tenant product.

Read this before you change it: the predecessor of this codebase shipped with no
auth at all and was found by a stranger who sent 9,360 unauthorized messages from
the client's number in eleven minutes. Every page and every /api/ route must sit
behind require_auth. The only routes that may be public are /health, /login,
/logout and the provider webhooks (which the carrier calls and cannot log in).
"""

from fastapi import Request, HTTPException
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from app.core.config import settings
import logging
import secrets

logger = logging.getLogger("auth")

# bcrypt must stay pinned to 4.0.x — passlib 1.7.4 breaks against bcrypt >= 4.1.
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

SESSION_COOKIE_NAME = settings.SESSION_COOKIE_NAME
SESSION_MAX_AGE = 86400 * settings.SESSION_DAYS


def _resolve_password_hash() -> str:
    """Prefer a pre-computed hash; fall back to hashing a plaintext dev password."""
    if settings.ADMIN_PASSWORD_HASH:
        return settings.ADMIN_PASSWORD_HASH
    if settings.ADMIN_PASSWORD:
        if settings.ENVIRONMENT == "production":
            logger.warning(
                "ADMIN_PASSWORD is set in plaintext in production. "
                "Generate a hash with scripts/hash_password.py and use ADMIN_PASSWORD_HASH."
            )
        return pwd_context.hash(settings.ADMIN_PASSWORD)
    # No credentials configured: lock the app rather than leaving it open.
    logger.error("No ADMIN_PASSWORD or ADMIN_PASSWORD_HASH configured — login is disabled.")
    return pwd_context.hash(secrets.token_urlsafe(32))


ADMIN_PASSWORD_HASH = _resolve_password_hash()


def get_client_ip(request: Request) -> str:
    """Best-effort client IP. Prefers X-Forwarded-For, which nginx sets."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return "unknown"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.SECRET_KEY)


def create_session_token(username: str) -> str:
    return _serializer().dumps({"user": username})


def verify_session_token(token: str):
    try:
        return _serializer().loads(token, max_age=SESSION_MAX_AGE)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request):
    """Return the logged-in username, or None."""
    token = request.cookies.get(SESSION_COOKIE_NAME)
    if not token:
        return None
    data = verify_session_token(token)
    return data.get("user") if data else None


def require_auth(request: Request) -> str:
    """FastAPI dependency. 401s API calls, redirects page loads to /login."""
    user = get_current_user(request)
    if not user:
        if request.url.path.startswith("/api/"):
            raise HTTPException(status_code=401, detail="Not authenticated")
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


def verify_login(username: str, password: str) -> bool:
    # compare_digest on the username avoids leaking it through timing.
    if not secrets.compare_digest(username, settings.ADMIN_USERNAME):
        return False
    return pwd_context.verify(password, ADMIN_PASSWORD_HASH)
