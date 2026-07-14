"""Kullanıcı + parola tabanlı kimlik doğrulama (LAN için).

Tasarım:
  - Kullanıcılar `users` tablosunda saklanır (PBKDF2-HMAC-SHA256, 600k iter).
  - Login → 32-byte random session token üretilir, `sessions` tablosuna yazılır.
  - Frontend Authorization: Bearer <session_token> header'ı ile her isteği auth eder.
  - Token expiry: kısa (8 saat) veya "beni hatırla" (30 gün).
  - Roller: 'admin' (tam yetki, kullanıcı yönetimi) | 'operator' (panel kullanıcısı).

EXE paketleme için ilk açılışta:
  - Hiç kullanıcı yoksa: rastgele 10 karakter parolayla 'admin' kullanıcısı yaratılır.
  - Parola konsola yazdırılır ve `data/initial_admin_password.txt` dosyasına yazılır.
  - Admin ilk login'de parolasını değiştirmek zorundadır (must_change_password=1).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, TYPE_CHECKING

from fastapi import HTTPException, Request, WebSocket, status

if TYPE_CHECKING:
    from app.database import Database

logger = logging.getLogger("gateguard.app")

# ── State ───────────────────────────────────────────────────

_db: Optional["Database"] = None
_INITIAL_PW_FILE: Optional[Path] = None

# Session lifetimes
SESSION_HOURS_DEFAULT = 8
SESSION_HOURS_REMEMBER = 24 * 30

# PBKDF2 parameters
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16

# Auth bypass — public endpoints that don't require a session
PUBLIC_PATHS: set[str] = {
    "/api/login",
    "/api/auth/me",            # checks but doesn't require — middleware lets it through
    "/login",
    "/favicon.ico",
    # HTML shells are public — auth-client.js gates their content
    "/", "/alpr-test", "/camera-test", "/night-watch",
    "/zone-editor", "/admin", "/intrusion-test", "/test-history",
    "/intrusion-history", "/zone-playground", "/cameras", "/vehicles",
    "/kilavuz",
}
PUBLIC_PREFIXES: tuple[str, ...] = ("/static/",)


# ── Initialization ──────────────────────────────────────────

def init_auth(db: "Database", base_dir: Path) -> None:
    """Wire up auth with the shared DB instance and bootstrap default admin."""
    global _db, _INITIAL_PW_FILE
    _db = db
    _INITIAL_PW_FILE = base_dir / "data" / "initial_admin_password.txt"

    # Bootstrap default admin if no users exist
    if _db.get_user_count() == 0:
        password = _generate_initial_password()
        h, salt = hash_password(password)
        _db.add_user(
            username="admin", password_hash=h, salt=salt,
            role="admin", full_name="Sistem Yöneticisi",
            must_change_password=1,
        )
        try:
            _INITIAL_PW_FILE.parent.mkdir(parents=True, exist_ok=True)
            _INITIAL_PW_FILE.write_text(
                f"İlk açılış admin bilgileri (ilk girişten sonra silebilirsiniz):\n\n"
                f"Kullanıcı: admin\n"
                f"Parola:    {password}\n\n"
                f"NOT: Bu parolayla ilk girdiğinizde sistem yeni bir parola belirlemenizi isteyecek.\n"
                f"Oluşturuldu: {datetime.now().isoformat()}\n",
                encoding="utf-8",
            )
        except OSError:
            logger.exception("Failed to write initial password file")
        # Loud log for the operator
        logger.warning("=" * 60)
        logger.warning("İLK AÇILIŞ — VARSAYILAN ADMIN OLUŞTURULDU:")
        logger.warning("  Kullanıcı : admin")
        logger.warning("  Parola    : %s", password)
        logger.warning("  Dosya     : %s", _INITIAL_PW_FILE)
        logger.warning("İlk girişten sonra mutlaka değiştirin!")
        logger.warning("=" * 60)
    else:
        logger.info("Auth: %d aktif kullanıcı yüklü", _db.get_user_count())


def _generate_initial_password() -> str:
    """10-char human-friendly initial password (no confusing chars)."""
    alphabet = "abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(10))


# ── Password hashing ────────────────────────────────────────

def hash_password(password: str, salt: Optional[bytes] = None) -> tuple[str, str]:
    """Return (hash_hex, salt_hex) for storage."""
    if salt is None:
        salt = secrets.token_bytes(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS,
    )
    return dk.hex(), salt.hex()


def verify_password(password: str, stored_hash_hex: str, salt_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    dk, _ = hash_password(password, salt)
    return secrets.compare_digest(dk, stored_hash_hex)


# ── Login / logout ──────────────────────────────────────────

def login(
    username: str, password: str, remember: bool = False,
    user_agent: str = "", ip: str = "",
) -> dict:
    """Validate credentials, create a session, return user+token info.
    Raises HTTPException on failure.
    """
    if not _db:
        raise HTTPException(503, "Auth not initialized")
    user = _db.get_user_by_username(username.strip())
    if not user:
        # Constant-time-ish: still hash a dummy to avoid timing leaks
        hash_password(password)
        raise HTTPException(401, "Kullanıcı adı veya parola hatalı")
    if not verify_password(password, user["password_hash"], user["salt"]):
        raise HTTPException(401, "Kullanıcı adı veya parola hatalı")

    # Issue session
    token = secrets.token_urlsafe(32)
    hours = SESSION_HOURS_REMEMBER if remember else SESSION_HOURS_DEFAULT
    expires_at = (datetime.now() + timedelta(hours=hours)).isoformat()
    _db.create_session(
        token=token, user_id=user["id"], expires_at=expires_at,
        user_agent=user_agent, ip=ip,
    )
    _db.touch_user_login(user["id"])

    # Opportunistic GC of expired sessions (no-op if recent)
    try:
        _db.gc_expired_sessions()
    except Exception:
        pass

    return {
        "token": token,
        "expires_at": expires_at,
        "user": {
            "id": user["id"],
            "username": user["username"],
            "role": user["role"],
            "full_name": user.get("full_name") or "",
            "must_change_password": bool(user.get("must_change_password")),
        },
    }


def logout(token: str) -> bool:
    if not _db:
        return False
    return _db.delete_session(token)


def change_password(user_id: int, current_password: str, new_password: str) -> None:
    """Self-service password change. Raises HTTPException on error."""
    if not _db:
        raise HTTPException(503, "Auth not initialized")
    if len(new_password) < 6:
        raise HTTPException(400, "Parola en az 6 karakter olmalı")
    user = _db.get_user_by_id(user_id)
    if not user:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    if not verify_password(current_password, user["password_hash"], user["salt"]):
        raise HTTPException(401, "Mevcut parola hatalı")
    new_hash, new_salt = hash_password(new_password)
    _db.update_user_password(user_id, new_hash, new_salt)
    logger.info("Password changed for user %s (id=%d)", user["username"], user_id)


def admin_reset_password(target_user_id: int, new_password: str) -> str:
    """Admin sets a new password for any user — also forces must_change on next login."""
    if not _db:
        raise HTTPException(503, "Auth not initialized")
    if len(new_password) < 6:
        raise HTTPException(400, "Parola en az 6 karakter olmalı")
    user = _db.get_user_by_id(target_user_id)
    if not user:
        raise HTTPException(404, "Kullanıcı bulunamadı")
    new_hash, new_salt = hash_password(new_password)
    _db.update_user_password(target_user_id, new_hash, new_salt)
    # Force change on next login
    with _db._lock:                                       # type: ignore[attr-defined]
        _db.conn.execute(
            "UPDATE users SET must_change_password=1 WHERE id=?", (target_user_id,),
        )
        _db.conn.commit()
    # Invalidate existing sessions
    _db.delete_user_sessions(target_user_id)
    return user["username"]


# ── Request introspection ───────────────────────────────────

def _is_public_path(path: str) -> bool:
    if path in PUBLIC_PATHS:
        return True
    return any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES)


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or request.headers.get("Authorization")
    if auth:
        parts = auth.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            return parts[1].strip()
        return auth.strip()
    x_auth = request.headers.get("x-auth-token") or request.headers.get("X-Auth-Token")
    if x_auth:
        return x_auth.strip()
    q = request.query_params.get("token")
    if q:
        return q.strip()
    return None


def get_request_user(request: Request) -> Optional[dict]:
    """Resolve current user from the request's session token. None if not authed."""
    if not _db:
        return None
    token = _extract_token(request)
    if not token:
        return None
    return _db.get_session_user(token)


