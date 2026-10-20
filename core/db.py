from __future__ import annotations

from typing import Any

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from . import config


class Base(DeclarativeBase):
    pass


_ENGINE_CACHE: dict[str, Engine] = {}
_SESSION_FACTORY_CACHE: dict[str, sessionmaker[Session]] = {}


def get_engine(url: str | None = None) -> Engine:
    db_url = url or config.DATABASE_URL
    engine = _ENGINE_CACHE.get(db_url)
    if engine is None:
        engine = create_engine(db_url, pool_pre_ping=True)
        _ENGINE_CACHE[db_url] = engine
    return engine


def get_session(url: str | None = None) -> Session:
    db_url = url or config.DATABASE_URL
    factory = _SESSION_FACTORY_CACHE.get(db_url)
    if factory is None:
        factory = sessionmaker(bind=get_engine(db_url), expire_on_commit=False)
        _SESSION_FACTORY_CACHE[db_url] = factory
    return factory()


def init_db(url: str | None = None) -> None:
    from . import models as _models  # noqa: F401

    Base.metadata.create_all(bind=get_engine(url))
