import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.sqlite import JSON
from sqlalchemy.orm import relationship

from app.database import Base


class JobStatus(str, enum.Enum):
    pending = "pending"
    uploading = "uploading"
    processing = "processing"
    done = "done"
    failed = "failed"


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False, default="")
    role = Column(String(32), nullable=False, default="user")
    is_active = Column(Boolean, nullable=False, default=True)
    is_password_set = Column(Boolean, nullable=False, default=True)
    avatar_key = Column(String(500), nullable=True)
    avatar_thumb_key = Column(String(500), nullable=True)
    token_version = Column(Integer, nullable=False, default=1)
    failed_login_attempts = Column(Integer, nullable=False, default=0)
    locked_until = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime, nullable=True)

    jobs = relationship("Job", back_populates="user", cascade="all, delete-orphan")
    logos = relationship("UserLogo", back_populates="user", cascade="all, delete-orphan")
    presets = relationship("Preset", back_populates="user", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    status = Column(Enum(JobStatus), default=JobStatus.pending, nullable=False)
    mode = Column(String(32), nullable=False, default="multiple_pdfs")
    logo_key = Column(String(500), nullable=False)
    logo_keys = Column(JSON, nullable=True, default=list)
    logo_names = Column(JSON, nullable=True, default=list)
    config = Column(JSON, nullable=False)
    source_keys = Column(JSON, nullable=True, default=list)
    source_names = Column(JSON, nullable=True, default=list)
    title = Column(String(255), nullable=True)
    total_files = Column(Integer, default=0)
    processed_files = Column(Integer, default=0)
    output_zip_key = Column(String(500), nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="jobs")


class UserLogo(Base):
    __tablename__ = "user_logos"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    storage_key = Column(String(500), nullable=False)
    thumb_key = Column(String(500), nullable=False)
    aspect_ratio = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="logos")


class Preset(Base):
    __tablename__ = "presets"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    mode = Column(String(32), nullable=False, default="multiple_pdfs")
    pos_strategy = Column(String(32), nullable=False, default="shared")
    page_selection = Column(JSON, nullable=True, default=dict)
    position = Column(JSON, nullable=True, default=dict)
    position_rest = Column(JSON, nullable=True, default=dict)
    position_mode = Column(String(32), nullable=False, default="single")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    deleted_at = Column(DateTime, nullable=True)

    user = relationship("User", back_populates="presets")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(String(64), primary_key=True)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    token_hash = Column(String(64), nullable=False, index=True)
    revoked_at = Column(DateTime, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    replaced_by = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
