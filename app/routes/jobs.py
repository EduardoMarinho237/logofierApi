from __future__ import annotations

import base64
import io
import json
import os
import uuid
from datetime import datetime, timezone

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from fastapi.responses import Response, StreamingResponse
from sqlalchemy.orm import Session

from app.config import settings
from app.deps import get_current_user, get_db
from app.models import Job, JobStatus, User, UserLogo
from app.schemas import (
    JobConfig,
    JobResponse,
    JobStatusResponse,
    LogoPosition,
    ProcessingListResponse,
)
from app.services import storage
from app.services.cleanup import cleanup_expired_job_files
from app.services.job_runner import run_pdf_job
from app.utils.image_utils import convert_to_png, create_logo_thumbnail, get_image_dimensions
from app.utils.sanitize import sanitize_filename

router = APIRouter()

ALLOWED_LOGO_EXTS = {"png", "jpg", "jpeg", "webp", "svg", "gif", "bmp", "tiff"}


def _get_owned_job(job_id: str, user: User, db: Session) -> Job:
    query = db.query(Job).filter(Job.id == job_id, Job.deleted_at.is_(None))
    if user.role != "admin":
        query = query.filter(Job.user_id == user.id)
    job = query.first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def _format_label(job: Job) -> str:
    if job.title:
        return job.title
    source_names = job.source_names or []
    if source_names:
        return os.path.splitext(source_names[0])[0]
    return job.created_at.strftime("%d/%m/%Y %H:%M") if job.created_at else "Sem título"


@router.get("", response_model=ProcessingListResponse)
def list_jobs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cleanup_expired_job_files(user.id, db)
    query = db.query(Job).filter(Job.deleted_at.is_(None))
    if user.role != "admin":
        query = query.filter(Job.user_id == user.id)
    total = query.count()
    jobs = query.order_by(Job.created_at.desc()).offset(skip).limit(limit).all()
    items = [
        {
            "id": job.id,
            "title": job.title,
            "display_label": _format_label(job),
            "status": job.status.value,
            "mode": job.mode,
            "total_files": job.total_files,
            "processed_files": job.processed_files,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "is_expired": job.output_zip_key is None,
        }
        for job in jobs
    ]
    return {"items": items, "total": total}


@router.post("", response_model=JobResponse, status_code=status.HTTP_201_CREATED)
async def create_job(
    config: str = File(...),
    logo: list[UploadFile] = File(default=[]),
    logo_ids: list[str] = Form(default=[]),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        job_config = JobConfig.model_validate(json.loads(config))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid config JSON: {exc}",
        )

    if len(logo) > settings.MAX_UPLOADED_LOGOS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many logos uploaded in this request",
        )
    if len(logo_ids) > settings.MAX_LOGO_IDS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many selected logos",
        )

    job_id = str(uuid.uuid4())
    logo_keys: list[str] = []
    logo_names: list[str] = []

    # Load saved logos selected by the user.
    saved_logos: list[UserLogo] = []
    if logo_ids:
        saved_logos = (
            db.query(UserLogo)
            .filter(
                UserLogo.id.in_(logo_ids),
                UserLogo.user_id == user.id,
                UserLogo.deleted_at.is_(None),
            )
            .all()
        )
        if len(saved_logos) != len(logo_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="One or more selected logos not found",
            )

    # Start indexing after uploaded files so each logo gets a unique job index.
    saved_offset = len(logo)

    # Process newly uploaded logos: save to user account and copy to job path.
    for i, lf in enumerate(logo):
        logo_ext = lf.filename.rsplit(".", 1)[-1].lower() if "." in lf.filename else ""
        if logo_ext not in ALLOWED_LOGO_EXTS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported logo format: {logo_ext}",
            )
        logo_bytes = await lf.read()
        if len(logo_bytes) > settings.MAX_LOGO_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Logo must be under 5MB",
            )
        png_bytes = convert_to_png(logo_bytes, lf.filename or f"logo_{i}.png")

        # Save to user's logo library.
        file_id = uuid.uuid4().hex
        user_storage_key = f"user-logos/{user.id}/{file_id}.png"
        user_thumb_key = f"user-logos/{user.id}/{file_id}_thumb.png"
        width, height = get_image_dimensions(png_bytes)
        thumb_bytes = create_logo_thumbnail(png_bytes)
        storage.upload_file(png_bytes, user_storage_key, content_type="image/png")
        storage.upload_file(thumb_bytes, user_thumb_key, content_type="image/png")

        name_base = sanitize_filename(
            lf.filename.rsplit(".", 1)[0] if "." in lf.filename else lf.filename,
            default=f"Logo {i + 1}",
            keep_unicode=True,
        )
        user_logo = UserLogo(
            user_id=user.id,
            name=name_base or "Logo",
            storage_key=user_storage_key,
            thumb_key=user_thumb_key,
            aspect_ratio=width / height if height else 1.0,
        )
        db.add(user_logo)

        # Copy to job-specific path for processing.
        job_key = f"logos/{job_id}/logo_{i}.png"
        storage.upload_file(png_bytes, job_key, content_type="image/png")
        logo_keys.append(job_key)
        logo_names.append(sanitize_filename(lf.filename or f"logo_{i}.png", keep_unicode=True))

    # Copy saved logos to job path.
    for idx, user_logo in enumerate(saved_logos):
        i = saved_offset + idx
        try:
            png_bytes = storage.download_file(user_logo.storage_key)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Could not load saved logo: {user_logo.name}",
            )
        job_key = f"logos/{job_id}/logo_{i}.png"
        storage.upload_file(png_bytes, job_key, content_type="image/png")
        logo_keys.append(job_key)
        logo_names.append(user_logo.name)

    if len(logo_keys) > settings.MAX_LOGOS_PER_JOB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many logos for one job",
        )
    if not logo_keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one logo is required",
        )

    if job_config.mode == "multiple_logos" and len(logo_keys) < 2:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple logos mode requires at least two logos",
        )

    job = Job(
        id=job_id,
        user_id=user.id,
        mode=job_config.mode,
        config=job_config.model_dump(),
        status=JobStatus.uploading,
        logo_key=logo_keys[0],
        logo_keys=logo_keys,
        logo_names=logo_names,
        source_keys=[],
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    return JobResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        config=job.config,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.post("/{job_id}/files", status_code=status.HTTP_204_NO_CONTENT)
async def upload_files(
    job_id: str,
    files: list[UploadFile] = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)

    if len(files) > settings.MAX_PDFS_PER_REQUEST:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many PDFs in this request",
        )

    current_keys = list(job.source_keys or [])
    current_names = list(job.source_names or [])
    for file in files:
        if not file.filename.lower().endswith(".pdf"):
            continue
        data = await file.read()
        if len(data) > settings.MAX_PDF_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="PDF must be under 50MB",
            )
        safe_name = sanitize_filename(file.filename, default=f"arquivo_{len(current_keys) + 1}.pdf")
        key = f"jobs/{job.id}/sources/{uuid.uuid4().hex}_{safe_name}"
        storage.upload_file(data, key, content_type="application/pdf")
        current_keys.append(key)
        current_names.append(safe_name)

    if len(current_keys) > settings.MAX_PDFS_PER_JOB:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Too many PDFs for one job",
        )

    if current_keys:
        job.source_keys = current_keys
        job.source_names = current_names
        job.total_files = len(current_keys)
        if not job.title:
            job.title = os.path.splitext(current_names[0])[0]
        db.commit()

    return None


