from datetime import datetime, timedelta, timezone

import uuid as uuid_module
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session
from starlette.requests import Request

from app.auth import (
    create_access_token,
    ensure_utc,
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    verify_password,
)
from app.config import settings
from app.deps import get_current_user, get_db, require_admin
from app.limiter import limiter
from app.models import RefreshToken, User
from app.schemas import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    UpdateMeRequest,
    UserResponse,
)
from app.services import storage
from app.services.cleanup import cleanup_expired_job_files
from app.utils.image_utils import create_avatar_thumbnail

router = APIRouter()

ALLOWED_AVATAR_EXTS = {"png", "jpg", "jpeg", "webp", "gif"}
AVATAR_SIZE = 200


def _issue_tokens(user: User, db: Session, replace_rt_id: str | None = None) -> TokenResponse:
    access = create_access_token(user.id, user.token_version)
    raw_rt, rt_hash, rt_id = generate_refresh_token()
    expires_at = datetime.now(timezone.utc) + timedelta(
        days=settings.REFRESH_TOKEN_EXPIRATION_DAYS
    )
    if replace_rt_id:
        old = db.query(RefreshToken).filter(RefreshToken.id == replace_rt_id).first()
        if old is not None:
            old.revoked_at = datetime.now(timezone.utc)
            old.replaced_by = rt_id
    db.add(
        RefreshToken(
            id=rt_id,
            user_id=user.id,
            token_hash=rt_hash,
            expires_at=expires_at,
        )
    )
    db.commit()
    return TokenResponse(access_token=access, refresh_token=raw_rt)


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        avatar_url=("/api/auth/me/avatar" if user.avatar_thumb_key else None),
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_LOGIN)
def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    user = (
        db.query(User)
        .filter(User.email == body.email, User.deleted_at.is_(None))
        .first()
    )
    if user is not None:
        now = datetime.now(timezone.utc)
        if user.locked_until and ensure_utc(user.locked_until) > now:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Account is temporarily locked. Try again later.",
            )
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is inactive",
        )

    if not verify_password(body.password, user.hashed_password):
        now = datetime.now(timezone.utc)
        user.failed_login_attempts += 1
        if user.failed_login_attempts >= settings.LOGIN_MAX_FAILED_ATTEMPTS:
            user.locked_until = now + timedelta(minutes=settings.LOGIN_LOCKOUT_MINUTES)
        user.updated_at = now
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    user.failed_login_attempts = 0
    user.locked_until = None
    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    cleanup_expired_job_files(user.id, db)
    return _issue_tokens(user, db)


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(body: RefreshRequest, db: Session = Depends(get_db)):
    token_hash = hash_refresh_token(body.refresh_token)
    rt = (
        db.query(RefreshToken)
        .filter(RefreshToken.token_hash == token_hash)
        .first()
    )
    if rt is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        )

    now = datetime.now(timezone.utc)
    if rt.revoked_at is not None or ensure_utc(rt.expires_at) <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked or expired",
        )

    user = (
        db.query(User)
        .filter(User.id == rt.user_id, User.deleted_at.is_(None))
        .first()
    )
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or inactive",
        )

    # Rotation: the presented token is consumed; a new pair is issued.
    return _issue_tokens(user, db, replace_rt_id=rt.id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(body: RefreshRequest | None = None, db: Session = Depends(get_db)):
    if body is not None:
        token_hash = hash_refresh_token(body.refresh_token)
        rt = (
            db.query(RefreshToken)
            .filter(RefreshToken.token_hash == token_hash)
            .first()
        )
        if rt is not None:
            user = (
                db.query(User)
                .filter(User.id == rt.user_id)
                .first()
            )
            if user is not None:
                now = datetime.now(timezone.utc)
                db.query(RefreshToken).filter(
                    RefreshToken.user_id == user.id
                ).update(
                    {
                        RefreshToken.revoked_at: now,
                    },
                    synchronize_session=False,
                )
                # Bump token_version → invalidates all issued access tokens.
                user.token_version += 1
                user.updated_at = now
                db.commit()
    return None


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return _user_response(current_user)


@router.put("/me", response_model=UserResponse)
def update_me(
    body: UpdateMeRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.name is not None:
        current_user.name = body.name.strip()
    if body.email is not None:
        new_email = body.email.strip().lower()
        if new_email != current_user.email:
            existing = (
                db.query(User)
                .filter(User.email == new_email, User.deleted_at.is_(None))
                .first()
            )
            if existing is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email already registered",
                )
            current_user.email = new_email
    current_user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current_user)
    return _user_response(current_user)


@router.post("/me/password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.new_password != body.new_password_confirm:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Passwords do not match",
        )
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect",
        )
    current_user.hashed_password = hash_password(body.new_password)
    current_user.updated_at = datetime.now(timezone.utc)
    db.commit()
    return None


@router.post("/me/avatar", response_model=UserResponse)
async def upload_avatar(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_AVATAR_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported image format",
        )

    data = await file.read()
    if len(data) > settings.MAX_AVATAR_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Avatar must be under 5MB",
        )

    try:
        thumb_bytes = create_avatar_thumbnail(data, size=AVATAR_SIZE)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image: {exc}",
        )

    # Remove old avatars
    if current_user.avatar_key:
        storage.delete_file(current_user.avatar_key)
    if current_user.avatar_thumb_key:
        storage.delete_file(current_user.avatar_thumb_key)

    file_id = uuid_module.uuid4().hex
    avatar_key = f"avatars/{current_user.id}/{file_id}.jpg"
    thumb_key = f"avatars/{current_user.id}/{file_id}_thumb.jpg"

    storage.upload_file(thumb_bytes, thumb_key, content_type="image/jpeg")
    storage.upload_file(data, avatar_key, content_type="image/jpeg")

    current_user.avatar_key = avatar_key
    current_user.avatar_thumb_key = thumb_key
    current_user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current_user)
    return _user_response(current_user)


@router.get("/me/avatar")
def get_avatar(current_user: User = Depends(get_current_user)):
    if not current_user.avatar_thumb_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )
    try:
        data = storage.download_file(current_user.avatar_thumb_key)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )
    return Response(content=data, media_type="image/jpeg")


@router.delete("/me/avatar", response_model=UserResponse)
def delete_avatar(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if current_user.avatar_key:
        storage.delete_file(current_user.avatar_key)
    if current_user.avatar_thumb_key:
        storage.delete_file(current_user.avatar_thumb_key)
    current_user.avatar_key = None
    current_user.avatar_thumb_key = None
    current_user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(current_user)
    return _user_response(current_user)
