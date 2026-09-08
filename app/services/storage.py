import os
import tempfile
from urllib.parse import quote

import boto3
from botocore.config import Config

from app.config import settings

LOCAL_STORAGE_DIR = (
    settings.LOCAL_STORAGE_DIR
    or os.path.join(tempfile.gettempdir(), "logofier_storage")
)


def _r2_configured() -> bool:
    return bool(
        settings.R2_ACCOUNT_ID
        and settings.R2_ACCESS_KEY_ID
        and settings.R2_SECRET_ACCESS_KEY
    )


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def _local_path(key: str) -> str:
    base = os.path.abspath(LOCAL_STORAGE_DIR)
    full = os.path.abspath(os.path.join(base, *(key.split("/"))))
    if os.path.commonpath([base, full]) != base:
        raise ValueError(f"Invalid storage key: {key}")
    return full


def upload_file(file_bytes: bytes, key: str, content_type: str = "application/octet-stream") -> str:
    if not _r2_configured():
        path = _local_path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(file_bytes)
        return key

    client = _get_client()
    client.put_object(
        Bucket=settings.R2_BUCKET_NAME,
        Key=key,
        Body=file_bytes,
        ContentType=content_type,
    )
    return key


def download_file(key: str) -> bytes:
    if not _r2_configured():
        path = _local_path(key)
        with open(path, "rb") as f:
            return f.read()

    client = _get_client()
    response = client.get_object(Bucket=settings.R2_BUCKET_NAME, Key=key)
    return response["Body"].read()


def delete_file(key: str) -> None:
    if not _r2_configured():
        path = _local_path(key)
        if os.path.exists(path):
            os.remove(path)
        return

    client = _get_client()
    client.delete_object(Bucket=settings.R2_BUCKET_NAME, Key=key)


def list_files(prefix: str = "") -> list[tuple[str, int]]:
    """Return (key, size_bytes) for every stored file, optionally under a prefix."""
    if not _r2_configured():
        base = (
            os.path.join(LOCAL_STORAGE_DIR, *(prefix.split("/")))
            if prefix
            else LOCAL_STORAGE_DIR
        )
        if not os.path.isdir(base):
            return []
        results: list[tuple[str, int]] = []
        for dirpath, _dirs, fnames in os.walk(base):
            for fname in fnames:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, LOCAL_STORAGE_DIR).replace(os.sep, "/")
                results.append((rel, os.path.getsize(full)))
        return results

    client = _get_client()
    paginator = client.get_paginator("list_objects_v2")
    results = []
    for page in paginator.paginate(Bucket=settings.R2_BUCKET_NAME, Prefix=prefix):
        for obj in page.get("Contents", []):
            results.append((obj["Key"], obj["Size"]))
    return results


def get_presigned_url(key: str, expires_in: int = 3600) -> str:
    if not _r2_configured():
        path = _local_path(key)
        if os.name == "nt":
            url = f"file:///{path.replace(os.sep, '/')}"
        else:
            url = f"file://{path}"
        return url

    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.R2_BUCKET_NAME, "Key": key},
        ExpiresIn=expires_in,
    )