@router.post("/{job_id}/process", status_code=status.HTTP_202_ACCEPTED)
async def process_job(
    job_id: str,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)

    keys = list(job.source_keys or [])
    if not keys:
        job.status = JobStatus.failed
        job.error_message = "No PDF files uploaded"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No PDF files uploaded for this job",
        )

    logo_keys = list(job.logo_keys or [])
    mode = job.mode or "multiple_pdfs"

    if mode == "multiple_logos" and len(keys) > 1:
        job.status = JobStatus.failed
        job.error_message = "Multiple logos mode accepts a single PDF file"
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Multiple logos mode accepts a single PDF file",
        )

    background_tasks.add_task(run_pdf_job, job.id, keys, logo_keys, mode)
    return {"status": "accepted"}


@router.get("/{job_id}/status", response_model=JobStatusResponse)
def get_status(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    return JobStatusResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        error_message=job.error_message,
    )


@router.get("/{job_id}", response_model=JobResponse)
def get_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    return JobResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        config=job.config,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.get("/{job_id}/logo")
def get_logo(
    job_id: str,
    index: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    try:
        logo_keys = list(job.logo_keys or [])
        if 0 <= index < len(logo_keys):
            key = logo_keys[index]
        elif logo_keys:
            key = logo_keys[0]
        else:
            key = job.logo_key
        data = storage.download_file(key)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Logo not found",
        )
    return Response(content=data, media_type="image/png")


