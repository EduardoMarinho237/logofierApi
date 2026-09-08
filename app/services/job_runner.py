from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import time
import uuid

from app.config import settings
from app.database import SessionLocal
from app.models import Job, JobStatus
from app.schemas import JobConfig
from app.services import storage
from app.services.pdf_processor import (
    create_zip_from_pdf_bytes,
    get_pdf_page_count,
    stamp_logo_on_pdf,
)
from app.utils.image_utils import convert_to_png


def _unique_names(names: list[str]) -> list[str]:
    """Rename duplicates with (1), (2), (3)... suffixes after the base name."""
    used: set[str] = set()
    result: list[str] = []
    for name in names:
        candidate = name
        n = 1
        while candidate in used:
            base, ext = os.path.splitext(name)
            candidate = f"{base} ({n}){ext}"
            n += 1
        used.add(candidate)
        result.append(candidate)
    return result


def _update_job(job_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            for key, value in fields.items():
                setattr(job, key, value)
            db.commit()
    finally:
        db.close()


def run_pdf_job(job_id: str, source_keys: list[str], logo_keys: list[str], mode: str) -> None:
    _update_job(job_id, status=JobStatus.processing)
    tmp_dir = tempfile.mkdtemp(prefix=f"logofier_{job_id}_")
    started = time.monotonic()

    def _within_deadline() -> None:
        if time.monotonic() - started > settings.JOB_PROCESSING_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"Job exceeded the processing timeout of "
                f"{settings.JOB_PROCESSING_TIMEOUT_SECONDS}s"
            )

    try:
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            config = JobConfig.model_validate(job.config)
        finally:
            db.close()

        if mode == "multiple_logos":
            total = len(logo_keys)
            _update_job(job_id, total_files=total)
            processed_pdfs: list[list[tuple[str, bytes]]] = []
            source_key = source_keys[0]
            pdf_bytes = storage.download_file(source_key)
            source_name = (job.source_names or [""])[0] or os.path.basename(source_key)
            source_base = os.path.splitext(source_name)[0]
            logo_names = job.logo_names or []
            for idx, logo_key in enumerate(logo_keys):
                _within_deadline()
                logo_bytes = storage.download_file(logo_key)
                # Use the per-logo position if the user set one individually,
                # otherwise fall back to the shared config position.
                eff_config = config
                per_logo = config.logo_positions.get(str(idx))
                if per_logo:
                    eff_config = config.model_copy(
                        update={
                            "position": per_logo.position,
                            "position_rest": per_logo.position_rest,
                        }
                    )
                stamped = stamp_logo_on_pdf(
                    pdf_bytes, logo_bytes, eff_config, preserve_aspect=True
                )
                logo_file = logo_names[idx] if idx < len(logo_names) else os.path.basename(logo_key)
                logo_base = os.path.splitext(logo_file)[0]
                output_name = f"{source_base}_{logo_base}.pdf"
                processed_pdfs.append([(output_name, stamped)])
                _update_job(job_id, processed_files=idx + 1)
        else:
            total = len(source_keys)
            _update_job(job_id, total_files=total)
            processed_pdfs = []
            logo_bytes = storage.download_file(logo_keys[0] if logo_keys else f"logos/{job_id}/logo.png")
            source_names = job.source_names or []
            for idx, source_key in enumerate(source_keys):
                _within_deadline()
                pdf_bytes = storage.download_file(source_key)
                stamped = stamp_logo_on_pdf(pdf_bytes, logo_bytes, config)
                source_file = source_names[idx] if idx < len(source_names) else os.path.basename(source_key)
                source_base = os.path.splitext(source_file)[0]
                output_name = f"{source_base}.pdf"
                processed_pdfs.append([(output_name, stamped)])
                _update_job(job_id, processed_files=idx + 1)

        all_files = [item for group in processed_pdfs for item in group]
        output_names = _unique_names([n for (n, _) in all_files])
        all_files = [(new_name, data) for (_, data), new_name in zip(all_files, output_names)]
        zip_bytes = create_zip_from_pdf_bytes(all_files)
        zip_key = f"output/{job_id}/result.zip"
        storage.upload_file(zip_bytes, zip_key, content_type="application/zip")

        _update_job(job_id, status=JobStatus.done, output_zip_key=zip_key)

        # Storage hygiene: keep only the final ZIP. The raw input PDFs and the
        # per-job logo copies are not needed anymore. The user's logo library
        # (user-logos/...) is intentionally left untouched.
        for artifact_key in list(source_keys) + list(logo_keys):
            try:
                storage.delete_file(artifact_key)
            except Exception:
                pass

    except Exception as exc:
        _update_job(job_id, status=JobStatus.failed, error_message=str(exc))
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
