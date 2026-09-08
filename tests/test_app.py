import json
import os
import time
import uuid

from sqlalchemy import text

_test_db = os.environ.get(
    "TEST_DATABASE_URL", f"sqlite:///./test_logofier_{uuid.uuid4().hex}.db"
)
os.environ["JWT_SECRET"] = "test-secret-change-me-for-tests-1234567890abcdef"
os.environ["DATABASE_URL"] = _test_db
os.environ["RATE_LIMIT_LOGIN"] = "1000/minute"

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.auth import create_access_token, hash_password
from app.database import SessionLocal, engine
from app.main import app
from app.models import User
from app.services import storage as storage_module


@pytest.fixture(scope="session")
def _cleanup():
    yield
    engine.dispose()
    if _test_db.startswith("sqlite"):
        db_path = _test_db.replace("sqlite:///./", "")
        if os.path.exists(db_path):
            os.remove(db_path)


@pytest.fixture(scope="session", autouse=True)
def _setup_db(_cleanup):
    from alembic import command
    from alembic.config import Config

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", _test_db)
    command.upgrade(alembic_cfg, "head")


@pytest.fixture
def client(_cleanup):
    return TestClient(app)


def _create_admin(db):
    admin = User(
        id="00000000-0000-0000-0000-000000000001",
        email="dev@logofier.com.br",
        hashed_password=hash_password("adminpass123"),
        name="Eduardo Marinho",
        role="admin",
        is_active=True,
        is_password_set=True,
    )
    db.add(admin)
    db.commit()
    return admin


@pytest.fixture(autouse=True)
def clean_tables(_cleanup):
    db = SessionLocal()
    try:
        db.execute(text("DELETE FROM jobs"))
        db.execute(text("DELETE FROM refresh_tokens"))
        db.execute(text("DELETE FROM presets"))
        db.execute(text("DELETE FROM user_logos"))
        db.execute(text("DELETE FROM users"))
        db.commit()
        _create_admin(db)
    finally:
        db.close()
    yield


@pytest.fixture
def admin_token(client):
    r = client.post(
        "/api/auth/login",
        json={"email": "dev@logofier.com.br", "password": "adminpass123"},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.fixture
def token(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": f"user_{uuid.uuid4().hex}@logofier.com",
            "password": "secret123",
            "name": "Test User",
        },
    )
    assert r.status_code == 201, r.text
    user_email = r.json()["email"]
    r = client.post(
        "/api/auth/login",
        json={"email": user_email, "password": "secret123"},
    )
    assert r.status_code == 200
    return r.json()["access_token"]


@pytest.fixture(autouse=True)
def mock_storage(monkeypatch):
    server = {}

    def fake_upload(data, key, content_type="application/octet-stream"):
        server[key] = data
        return key

    def fake_download(key):
        return server[key]

    monkeypatch.setattr(storage_module, "upload_file", fake_upload)
    monkeypatch.setattr(storage_module, "download_file", fake_download)
    monkeypatch.setattr(storage_module, "delete_file", lambda key: server.pop(key, None))
    monkeypatch.setattr(storage_module, "_mock_storage_dict", server, raising=False)


def make_logo(path="test_logo.png"):
    img = Image.new("RGBA", (200, 100), (255, 0, 0, 255))
    img.save(path)
    return path


def make_pdf(path="test_sample.pdf", pages=5):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    with open(path, "wb") as f:
        f.write(doc.tobytes())
    doc.close()
    return path


def test_auth_flow(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={"email": "auth@logofier.com", "password": "secret123", "name": "Auth User"},
    )
    assert r.status_code == 201

    r = client.post(
        "/api/auth/login",
        json={"email": "auth@logofier.com", "password": "secret123"},
    )
    assert r.status_code == 200
    assert "access_token" in r.json()

    r = client.post(
        "/api/auth/login",
        json={"email": "auth@logofier.com", "password": "wrong"},
    )
    assert r.status_code == 401


def test_refresh_token_rotation(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={"email": "refresh@logofier.com", "password": "secret123", "name": "Refresh User"},
    )
    assert r.status_code == 201

    r = client.post(
        "/api/auth/login",
        json={"email": "refresh@logofier.com", "password": "secret123"},
    )
    assert r.status_code == 200
    body = r.json()
    assert "refresh_token" in body
    rt_old = body["refresh_token"]

    # First refresh → new pair, old token revoked
    r = client.post("/api/auth/refresh", json={"refresh_token": rt_old})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "access_token" in body
    assert "refresh_token" in body
    rt_new = body["refresh_token"]
    assert rt_new != rt_old

    # Reuse of the rotated token must be rejected
    r = client.post("/api/auth/refresh", json={"refresh_token": rt_old})
    assert r.status_code == 401

    # The current token still works
    r = client.post("/api/auth/refresh", json={"refresh_token": rt_new})
    assert r.status_code == 200


