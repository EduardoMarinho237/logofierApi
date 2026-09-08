import os
from collections import defaultdict
from datetime import datetime, timezone

from app.models import Job, JobStatus, User, UserLogo
from app.services import storage

CAT_AVATARS = "avatars"
CAT_LOGOS = "logos"
CAT_PDFS = "pdfs"
CAT_ZIPS = "zips"
CAT_OTHER = "other"

CATEGORIES = [CAT_AVATARS, CAT_LOGOS, CAT_PDFS, CAT_ZIPS, CAT_OTHER]


def _categorize(key: str, job_to_user: dict[str, str]) -> tuple[str, str | None]:
    """Return (category, owner_user_id|None)."""
    parts = key.split("/")
    if not parts:
        return CAT_OTHER, None
    root = parts[0]

    if root == "avatars" and len(parts) >= 2:
        return CAT_AVATARS, parts[1]
    if root == "user-logos" and len(parts) >= 2:
        return CAT_LOGOS, parts[1]
    if root == "logos" and len(parts) >= 2:
        return CAT_LOGOS, job_to_user.get(parts[1])
    if root == "jobs" and len(parts) >= 3 and parts[2] == "sources":
        return CAT_PDFS, job_to_user.get(parts[1])
    if root == "output" and len(parts) >= 2:
        return CAT_ZIPS, job_to_user.get(parts[1])
    return CAT_OTHER, None


def _active_job_map(db) -> dict[str, str]:
    jobs = db.query(Job.id, Job.user_id).filter(Job.deleted_at.is_(None)).all()
    return {job_id: user_id for job_id, user_id in jobs}


def _active_user_ids(db) -> set[str]:
    ids = db.query(User.id).filter(User.deleted_at.is_(None)).all()
    return {uid for (uid,) in ids}


def _job_display(job: Job) -> str:
    if job.title:
        return job.title
    names = list(job.source_names or [])
    if names:
        return os.path.splitext(names[0])[0]
    return "Processamento sem título"


def _source_label(job: Job, key: str, fallback: str) -> str:
    keys = list(job.source_keys or [])
    names = list(job.source_names or [])
    try:
        idx = keys.index(key)
    except ValueError:
        idx = -1
    if 0 <= idx < len(names):
        return f"{names[idx]} · PDF de origem"
    return fallback


def compute_admin_stats(db):
    files = storage.list_files()

    jobs = (
        db.query(Job.id, Job.user_id, Job.status, Job.total_files)
        .filter(Job.deleted_at.is_(None))
        .all()
    )
    job_to_user = {job_id: user_id for job_id, user_id, *_ in jobs}

    processings = len(jobs)
    processings_done = sum(1 for job in jobs if job[2] == JobStatus.done)
    pdfs_generated = sum((job[3] or 0) for job in jobs if job[2] == JobStatus.done)

    active_users = (
        db.query(User).filter(User.deleted_at.is_(None)).order_by(User.name).all()
    )
    active_user_ids = {u.id for u in active_users}

    storage_totals: dict[str, int] = defaultdict(int)
    per_user: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    orphans = 0

    for key, size in files:
        category, owner = _categorize(key, job_to_user)
        storage_totals[category] += size
        if owner and owner in active_user_ids:
            per_user[owner][category] += size
        elif category != CAT_OTHER:
            orphans += size

    users_rows = []
    for u in active_users:
        user_storage = per_user.get(u.id, defaultdict(int))
        user_total = sum(user_storage.get(c, 0) for c in CATEGORIES)
        jobs_count = sum(1 for _, uid, *_ in jobs if uid == u.id)
        users_rows.append(
            {
                "user_id": u.id,
                "name": u.name,
                "email": u.email,
                "role": u.role,
                "is_active": u.is_active,
                "jobs_count": jobs_count,
                "storage": {
                    c: user_storage.get(c, 0) for c in CATEGORIES
                },
                "storage_total": user_total,
            }
        )

    storage_total = sum(storage_totals.get(c, 0) for c in CATEGORIES)

    return {
        "totals": {
            "processings": processings,
            "processings_done": processings_done,
            "pdfs_generated": pdfs_generated,
        },
        "storage": {c: storage_totals.get(c, 0) for c in CATEGORIES},
        "storage_total": storage_total,
        "orphans": orphans,
        "users": users_rows,
    }


def list_orphan_files(db) -> list[tuple[str, int]]:
    """Return (key, size) for every file with no active owner."""
    files = storage.list_files()
    job_to_user = _active_job_map(db)
    active_user_ids = _active_user_ids(db)

    orphans: list[tuple[str, int]] = []
    for key, size in files:
        category, owner = _categorize(key, job_to_user)
        if category == CAT_OTHER:
            continue
        if owner is None or owner not in active_user_ids:
            orphans.append((key, size))
    return orphans


