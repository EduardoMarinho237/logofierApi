from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models import Job
from app.services import storage


RESULT_RETENTION_DAYS = 30


def cleanup_expired_job_files(user_id: str, db: Session) -> int:
    """Automatically discard a user's jobs older than RESULT_RETENTION_DAYS.

    Frees the result ZIP and any remaining per-job PDF/PNG artifacts, then
    soft-deletes the row so the processing disappears from the user's list.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=RESULT_RETENTION_DAYS)
    expired = (
        db.query(Job)
        .filter(
            Job.user_id == user_id,
            Job.deleted_at.is_(None),
            Job.created_at < cutoff,
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    cleaned = 0
    for job in expired:
        artifact_keys = list(job.source_keys or []) + list(job.logo_keys or [])
        if job.output_zip_key:
            artifact_keys.append(job.output_zip_key)
        for key in artifact_keys:
            try:
                storage.delete_file(key)
            except Exception:
                pass
        job.source_keys = []
        job.source_names = []
        job.logo_keys = []
        job.logo_names = []
        job.output_zip_key = None
        job.deleted_at = now
        job.updated_at = now
        cleaned += 1
    if cleaned:
        db.commit()
    return cleaned