@router.get("/{job_id}/logos")
def get_logos(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    keys = list(job.logo_keys or [])
    return {"keys": keys, "count": len(keys)}


@router.put("/{job_id}/logos/{index}/position", response_model=JobResponse)
async def update_logo_position(
    job_id: str,
    index: int,
    body: LogoPosition,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    logo_keys = list(job.logo_keys or [])
    if not (0 <= index < len(logo_keys)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid logo index",
        )

    config = JobConfig.model_validate(job.config)
    config.logo_positions = {**config.logo_positions, str(index): body}
    job.config = config.model_dump()
    db.commit()
    db.refresh(job)
    return JobResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        config=job.config,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.get("/{job_id}/preview-page")
def preview_page(
    job_id: str,
    page: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    keys = list(job.source_keys or [])
    if not keys:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No PDF files uploaded",
        )
    try:
        pdf_bytes = storage.download_file(keys[0])
        from app.services.pdf_processor import render_page_preview

        png_bytes, width, height = render_page_preview(pdf_bytes, page)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not render page",
        )
    headers = {
        "X-Page-Width-Points": str(width),
        "X-Page-Height-Points": str(height),
    }
    return Response(content=png_bytes, media_type="image/png", headers=headers)


@router.get("/{job_id}/review")
def review_manifest(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List the output PDFs of a job so the review screen can render a card per file."""
    job = _get_owned_job(job_id, user, db)
    mode = job.mode or "multiple_pdfs"
    source_names = list(job.source_names or [])
    logo_names = list(job.logo_names or [])
    files: list[dict] = []
    if mode == "multiple_logos":
        for i, name in enumerate(logo_names or []):
            base = os.path.splitext(name)[0] if name else f"Logo {i + 1}"
            files.append({"index": i, "name": base})
    else:
        for i, name in enumerate(source_names or []):
            base = os.path.splitext(name)[0] if name else f"Arquivo {i + 1}"
            files.append({"index": i, "name": base})
    return {"mode": mode, "files": files}


@router.get("/{job_id}/review-preview")
def review_preview(
    job_id: str,
    file: int = 0,
    pages: str = "0",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Render review thumbnails of the final output for one PDF of a job.

    `file` selects which output PDF to preview:
      - multiple_pdfs: index into the source PDFs.
      - multiple_logos: index into the logos (each produces its own stamped copy).
    `pages` is a comma-separated list of 0-based page numbers to render.
    The PDF is opened once and the logo encoded once per request for speed.
    """
    job = _get_owned_job(job_id, user, db)
    try:
        page_list = [int(p) for p in pages.split(",") if p.strip() != ""]
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid pages list",
        )
    page_list = [p for p in page_list if p >= 0]
    if len(page_list) > settings.MAX_REVIEW_PAGES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many pages to review (max {settings.MAX_REVIEW_PAGES})",
        )

    config = JobConfig.model_validate(job.config)
    mode = job.mode or "multiple_pdfs"
    source_keys = list(job.source_keys or [])
    logo_keys = list(job.logo_keys or [])

    if mode == "multiple_logos":
        if not (0 <= file < len(logo_keys)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file index",
            )
        pdf_key = source_keys[0] if source_keys else None
        logo_key = logo_keys[file]
        preserve_aspect = True
        per_logo = config.logo_positions.get(str(file))
        eff_config = config
        if per_logo:
            eff_config = config.model_copy(
                update={
                    "position": per_logo.position,
                    "position_rest": per_logo.position_rest,
                }
            )
    else:
        if not (0 <= file < len(source_keys)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid file index",
            )
        pdf_key = source_keys[file]
        logo_key = logo_keys[0] if logo_keys else None
        preserve_aspect = False
        eff_config = config

    if not pdf_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No PDF files uploaded",
        )

    try:
        pdf_bytes = storage.download_file(pdf_key)
        logo_bytes = storage.download_file(logo_key) if logo_key else None
        from app.services.pdf_processor import render_stamped_pages_preview

        results, width, height, page_count = render_stamped_pages_preview(
            pdf_bytes,
            logo_bytes,
            eff_config,
            page_list,
            preserve_aspect=preserve_aspect,
            dpi=72,
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not render review preview",
        )

    pages_payload = [
        {
            "page": p,
            "stamped": stamped,
            "image_base64": base64.b64encode(b).decode("ascii"),
        }
        for (p, b, stamped) in results
    ]
    return {
        "width": width,
        "height": height,
        "page_count": page_count,
        "pages": pages_payload,
    }


@router.put("/{job_id}/config", response_model=JobResponse)
async def update_config(
    job_id: str,
    body: JobConfig,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    job.config = body.model_dump()
    db.commit()
    db.refresh(job)
    return JobResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        config=job.config,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.get("/{job_id}/download")
def download_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    if job.status != JobStatus.done or not job.output_zip_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job not finished yet",
        )
    zip_bytes = storage.download_file(job.output_zip_key)
    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="logofier_{job.id}.zip"'},
    )


@router.put("/{job_id}/title", response_model=JobResponse)
def update_title(
    job_id: str,
    body: dict,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    title = body.get("title")
    if title is not None:
        title = title.strip()
        if len(title) > 255:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Title too long",
            )
        job.title = title if title else None
        job.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(job)
    return JobResponse(
        id=job.id,
        status=job.status.value,
        total_files=job.total_files,
        processed_files=job.processed_files,
        config=job.config,
        error_message=job.error_message,
        created_at=job.created_at.isoformat() if job.created_at else None,
    )


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _get_owned_job(job_id, user, db)
    if job.output_zip_key:
        try:
            storage.delete_file(job.output_zip_key)
        except Exception:
            pass
        job.output_zip_key = None
    job.deleted_at = datetime.now(timezone.utc)
    db.commit()
    return None