def test_logout_revokes_all_tokens(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={"email": "logout@logofier.com", "password": "secret123", "name": "Logout User"},
    )
    assert r.status_code == 201

    r = client.post(
        "/api/auth/login",
        json={"email": "logout@logofier.com", "password": "secret123"},
    )
    assert r.status_code == 200
    body = r.json()
    access = body["access_token"]
    rt = body["refresh_token"]

    auth = {"Authorization": f"Bearer {access}"}
    assert client.get("/api/auth/me", headers=auth).status_code == 200

    # Logout revokes the refresh token and bumps token_version.
    r = client.post("/api/auth/logout", json={"refresh_token": rt})
    assert r.status_code == 204

    # Refresh no longer works after logout.
    r = client.post("/api/auth/refresh", json={"refresh_token": rt})
    assert r.status_code == 401

    # Outstanding access token is invalidated as well.
    r = client.get("/api/auth/me", headers=auth)
    assert r.status_code == 401


def test_create_user_forces_role_user(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "crole@logofier.com",
            "password": "secret123",
            "name": "Role Test",
            "role": "admin",
        },
    )
    assert r.status_code == 201
    assert r.json()["role"] == "user"
    assert r.json()["is_active"] is True


def test_user_restrict_blocks_login_and_access(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "restrict@logofier.com",
            "password": "secret123",
            "name": "Restrict Me",
        },
    )
    assert r.status_code == 201
    user_id = r.json()["id"]

    r = client.put(f"/api/users/{user_id}", headers=headers, json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False

    r = client.post(
        "/api/auth/login",
        json={"email": "restrict@logofier.com", "password": "secret123"},
    )
    assert r.status_code == 403

    user_token = create_access_token(user_id, token_version=1)
    r = client.get(
        "/api/jobs",
        headers={"Authorization": f"Bearer {user_token}"},
    )
    assert r.status_code == 403

    r = client.put(f"/api/users/{user_id}", headers=headers, json={"is_active": True})
    assert r.status_code == 200
    r = client.post(
        "/api/auth/login",
        json={"email": "restrict@logofier.com", "password": "secret123"},
    )
    assert r.status_code == 200


def test_cannot_restrict_own_account(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.get("/api/users", headers=headers)
    admin_id = next(u["id"] for u in r.json() if u["email"] == "dev@logofier.com.br")

    r = client.put(f"/api/users/{admin_id}", headers=headers, json={"is_active": False})
    assert r.status_code == 400


def test_update_user_name_email_and_password(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "edit@logofier.com",
            "password": "secret123",
            "name": "Old Name",
        },
    )
    user_id = r.json()["id"]

    r = client.put(
        f"/api/users/{user_id}",
        headers=headers,
        json={"name": "New Name", "email": "edited@logofier.com"},
    )
    assert r.status_code == 200
    assert r.json()["name"] == "New Name"
    assert r.json()["email"] == "edited@logofier.com"

    r = client.post(
        "/api/auth/login",
        json={"email": "edited@logofier.com", "password": "newpass123"},
    )
    assert r.status_code == 401

    r = client.put(f"/api/users/{user_id}", headers=headers, json={"password": "newpass123"})
    assert r.status_code == 200

    r = client.post(
        "/api/auth/login",
        json={"email": "edited@logofier.com", "password": "newpass123"},
    )
    assert r.status_code == 200


def test_admin_metadata(client, admin_token, monkeypatch):
    from app.models import Job, JobStatus

    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "stats@logofier.com",
            "password": "secret123",
            "name": "Stats User",
        },
    )
    user_id = r.json()["id"]

    avatar_bytes = b"A" * 100
    logo_bytes = b"B" * 200
    thumb_bytes = b"C" * 50
    pdf_bytes = b"D" * 300
    zip_bytes = b"E" * 500

    storage_module.upload_file(avatar_bytes, f"avatars/{user_id}/a.jpg", content_type="image/jpeg")
    storage_module.upload_file(logo_bytes, f"user-logos/{user_id}/l.png", content_type="image/png")
    storage_module.upload_file(thumb_bytes, f"user-logos/{user_id}/l_thumb.png", content_type="image/png")

    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        job = Job(
            id=job_id,
            user_id=user_id,
            status=JobStatus.done,
            logo_key=f"logos/{job_id}/logo_0.png",
            config={},
            source_keys=[f"jobs/{job_id}/sources/a.pdf"],
            output_zip_key=f"output/{job_id}/result.zip",
            total_files=5,
            processed_files=5,
        )
        db.add(job)
        db.commit()
    finally:
        db.close()

    storage_module.upload_file(b"B", f"logos/{job_id}/logo_0.png", content_type="image/png")
    storage_module.upload_file(pdf_bytes, f"jobs/{job_id}/sources/a.pdf", content_type="application/pdf")
    storage_module.upload_file(zip_bytes, f"output/{job_id}/result.zip", content_type="application/zip")

    def fake_list_files(prefix=""):
        return [
            (k, len(v))
            for k, v in storage_module._mock_storage_dict.items()
            if k.startswith(prefix)
        ]

    monkeypatch.setattr(storage_module, "list_files", fake_list_files)

    r = client.get("/api/admin/metadata", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["totals"]["processings"] == 1
    assert body["totals"]["processings_done"] == 1
    assert body["totals"]["pdfs_generated"] == 5

    assert body["storage"]["avatars"] == 100
    assert body["storage"]["logos"] == 200 + 50 + 1
    assert body["storage"]["pdfs"] == 300
    assert body["storage"]["zips"] == 500
    assert body["storage_total"] == 100 + 200 + 50 + 1 + 300 + 500
    assert body["orphans"] == 0

    user_row = next(u for u in body["users"] if u["user_id"] == user_id)
    assert user_row["jobs_count"] == 1
    assert user_row["storage"]["avatars"] == 100
    assert user_row["storage"]["logos"] == 200 + 50 + 1
    assert user_row["storage"]["pdfs"] == 300
    assert user_row["storage"]["zips"] == 500
    assert user_row["storage_total"] == body["storage_total"]


def test_admin_metadata_counts_deleted_user_as_orphans(client, admin_token, monkeypatch):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": "ghost@logofier.com",
            "password": "secret123",
            "name": "Ghost",
        },
    )
    user_id = r.json()["id"]
    storage_module.upload_file(b"X" * 64, f"user-logos/{user_id}/l.png", content_type="image/png")

    r = client.delete(f"/api/users/{user_id}", headers=headers)
    assert r.status_code == 204

    def fake_list_files(prefix=""):
        return [
            (k, len(v))
            for k, v in storage_module._mock_storage_dict.items()
            if k.startswith(prefix)
        ]

    monkeypatch.setattr(storage_module, "list_files", fake_list_files)

    r = client.get("/api/admin/metadata", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["storage"]["logos"] == 64
    assert body["orphans"] == 64
    assert all(u["user_id"] != user_id for u in body["users"])


def _patched_list_files(monkeypatch):
    def fake(prefix=""):
        return [
            (k, len(v))
            for k, v in storage_module._mock_storage_dict.items()
            if k.startswith(prefix)
        ]

    monkeypatch.setattr(storage_module, "list_files", fake)


def _make_content_user(client, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = client.post(
        "/api/users",
        headers=headers,
        json={
            "email": f"files_{uuid.uuid4().hex}@logofier.com",
            "password": "secret123",
            "name": "Files User",
        },
    )
    assert r.status_code == 201, r.text
    return headers, r.json()["id"]


def test_admin_delete_orphans(client, admin_token, monkeypatch):
    from app.models import Job, JobStatus, User, UserLogo

    headers, user_id = _make_content_user(client, admin_token)

    storage_module.upload_file(b"A" * 10, f"avatars/{user_id}/a.jpg", content_type="image/jpeg")
    storage_module.upload_file(b"B" * 20, f"avatars/{user_id}/a_thumb.jpg", content_type="image/jpeg")
    storage_module.upload_file(b"C" * 30, f"user-logos/{user_id}/l.png", content_type="image/png")
    storage_module.upload_file(b"D" * 40, f"user-logos/{user_id}/l_thumb.png", content_type="image/png")

    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user_id).update(
            {
                User.avatar_key: f"avatars/{user_id}/a.jpg",
                User.avatar_thumb_key: f"avatars/{user_id}/a_thumb.jpg",
            }
        )
        db.add(
            UserLogo(
                user_id=user_id,
                name="Meu logo",
                storage_key=f"user-logos/{user_id}/l.png",
                thumb_key=f"user-logos/{user_id}/l_thumb.png",
            )
        )
        db.add(
            Job(
                id=job_id,
                user_id=user_id,
                status=JobStatus.done,
                logo_key=f"logos/{job_id}/logo_0.png",
                config={},
                source_keys=[f"jobs/{job_id}/sources/a.pdf"],
                output_zip_key=f"output/{job_id}/result.zip",
            )
        )
        db.commit()
    finally:
        db.close()

    storage_module.upload_file(b"E" * 50, f"logos/{job_id}/logo_0.png", content_type="image/png")
    storage_module.upload_file(b"F" * 60, f"jobs/{job_id}/sources/a.pdf", content_type="application/pdf")
    storage_module.upload_file(b"G" * 70, f"output/{job_id}/result.zip", content_type="application/zip")

    storage_module.upload_file(b"H" * 1, "avatars/ghost/a.jpg", content_type="image/jpeg")
    storage_module.upload_file(b"I" * 2, "user-logos/ghost/l.png", content_type="image/png")
    storage_module.upload_file(b"J" * 3, "output/nojob/result.zip", content_type="application/zip")
    storage_module.upload_file(b"K" * 4, "random/not-categorized.bin", content_type="application/octet-stream")

    _patched_list_files(monkeypatch)

    r = client.post("/api/admin/orphans/delete", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 3
    assert body["freed_bytes"] == 1 + 2 + 3

    stored = dict(storage_module._mock_storage_dict)
    assert "random/not-categorized.bin" in stored
    assert f"avatars/{user_id}/a.jpg" in stored
    assert f"user-logos/{user_id}/l.png" in stored
    assert f"jobs/{job_id}/sources/a.pdf" in stored
    assert f"output/{job_id}/result.zip" in stored
    for orphan in ("avatars/ghost/a.jpg", "user-logos/ghost/l.png", "output/nojob/result.zip"):
        assert orphan not in stored


def test_admin_list_user_files(client, admin_token, monkeypatch):
    from app.models import Job, JobStatus, User, UserLogo

    headers, user_id = _make_content_user(client, admin_token)

    storage_module.upload_file(b"A" * 10, f"avatars/{user_id}/a.jpg", content_type="image/jpeg")
    storage_module.upload_file(b"C" * 30, f"user-logos/{user_id}/l.png", content_type="image/png")
    storage_module.upload_file(b"D" * 40, f"user-logos/{user_id}/l_thumb.png", content_type="image/png")

    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.query(User).filter(User.id == user_id).update(
            {
                User.avatar_key: f"avatars/{user_id}/a.jpg",
                User.avatar_thumb_key: None,
            }
        )
        db.add(
            UserLogo(
                user_id=user_id,
                name="Meu logo",
                storage_key=f"user-logos/{user_id}/l.png",
                thumb_key=f"user-logos/{user_id}/l_thumb.png",
            )
        )
        db.add(
            Job(
                id=job_id,
                user_id=user_id,
                status=JobStatus.done,
                logo_key=f"logos/{job_id}/logo_0.png",
                config={},
                source_keys=[f"jobs/{job_id}/sources/relatorio.pdf"],
                source_names=["relatorio.pdf"],
                output_zip_key=f"output/{job_id}/result.zip",
            )
        )
        db.commit()
    finally:
        db.close()

    storage_module.upload_file(b"E" * 50, f"logos/{job_id}/logo_0.png", content_type="image/png")
    storage_module.upload_file(b"F" * 60, f"jobs/{job_id}/sources/relatorio.pdf", content_type="application/pdf")
    storage_module.upload_file(b"G" * 70, f"output/{job_id}/result.zip", content_type="application/zip")
    storage_module.upload_file(b"Z" * 5, "user-logos/other/x.png", content_type="image/png")
    storage_module.upload_file(b"W" * 5, "random/key.bin", content_type="application/octet-stream")

    _patched_list_files(monkeypatch)

    r = client.get(f"/api/admin/users/{user_id}/files", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"].startswith("files_")
    assert body["total_bytes"] == 10 + 30 + 40 + 50 + 60 + 70

    by_key = {f["key"]: f for f in body["files"]}
    assert set(by_key) == {
        f"avatars/{user_id}/a.jpg",
        f"user-logos/{user_id}/l.png",
        f"user-logos/{user_id}/l_thumb.png",
        f"logos/{job_id}/logo_0.png",
        f"jobs/{job_id}/sources/relatorio.pdf",
        f"output/{job_id}/result.zip",
    }
    assert by_key[f"avatars/{user_id}/a.jpg"]["category"] == "avatars"
    assert by_key[f"user-logos/{user_id}/l.png"]["label"] == "Meu logo"
    assert by_key[f"logos/{job_id}/logo_0.png"]["category"] == "logos"
    assert by_key[f"jobs/{job_id}/sources/relatorio.pdf"]["label"].startswith("relatorio.pdf")
    assert by_key[f"output/{job_id}/result.zip"]["category"] == "zips"

    r = client.get(f"/api/admin/users/{uuid.uuid4().hex}/files", headers=headers)
    assert r.status_code == 404


def test_admin_delete_user_file_logo(client, admin_token, monkeypatch):
    from app.models import User, UserLogo

    headers, user_id = _make_content_user(client, admin_token)

    storage_module.upload_file(b"C" * 30, f"user-logos/{user_id}/l.png", content_type="image/png")
    storage_module.upload_file(b"D" * 40, f"user-logos/{user_id}/l_thumb.png", content_type="image/png")
    storage_module.upload_file(b"Q" * 9, f"user-logos/{user_id}/other.png", content_type="image/png")

    db = SessionLocal()
    try:
        db.add(
            UserLogo(
                user_id=user_id,
                name="Meu logo",
                storage_key=f"user-logos/{user_id}/l.png",
                thumb_key=f"user-logos/{user_id}/l_thumb.png",
            )
        )
        db.commit()
    finally:
        db.close()

    _patched_list_files(monkeypatch)

    r = client.request(
        "DELETE",
        f"/api/admin/users/{user_id}/files",
        headers=headers,
        json={"key": f"user-logos/{user_id}/l_thumb.png"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == 30 + 40

    stored = dict(storage_module._mock_storage_dict)
    assert f"user-logos/{user_id}/l.png" not in stored
    assert f"user-logos/{user_id}/l_thumb.png" not in stored
    assert f"user-logos/{user_id}/other.png" in stored

    db = SessionLocal()
    try:
        logo = db.query(UserLogo).filter(UserLogo.user_id == user_id).first()
        assert logo.deleted_at is not None
    finally:
        db.close()

    # deleting a file that does not belong to this user -> 400
    r = client.request(
        "DELETE",
        f"/api/admin/users/{user_id}/files",
        headers=headers,
        json={"key": "user-logos/other/x.png"},
    )
    assert r.status_code == 400

    # deleting an unknown prefix -> 400
    r = client.request(
        "DELETE",
        f"/api/admin/users/{user_id}/files",
        headers=headers,
        json={"key": "random/key.bin"},
    )
    assert r.status_code == 400


def test_admin_delete_user_file_job(client, admin_token, monkeypatch):
    from app.models import Job, JobStatus, UserLogo

    headers, user_id = _make_content_user(client, admin_token)

    job_id = uuid.uuid4().hex
    db = SessionLocal()
    try:
        db.add(
            Job(
                id=job_id,
                user_id=user_id,
                status=JobStatus.done,
                logo_key=f"logos/{job_id}/logo_0.png",
                logo_keys=[f"logos/{job_id}/logo_0.png"],
                config={},
                source_keys=[f"jobs/{job_id}/sources/relatorio.pdf"],
                source_names=["relatorio.pdf"],
                output_zip_key=f"output/{job_id}/result.zip",
            )
        )
        db.commit()
    finally:
        db.close()

    storage_module.upload_file(b"E" * 50, f"logos/{job_id}/logo_0.png", content_type="image/png")
    storage_module.upload_file(b"F" * 60, f"jobs/{job_id}/sources/relatorio.pdf", content_type="application/pdf")
    storage_module.upload_file(b"G" * 70, f"output/{job_id}/result.zip", content_type="application/zip")

    _patched_list_files(monkeypatch)

    r = client.request(
        "DELETE",
        f"/api/admin/users/{user_id}/files",
        headers=headers,
        json={"key": f"jobs/{job_id}/sources/relatorio.pdf"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["freed_bytes"] == 50 + 60 + 70

    stored = dict(storage_module._mock_storage_dict)
    assert not any(k.startswith(f"logos/{job_id}/") for k in stored)
    assert not any(k.startswith(f"jobs/{job_id}/") for k in stored)
    assert not any(k.startswith(f"output/{job_id}/") for k in stored)

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        assert job.deleted_at is not None
        assert job.output_zip_key is None
        assert job.source_keys == []
    finally:
        db.close()


def test_admin_dashboard_rejects_normal_user(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/metadata", headers=headers).status_code == 403
    assert client.post("/api/admin/orphans/delete", headers=headers).status_code == 403
    assert client.get("/api/admin/users/abc/files", headers=headers).status_code == 403
    assert client.request(
        "DELETE", "/api/admin/users/abc/files", headers=headers, json={"key": "x"}
    ).status_code == 403


def test_passwordless_account_cannot_login(client, clean_tables):
    # A user with no password set must NOT be able to bootstrap on first login.
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "dev@logofier.com.br").first()
        admin.hashed_password = ""
        admin.is_password_set = False
        db.commit()
    finally:
        db.close()

    r = client.post(
        "/api/auth/login",
        json={"email": "dev@logofier.com.br", "password": "newadminpass"},
    )
    assert r.status_code == 401


def test_full_job_flow(client, token):
    from app.services import storage as storage_module

    headers = {"Authorization": f"Bearer {token}"}

    make_logo()
    make_pdf()

    config = json.dumps(
        {
            "page_selection": {
                "mode": "first_n_and_last_m",
                "first_count": 1,
                "last_count": 1,
            },
            "position": {"x": 400, "y": 50, "width": 120, "height": 40},
        }
    )

    with open("test_logo.png", "rb") as logo:
        files = {
            "logo": ("logo.png", logo, "image/png"),
            "config": (None, config, "application/json"),
        }
        r = client.post("/api/jobs", headers=headers, files=files)
    assert r.status_code == 201, r.text
    job_id = r.json()["id"]

    with open("test_sample.pdf", "rb") as pdf:
        r = client.post(
            f"/api/jobs/{job_id}/files",
            headers=headers,
            files=[("files", ("doc.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 204

    r = client.post(f"/api/jobs/{job_id}/process", headers=headers)
    assert r.status_code == 202

    state = {}
    r = client.get(f"/api/jobs/{job_id}/status", headers=headers)
    assert r.status_code == 200
    state = r.json()
    assert state["status"] in ("done", "processing", "failed")
    assert state["total_files"] == 1

    if state["status"] == "done":
        r = client.get(f"/api/jobs/{job_id}/download", headers=headers)
        assert r.status_code == 200
        assert r.content[:2] == b"PK"


def test_preview(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    make_pdf()
    with open("test_sample.pdf", "rb") as pdf:
        r = client.post(
            "/api/preview",
            headers=headers,
            files=[("pdf", ("preview.pdf", pdf, "application/pdf"))],
            data={"page": "0"},
        )
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def create_uploaded_job(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    make_logo()
    make_pdf()
    config = json.dumps(
        {
            "page_selection": {"mode": "all"},
            "position": {"x": 100, "y": 50, "width": 120, "height": 40},
        }
    )
    with open("test_logo.png", "rb") as logo:
        files = {
            "logo": ("logo.png", logo, "image/png"),
            "config": (None, config, "application/json"),
        }
        r = client.post("/api/jobs", headers=headers, files=files)
    assert r.status_code == 201
    job_id = r.json()["id"]
    with open("test_sample.pdf", "rb") as pdf:
        r = client.post(
            f"/api/jobs/{job_id}/files",
            headers=headers,
            files=[("files", ("doc.pdf", pdf, "application/pdf"))],
        )
    assert r.status_code == 204
    return job_id, headers


def test_get_job(client, token):
    job_id, headers = create_uploaded_job(client, token)
    r = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == job_id
    assert body["config"]["page_selection"]["mode"] == "all"


def test_job_logo(client, token):
    job_id, headers = create_uploaded_job(client, token)
    r = client.get(f"/api/jobs/{job_id}/logo", headers=headers)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_job_preview_page(client, token):
    job_id, headers = create_uploaded_job(client, token)
    r = client.get(f"/api/jobs/{job_id}/preview-page?page=0", headers=headers)
    assert r.status_code == 200
    assert r.headers["Content-Type"] == "image/png"
    assert r.headers.get("X-Page-Width-Points")
    assert r.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_update_config(client, token):
    job_id, headers = create_uploaded_job(client, token)
    new_config = {
        "page_selection": {"mode": "first_only"},
        "position": {"x": 5, "y": 5, "width": 50, "height": 20},
    }
    r = client.put(f"/api/jobs/{job_id}/config", headers=headers, json=new_config)
    assert r.status_code == 200
    assert r.json()["config"]["position"]["x"] == 5

    r = client.get(f"/api/jobs/{job_id}", headers=headers)
    assert r.json()["config"]["page_selection"]["mode"] == "first_only"


def test_list_and_title_and_delete(client, token):
    job_id, headers = create_uploaded_job(client, token)

    r = client.get("/api/jobs", headers=headers)
    assert r.status_code == 200
    assert r.json()["total"] == 1

    r = client.put(f"/api/jobs/{job_id}/title", headers=headers, json={"title": "Meu Job"})
    assert r.status_code == 200

    r = client.get("/api/jobs", headers=headers)
    assert r.json()["items"][0]["title"] == "Meu Job"

    r = client.delete(f"/api/jobs/{job_id}", headers=headers)
    assert r.status_code == 204

    r = client.get("/api/jobs", headers=headers)
    assert r.json()["total"] == 0


def test_job_title_derived_from_first_pdf(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    make_logo()
    make_pdf()
    config = json.dumps(
        {
            "page_selection": {"mode": "all"},
            "position": {"x": 100, "y": 50, "width": 120, "height": 40},
        }
    )
    with open("test_logo.png", "rb") as logo:
        files = {
            "logo": ("logo.png", logo, "image/png"),
            "config": (None, config, "application/json"),
        }
        r = client.post("/api/jobs", headers=headers, files=files)
    assert r.status_code == 201
    job_id = r.json()["id"]

    with open("test_sample.pdf", "rb") as pdf:
        r = client.post(
            f"/api/jobs/{job_id}/files",
            headers=headers,
            files=[
                ("files", ("relatorio.pdf", pdf, "application/pdf")),
                ("files", ("anexo.pdf", pdf, "application/pdf")),
            ],
        )
    assert r.status_code == 204

    r = client.get("/api/jobs", headers=headers)
    item = next(i for i in r.json()["items"] if i["id"] == job_id)
    assert item["title"] == "relatorio"
    assert item["display_label"] == "relatorio"


def _run_job_to_completion(client, token):
    headers = {"Authorization": f"Bearer {token}"}
    job_id, _ = create_uploaded_job(client, token)
    r = client.post(f"/api/jobs/{job_id}/process", headers=headers)
    assert r.status_code == 202
    for _ in range(40):
        r = client.get(f"/api/jobs/{job_id}/status", headers=headers)
        state = r.json()
        if state["status"] in ("done", "failed"):
            return job_id, headers, state
        time.sleep(0.05)
    raise AssertionError("job did not finish processing")


def test_done_job_keeps_only_zip(client, token):
    from app.services import storage as storage_module

    job_id, _, state = _run_job_to_completion(client, token)
    assert state["status"] == "done", state

    stored = dict(storage_module._mock_storage_dict)
    assert not any(k.startswith(f"jobs/{job_id}/sources/") for k in stored)
    assert not any(k.startswith(f"logos/{job_id}/") for k in stored)
    assert f"output/{job_id}/result.zip" in stored
    assert any(k.startswith("user-logos/") for k in stored)


def test_expired_job_auto_deleted_after_30_days(client, token):
    from datetime import datetime, timedelta, timezone

    from app.models import Job, JobStatus

    headers = {"Authorization": f"Bearer {token}"}
    job_id, _ = create_uploaded_job(client, token)

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.created_at = datetime.now(timezone.utc) - timedelta(days=31)
        job.status = JobStatus.done
        job.output_zip_key = f"output/{job_id}/result.zip"
        db.commit()
    finally:
        db.close()

    storage_module.upload_file(b"PK\x03\x04...", f"output/{job_id}/result.zip", content_type="application/zip")

    r = client.get("/api/jobs", headers=headers)
    assert r.json()["total"] == 0

    stored = dict(storage_module._mock_storage_dict)
    assert not any(k.startswith(f"output/{job_id}/") for k in stored)
    assert not any(k.startswith(f"jobs/{job_id}/") for k in stored)
    assert not any(k.startswith(f"logos/{job_id}/") for k in stored)

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        assert job.deleted_at is not None
        assert job.output_zip_key is None
    finally:
        db.close()


def test_stamp_first_rest_distinct_positions():
    import io

    import pymupdf

    from app.schemas import JobConfig, PageSelection, Position
    from app.services.pdf_processor import stamp_logo_on_pdf

    # small red logo 100x50
    logo = pymupdf.open()
    lp = logo.new_page(width=100, height=50)
    lp.draw_rect(pymupdf.Rect(0, 0, 100, 50), color=(1, 0, 0), fill=(1, 0, 0))
    logo_bytes = lp.get_pixmap().tobytes("png")

    # 3-page pdf
    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=595, height=842)
    pdf_bytes = pdf.tobytes()

    config = JobConfig(
        page_selection=PageSelection(mode="all"),
        position=Position(x=60, y=60, width=100, height=50),
        position_rest=Position(x=300, y=300, width=80, height=40),
        position_mode="first_rest",
    )
    out = stamp_logo_on_pdf(pdf_bytes, logo_bytes, config)
    doc = pymupdf.open(stream=out, filetype="pdf")

    def red_bbox(page_index):
        pix = doc[page_index].get_pixmap(dpi=96)
        s, stride, n, w_, h_ = pix.samples, pix.stride, pix.n, pix.width, pix.height
        minx = maxx = miny = maxy = None
        for y in range(h_):
            row = s[y * stride : (y + 1) * stride]
            for x in range(w_):
                r_, g_, b_ = row[x * n], row[x * n + 1], row[x * n + 2]
                if r_ > 200 and g_ < 100 and b_ < 100:
                    minx = x if minx is None else min(minx, x)
                    maxx = x if maxx is None else max(maxx, x)
                    miny = y if miny is None else min(miny, y)
                    maxy = y if maxy is None else max(maxy, y)
        return minx, maxx, miny, maxy

    first = red_bbox(0)   # ~x 80-213, y 80-146
    rest = red_bbox(1)    # ~x 400-506, y 400-453

    # first page uses the primary position at (60,60)
    assert first is not None
    assert first[0] == 80 and abs(first[1] - 213) <= 1
    assert first[2] == 80 and abs(first[3] - 146) <= 1

    # other pages use position_rest at (300,300)
    assert rest is not None
    assert rest[0] == 400 and abs(rest[1] - 506) <= 1
    assert rest[2] == 400 and abs(rest[3] - 453) <= 1
    assert red_bbox(2) == rest


def test_stamp_first_rest_ignored_for_non_all_modes():
    import pymupdf

    from app.schemas import JobConfig, PageSelection, Position
    from app.services.pdf_processor import stamp_logo_on_pdf

    logo = pymupdf.open()
    lp = logo.new_page(width=100, height=50)
    lp.draw_rect(pymupdf.Rect(0, 0, 100, 50), color=(1, 0, 0), fill=(1, 0, 0))
    logo_bytes = lp.get_pixmap().tobytes("png")

    pdf = pymupdf.open()
    for _ in range(3):
        pdf.new_page(width=595, height=842)
    pdf_bytes = pdf.tobytes()

    # non-"all" selection with position_mode=first_rest: must use `position`
    # on every target page, ignoring position_rest.
    config = JobConfig(
        page_selection=PageSelection(mode="specific", specific_pages=[1, 2]),
        position=Position(x=60, y=60, width=100, height=50),
        position_rest=Position(x=300, y=300, width=80, height=40),
        position_mode="first_rest",
    )
    out = stamp_logo_on_pdf(pdf_bytes, logo_bytes, config)
    doc = pymupdf.open(stream=out, filetype="pdf")

    def red_bbox(page_index):
        pix = doc[page_index].get_pixmap(dpi=96)
        s, stride, n, w_, h_ = pix.samples, pix.stride, pix.n, pix.width, pix.height
        minx = maxx = None
        for y in range(h_):
            row = s[y * stride : (y + 1) * stride]
            for x in range(w_):
                r_, g_, b_ = row[x * n], row[x * n + 1], row[x * n + 2]
                if r_ > 200 and g_ < 100 and b_ < 100:
                    minx = x if minx is None else min(minx, x)
                    maxx = x if maxx is None else max(maxx, x)
        return minx, maxx

    # both target pages (1 and 2) must use the primary position (x~80-213),
    # NOT the rest position (400-506).
    for i in (1, 2):
        bbox = red_bbox(i)
        assert bbox is not None
        assert bbox[0] == 80 and abs(bbox[1] - 213) <= 1


def test_sanitize_filename_blocks_traversal():
    from app.utils.sanitize import sanitize_filename

    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("..\\..\\x") == "x"
    assert sanitize_filename("no" * 100) == "no" * 50
    assert sanitize_filename("") == "file"
    assert sanitize_filename("arquivo.txt", keep_unicode=True) == "arquivo.txt"
    assert sanitize_filename("a\u0000b.txt") == "ab.txt"


def test_storage_local_path_rejects_traversal():
    import os as _os

    from app.services.storage import LOCAL_STORAGE_DIR, _local_path

    import pytest as _pytest

    # A key escaping the storage root must raise instead of writing elsewhere.
    with _pytest.raises(ValueError):
        _local_path("../../outside.txt")
    with _pytest.raises(ValueError):
        _local_path("..\\outside.txt")
    with _pytest.raises(ValueError):
        _local_path("jobs/../../outside.txt")

    # Normal keys resolve inside the storage dir.
    safe = _local_path("jobs/abc/sources/file.pdf")
    assert safe.startswith(_os.path.abspath(LOCAL_STORAGE_DIR))


def make_logo_bytes(width=200, height=200):
    from io import BytesIO as _BytesIO

    img = Image.new("RGBA", (width, height), (255, 0, 0, 255))
    buf = _BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def test_image_exceeding_dimensions_rejected():
    from io import BytesIO as _BytesIO
    from PIL import Image as _PILImage

    import pytest as _pytest

    from app.utils.image_utils import get_image_dimensions

    huge = _PILImage.new("RGB", (8193, 100))
    buf = _BytesIO()
    huge.save(buf, format="PNG")

    with _pytest.raises(ValueError):
        get_image_dimensions(buf.getvalue())


def test_image_exceeding_pixel_budget_rejected(monkeypatch):
    import pytest as _pytest

    from app.utils.image_utils import create_logo_thumbnail

    # Force a tiny pixel budget so the 200x200 test image trips Pillow's guard.
    monkeypatch.setattr(
        "app.utils.image_utils.Image.MAX_IMAGE_PIXELS",
        1000,
    )
    with _pytest.raises(Exception):
        create_logo_thumbnail(make_logo_bytes(200, 200))


def test_pdf_page_count_limit(monkeypatch):
    import pytest as _pytest

    from app.config import settings

    from app.services.pdf_processor import get_pdf_page_count

    monkeypatch.setattr(settings, "MAX_PDF_PAGES", 3)
    with _pytest.raises(ValueError):
        get_pdf_page_count(make_pdf_bytes(pages=4))


def test_pdf_page_size_limit(monkeypatch):
    import pytest as _pytest

    from io import BytesIO as _BytesIO
    import pymupdf as _pymupdf

    from app.config import settings

    from app.services.pdf_processor import get_pdf_page_count

    monkeypatch.setattr(settings, "MAX_PAGE_SIZE_POINTS", 1000)
    doc = _pymupdf.open()
    doc.new_page(width=2000, height=50)
    buf = _BytesIO()
    buf.write(doc.tobytes())
    doc.close()
    with _pytest.raises(ValueError):
        get_pdf_page_count(buf.getvalue())


def test_render_dpi_is_clamped():
    from app.services.pdf_processor import _clamp_dpi

    assert _clamp_dpi(300) == 96
    assert _clamp_dpi(72) == 72


def make_pdf_bytes(pages=5):
    import pymupdf

    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    blob = doc.tobytes()
    doc.close()
    return blob


def test_svg_external_resources_blocked():
    import base64 as _b64

    import pytest as _pytest

    from app.utils.image_utils import blocking_svg_url_fetcher

    # HTTP(S)/file references must be rejected (SSRF / local read protection).
    for bad in ("http://evil.example/x.png", "https://evil.example/x.png", "file:///etc/passwd"):
        with _pytest.raises(ValueError):
            blocking_svg_url_fetcher(bad)

    # Embedded data: URLs are allowed.
    png_bits = _b64.b64encode(b"pngbytes").decode()
    data = blocking_svg_url_fetcher(f"data:image/png;base64,{png_bits}")
    assert data == b"pngbytes"


def test_svg_conversion_and_remote_image_rejection():
    import pytest as _pytest

    from app.utils.image_utils import convert_to_png

    # Rendering needs the system libcairo (installed in Docker/CI images).
    try:
        import cairocffi

        cairocffi.cairo  # noqa: B018 - forces dlopen of libcairo
    except Exception:
        _pytest.skip("libcairo not available on this host")

    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>'
    out = convert_to_png(svg, "logo.svg")
    assert out[:8] == b"\x89PNG\r\n\x1a\n"

    evil_svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><image href="http://evil.example/x.png"/></svg>'
    with _pytest.raises(Exception):
        convert_to_png(evil_svg, "logo.svg")