async def auth_middleware(request: Request, call_next):
    """HTTP middleware — enforce session token on protected paths."""
    path = request.url.path
    if _is_public_path(path) or request.method == "OPTIONS":
        return await call_next(request)
    if not _db:
        return _json_unauthorized("Auth not initialized")
    token = _extract_token(request)
    if not token:
        return _json_unauthorized("Token eksik")
    user = _db.get_session_user(token)
    if not user:
        return _json_unauthorized("Oturum geçersiz veya süresi dolmuş")
    # Stash user on request.state so route handlers can read role-checks
    request.state.user = user
    return await call_next(request)


def require_admin(request: Request) -> dict:
    """FastAPI dependency-style helper — raise 403 unless user is admin."""
    user = getattr(request.state, "user", None) or get_request_user(request)
    if not user:
        raise HTTPException(401, "Oturum gerekli")
    if user.get("role") != "admin":
        raise HTTPException(403, "Bu işlem için admin yetkisi gerekli")
    return user


def _json_unauthorized(reason: str):
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"error": reason, "code": "unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


# ── WebSocket auth ──────────────────────────────────────────

def check_websocket_token(websocket: WebSocket) -> Optional[dict]:
    """Returns user dict if WS token is valid, None otherwise."""
    if not _db:
        return None
    token = websocket.query_params.get("token", "")
    if not token:
        return None
    return _db.get_session_user(token)
