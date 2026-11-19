"""Registration, login (with lockout), profile and user administration."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..database import get_db
from ..models import Role, User, utcnow
from ..ratelimit import client_ip, limiter
from ..schemas import LoginIn, PasswordChange, ProfileUpdate, RegisterIn, RoleUpdate, TokenOut, UserOut
from ..security import LOCKOUT_MINUTES, MAX_FAILED_LOGINS, create_access_token, get_current_user, hash_password, require_roles, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])
_DUMMY_HASH = hash_password("Dummy-password-for-timing-1")


def _token(user: User) -> TokenOut:
    return TokenOut(access_token=create_access_token(user), expires_in=get_settings().access_token_minutes * 60, user=UserOut.model_validate(user))


@router.post("/register", response_model=TokenOut, status_code=201, dependencies=[Depends(limiter("auth"))])
def register(body: RegisterIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    email = body.email.lower()
    if db.scalar(select(User).where(func.lower(User.email) == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    # The very first account bootstraps the system as administrator.
    first = db.scalar(select(func.count(User.id))) == 0
    user = User(email=email, full_name=body.full_name, password_hash=hash_password(body.password), role=Role.admin if first else Role.investigator)
    db.add(user)
    db.flush()
    audit.record(db, "auth.register", user_id=user.id, detail={"role": user.role.value}, ip=client_ip(request))
    return _token(user)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limiter("auth"))])
def login(body: LoginIn, request: Request, db: Session = Depends(get_db)) -> TokenOut:
    user = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    generic = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    if not user:
        verify_password(body.password, _DUMMY_HASH)  # constant-ish timing, no user enumeration
        audit.record(db, "auth.login_failed", detail={"reason": "unknown_user"}, ip=client_ip(request))
        raise generic
    now = utcnow()
    locked = user.locked_until
    if locked and locked.tzinfo is None:
        locked = locked.replace(tzinfo=now.tzinfo)
    if locked and locked > now:
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked after repeated failed logins. Try again later.")
    if not verify_password(body.password, user.password_hash) or not user.is_active:
        user.failed_logins += 1
        if user.failed_logins >= MAX_FAILED_LOGINS:
            user.locked_until, user.failed_logins = now + timedelta(minutes=LOCKOUT_MINUTES), 0
        audit.record(db, "auth.login_failed", user_id=user.id, ip=client_ip(request))
        raise generic
    user.failed_logins, user.locked_until = 0, None
    audit.record(db, "auth.login", user_id=user.id, ip=client_ip(request))
    return _token(user)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> User:
    return user


@router.patch("/me", response_model=UserOut)
def update_me(body: ProfileUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> User:
    if body.full_name is not None:
        user.full_name = body.full_name.strip()
    if body.preferences is not None:
        allowed = {"reduced_motion", "high_contrast", "default_case_view", "page_size", "email_notifications"}
        user.preferences = {k: v for k, v in body.preferences.items() if k in allowed}
    audit.record(db, "user.profile_updated", user_id=user.id)
    return user


@router.post("/me/password", status_code=204)
def change_password(body: PasswordChange, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> None:
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    audit.record(db, "user.password_changed", user_id=user.id, ip=client_ip(request))


@router.get("/users", response_model=list[UserOut])
def list_users(_: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)) -> list[User]:
    return list(db.scalars(select(User).order_by(User.created_at)))


@router.patch("/users/{user_id}/role", response_model=UserOut)
def set_role(user_id: str, body: RoleUpdate, admin: User = Depends(require_roles(Role.admin)), db: Session = Depends(get_db)) -> User:
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(404, "User not found")
    if target.id == admin.id and body.role != Role.admin:
        raise HTTPException(400, "Administrators cannot demote themselves")
    target.role = body.role
    audit.record(db, "user.role_changed", user_id=admin.id, detail={"target": user_id, "role": body.role.value})
    return target
