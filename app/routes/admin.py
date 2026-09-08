from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, require_admin
from app.models import User
from app.routes.users import _user_response
from app.services import storage
from app.services.admin_stats import (
    compute_admin_stats,
    delete_user_file,
    list_orphan_files,
    list_user_files,
)

router = APIRouter()


class DeleteKeyRequest(BaseModel):
    key: str


@router.get("/metadata")
def admin_metadata(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    return compute_admin_stats(db)


@router.post("/orphans/delete")
def admin_delete_orphans(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    orphans = list_orphan_files(db)
    freed_bytes = 0
    for key, size in orphans:
        try:
            storage.delete_file(key)
        except Exception:
            continue
        freed_bytes += size
    return {"count": len(orphans), "freed_bytes": freed_bytes}


@router.get("/users/{user_id}/files")
def admin_user_files(
    user_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    result = list_user_files(db, user_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )
    user, payload = result
    return {"user": _user_response(user), **payload}


@router.delete("/users/{user_id}/files")
def admin_delete_user_file(
    user_id: str,
    body: DeleteKeyRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    try:
        freed_bytes = delete_user_file(db, user_id, body.key)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    return {"freed_bytes": freed_bytes}
