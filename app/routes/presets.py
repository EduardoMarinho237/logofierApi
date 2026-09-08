from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models import Preset, User
from app.schemas import PresetCreateRequest, PresetResponse

router = APIRouter()


def _preset_response(preset: Preset) -> PresetResponse:
    return PresetResponse(
        id=preset.id,
        name=preset.name,
        mode=preset.mode,
        pos_strategy=preset.pos_strategy,
        page_selection=preset.page_selection or {},
        position=preset.position or {},
        position_rest=preset.position_rest,
        position_mode=preset.position_mode,
        created_at=preset.created_at.isoformat() if preset.created_at else None,
    )


def _get_owned_preset(preset_id: str, user: User, db: Session) -> Preset:
    preset = (
        db.query(Preset)
        .filter(
            Preset.id == preset_id,
            Preset.user_id == user.id,
            Preset.deleted_at.is_(None),
        )
        .first()
    )
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preset not found",
        )
    return preset


@router.get("/{preset_id}", response_model=PresetResponse)
def get_preset(
    preset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preset = _get_owned_preset(preset_id, current_user, db)
    return _preset_response(preset)


@router.get("", response_model=list[PresetResponse])
def list_presets(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    presets = (
        db.query(Preset)
        .filter(Preset.user_id == current_user.id, Preset.deleted_at.is_(None))
        .order_by(Preset.created_at.desc())
        .all()
    )
    return [_preset_response(p) for p in presets]


@router.post("", response_model=PresetResponse, status_code=status.HTTP_201_CREATED)
def create_preset(
    body: PresetCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preset = Preset(
        user_id=current_user.id,
        name=body.name.strip(),
        mode=body.mode,
        pos_strategy=body.pos_strategy,
        page_selection=body.page_selection.model_dump(),
        position=body.position.model_dump(),
        position_rest=body.position_rest.model_dump() if body.position_rest else None,
        position_mode=body.position_mode,
    )
    db.add(preset)
    db.commit()
    db.refresh(preset)
    return _preset_response(preset)


@router.delete("/{preset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_preset(
    preset_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    preset = (
        db.query(Preset)
        .filter(
            Preset.id == preset_id,
            Preset.user_id == current_user.id,
            Preset.deleted_at.is_(None),
        )
        .first()
    )
    if not preset:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Preset not found",
        )
    preset.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return None
