"""SQLAlchemy persistence for the prototype's aggregate project state."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class ProjectState(Base):
    __tablename__ = "project_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


class ImportPreview(Base):
    __tablename__ = "import_previews"
    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    state_payload: Mapped[str] = mapped_column(Text, nullable=False)
    diff_payload: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)


def database_url() -> str:
    """Return configured URL, defaulting to the persistent /data database."""
    explicit = os.environ.get("DATABASE_URL")
    if explicit:
        return explicit
    data_dir = Path(os.environ.get("DATA_DIR", "/data")).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{(data_dir / 'app.db').as_posix()}"


def make_engine(url: str | None = None):
    url = url or database_url()
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if url.startswith("sqlite:"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
    return create_engine(url, **kwargs)


def make_session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


def load_state(session: Session) -> dict[str, Any] | None:
    import json
    row = session.get(ProjectState, 1)
    return json.loads(row.payload) if row else None


def save_state(session: Session, state: dict[str, Any]) -> None:
    import json
    row = session.get(ProjectState, 1)
    payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    if row is None:
        session.add(ProjectState(id=1, payload=payload))
    else:
        row.payload = payload
        row.updated_at = datetime.now(timezone.utc)


def bootstrap_admin(session: Session, username: str | None, password: str | None) -> None:
    """Create the first administrator only from explicitly configured credentials."""
    if not username and not password:
        return
    if not username or not password or len(password) < 12:
        raise RuntimeError("Set both ADMIN_USERNAME and ADMIN_PASSWORD; ADMIN_PASSWORD must be at least 12 characters.")
    existing = session.scalar(select(User).where(User.username == username))
    if existing:
        return
    from pwdlib import PasswordHash
    session.add(User(username=username, password_hash=PasswordHash.recommended().hash(password), is_admin=True))

