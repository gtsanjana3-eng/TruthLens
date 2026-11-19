"""Password hashing, JWT tokens, authentication dependencies and access control."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import CaseMember, InvestigationCase, Role, User

_bearer = HTTPBearer(auto_error=False)
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15


def hash_password(password: str) -> str:
    # bcrypt only uses the first 72 bytes; reject longer inputs at the schema layer.
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def create_access_token(user: User) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": user.id, "role": user.role.value, "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes), "typ": "access"}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def _decode(token: str) -> dict:
    s = get_settings()
    try:
        data = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm], options={"require": ["exp", "sub"]})
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token", headers={"WWW-Authenticate": "Bearer"})
    if data.get("typ") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    return data


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    # Tokens are accepted only via the Authorization header (never query strings, which leak into logs).
    token = creds.credentials if creds else None
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"})
    data = _decode(token)
    user = db.get(User, data["sub"])
    if not user or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found")
    return user


def require_roles(*roles: Role):
    def dep(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return user
    return dep


def get_case_for_user(db: Session, case_id: str, user: User, *, write: bool = False) -> InvestigationCase:
    """Fetch a case enforcing object-level access control. Returns 404 (not 403) to avoid leaking existence."""
    case = db.get(InvestigationCase, case_id)
    if not case or case.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    if user.role == Role.admin or case.owner_id == user.id:
        return case
    member = db.scalar(select(CaseMember).where(CaseMember.case_id == case_id, CaseMember.user_id == user.id))
    if not member:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Case not found")
    if write and not member.can_edit:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Read-only access to this case")
    return case
