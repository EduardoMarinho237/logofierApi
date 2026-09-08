from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.deps import get_db, require_admin
from app.models import User
from app.schemas import UserCreateRequest, UserResponse, UserUpdateRequest
from app.services import storage

router = APIRouter()


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        name=user.name,
        role=user.role,
        is_active=user.is_active,
        avatar_url=(f"/api/users/{user.id}/avatar" if user.avatar_thumb_key else None),
        created_at=user.created_at.isoformat() if user.created_at else None,
    )


@router.get("", response_model=list[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    users = (
        db.query(User)
        .filter(User.deleted_at.is_(None))
        .order_by(User.created_at.desc())
        .all()
    )
    return [_user_response(u) for u in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    existing = (
        db.query(User)
        .filter(User.email == body.email, User.deleted_at.is_(None))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already registered",
        )
    # Admins can only be created directly in the database, never via the API.
    user = User(
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.name,
        role="user",
        is_active=True,
        is_password_set=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_response(user)


@router.put("/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    body: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    if body.is_active is not None and body.is_active != user.is_active and admin.id == user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot restrict your own account",
        )

    if body.name is not None:
        user.name = body.name.strip()
    if body.email is not None:
        new_email = body.email.strip().lower()
        if new_email != user.email:
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
            user.email = new_email
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.password is not None:
        user.hashed_password = hash_password(body.password)
        user.is_password_set = True

    user.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(user)
    return _user_response(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    if admin.id == user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete your own account",
        )
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    user.is_active = False
    user.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return None


@router.get("/{user_id}/avatar")
def get_user_avatar(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user or not user.avatar_thumb_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )
    try:
        data = storage.download_file(user.avatar_thumb_key)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Avatar not found",
        )
    return Response(content=data, media_type="image/jpeg")