def list_user_files(db, user_id):
    """Return (user, payload) with every stored file owned by an active user."""
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        return None

    files = storage.list_files()

    logos = (
        db.query(UserLogo)
        .filter(
            UserLogo.user_id == user_id,
            UserLogo.deleted_at.is_(None),
        )
        .all()
    )
    logo_key_to_name: dict[str, str] = {}
    for logo in logos:
        logo_key_to_name[logo.storage_key] = logo.name
        logo_key_to_name[logo.thumb_key] = logo.name

    jobs = (
        db.query(Job)
        .filter(Job.user_id == user_id, Job.deleted_at.is_(None))
        .all()
    )
    jobs_by_id = {job.id: job for job in jobs}

    entries: list[dict] = []
    total_bytes = 0

    for key, size in files:
        parts = key.split("/")
        category = None
        label = key.rsplit("/", 1)[-1]

        if len(parts) >= 2 and parts[0] in ("avatars", "user-logos") and parts[1] == user_id:
            if parts[0] == "avatars":
                category = CAT_AVATARS
                label = "Avatar"
            else:
                category = CAT_LOGOS
                label = logo_key_to_name.get(key, label)
        elif len(parts) >= 2 and parts[0] in ("logos", "jobs", "output") and parts[1] in jobs_by_id:
            job = jobs_by_id[parts[1]]
            if parts[0] == "logos":
                category = CAT_LOGOS
                label = f"Logo de processamento · {_job_display(job)}"
            elif parts[0] == "jobs" and len(parts) >= 4 and parts[2] == "sources":
                category = CAT_PDFS
                label = _source_label(job, key, label)
            elif parts[0] == "output":
                category = CAT_ZIPS
                label = f"Resultado · {_job_display(job)}"

        if category is None:
            continue
        entries.append({"key": key, "category": category, "size": size, "label": label})
        total_bytes += size

    return user, {"files": entries, "total_bytes": total_bytes}


def delete_user_file(db, user_id: str, key: str) -> int:
    """Delete a single file owned by a user, cleaning up linked records.

    Returns the number of freed bytes. Raises ValueError when the key is not
    owned by the user or references an unknown file type.
    """
    user = (
        db.query(User)
        .filter(User.id == user_id, User.deleted_at.is_(None))
        .first()
    )
    if not user:
        raise ValueError("User not found")

    sizes = dict(storage.list_files())
    freed = 0
    now = datetime.now(timezone.utc)

    def remove(key: str) -> None:
        nonlocal freed
        if key in sizes:
            freed += sizes[key]
        try:
            storage.delete_file(key)
        except Exception:
            pass

    parts = key.split("/")

    # Avatar files (delete together, then clear the user columns).
    if len(parts) >= 2 and parts[0] == "avatars" and parts[1] == user_id:
        if key in (user.avatar_key, user.avatar_thumb_key):
            if user.avatar_key:
                remove(user.avatar_key)
            if user.avatar_thumb_key:
                remove(user.avatar_thumb_key)
            user.avatar_key = None
            user.avatar_thumb_key = None
            user.updated_at = now
            db.commit()
            return freed

    # Library logos (delete full + thumb, then soft-delete the logo row).
    if len(parts) >= 2 and parts[0] == "user-logos" and parts[1] == user_id:
        logo = (
            db.query(UserLogo)
            .filter(
                UserLogo.user_id == user_id,
                UserLogo.deleted_at.is_(None),
                (UserLogo.storage_key == key) | (UserLogo.thumb_key == key),
            )
            .first()
        )
        if logo:
            remove(logo.storage_key)
            remove(logo.thumb_key)
            logo.deleted_at = now
            logo.updated_at = now
            db.commit()
            return freed

    # Job files (delete every artifact of the job, then soft-delete the job).
    if len(parts) >= 2 and parts[0] in ("logos", "jobs", "output"):
        job = (
            db.query(Job)
            .filter(
                Job.id == parts[1],
                Job.user_id == user_id,
                Job.deleted_at.is_(None),
            )
            .first()
        )
        if job:
            prefixes = (
                f"logos/{job.id}/",
                f"jobs/{job.id}/sources/",
                f"output/{job.id}/",
            )
            if key.startswith(prefixes):
                for stored_key in sizes:
                    if stored_key.startswith(prefixes):
                        remove(stored_key)
                job.source_keys = []
                job.source_names = []
                job.logo_keys = []
                job.logo_names = []
                job.output_zip_key = None
                job.deleted_at = now
                job.updated_at = now
                db.commit()
                return freed

    raise ValueError("File does not belong to this user")