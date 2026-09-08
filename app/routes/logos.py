from datetime import datetime, timezone

import uuid as uuid_module
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_current_user, get_db
from app.models import User, UserLogo
from app.schemas import LogoRenameRequest, LogoResponse
from app.services import storage
from app.utils.image_utils import convert_to_png, create_logo_thumbnail, get_image_dimensions
from app.utils.sanitize import sanitize_filename

router = APIRouter()

ALLOWED_LOGO_EXTS = {"png", "jpg", "jpeg", "webp", "svg", "gif", "bmp", "tiff"}


def _logo_response(logo: UserLogo) -> LogoResponse:
    return LogoResponse(
        id=logo.id,
        name=logo.name,
        aspect_ratio=logo.aspect_ratio,
        created_at=logo.created_at.isoformat() if logo.created_at else None,
    )


@router.get("", response_model=list[LogoResponse])
def list_logos(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logos = (
        db.query(UserLogo)
        .filter(UserLogo.user_id == current_user.id, UserLogo.deleted_at.is_(None))
        .order_by(UserLogo.created_at.desc())
        .all()
    )
    return [_logo_response(l) for l in logos]


@router.post("", response_model=LogoResponse, status_code=status.HTTP_201_CREATED)
async def create_logo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ext = file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in ALLOWED_LOGO_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Unsupported logo format",
        )

    data = await file.read()
    if len(data) > settings.MAX_LOGO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Logo must be under 5MB",
        )

    try:
        png_bytes = convert_to_png(data, file.filename or "logo.png")
        width, height = get_image_dimensions(png_bytes)
        thumb_bytes = create_logo_thumbnail(png_bytes)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid image: {exc}",
        )

    file_id = uuid_module.uuid4().hex
    storage_key = f"user-logos/{current_user.id}/{file_id}.png"
    thumb_key = f"user-logos/{current_user.id}/{file_id}_thumb.png"

    storage.upload_file(png_bytes, storage_key, content_type="image/png")
    storage.upload_file(thumb_bytes, thumb_key, content_type="image/png")

    name_base = sanitize_filename(
        file.filename.rsplit(".", 1)[0] if "." in file.filename else file.filename,
        default="Logo",
        keep_unicode=True,
    )
    logo = UserLogo(
        user_id=current_user.id,
        name=name_base or "Logo",
        storage_key=storage_key,
        thumb_key=thumb_key,
        aspect_ratio=width / height if height else 1.0,
    )
    db.add(logo)
    db.commit()
    db.refresh(logo)
    return _logo_response(logo)


@router.put("/{logo_id}", response_model=LogoResponse)
def rename_logo(
    logo_id: str,
    body: LogoRenameRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logo = (
        db.query(UserLogo)
        .filter(
            UserLogo.id == logo_id,
            UserLogo.user_id == current_user.id,
            UserLogo.deleted_at.is_(None),
        )
        .first()
    )
    if not logo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )
    logo.name = body.name.strip()
    logo.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(logo)
    return _logo_response(logo)


@router.delete("/{logo_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_logo(
    logo_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logo = (
        db.query(UserLogo)
        .filter(
            UserLogo.id == logo_id,
            UserLogo.user_id == current_user.id,
            UserLogo.deleted_at.is_(None),
        )
        .first()
    )
    if not logo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )
    try:
        storage.delete_file(logo.storage_key)
    except Exception:
        pass
    try:
        storage.delete_file(logo.thumb_key)
    except Exception:
        pass
    logo.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return None


@router.get("/{logo_id}/image")
def get_logo_image(
    logo_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logo = (
        db.query(UserLogo)
        .filter(
            UserLogo.id == logo_id,
            UserLogo.user_id == current_user.id,
            UserLogo.deleted_at.is_(None),
        )
        .first()
    )
    if not logo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )
    try:
        data = storage.download_file(logo.storage_key)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo file not found",
        )
    return Response(content=data, media_type="image/png")


@router.get("/{logo_id}/thumbnail")
def get_logo_thumbnail(
    logo_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    logo = (
        db.query(UserLogo)
        .filter(
            UserLogo.id == logo_id,
            UserLogo.user_id == current_user.id,
            UserLogo.deleted_at.is_(None),
        )
        .first()
    )
    if not logo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )
    try:
        data = storage.download_file(logo.thumb_key)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo thumbnail not found",
        )
    return Response(content=data, media_type="image/png")
