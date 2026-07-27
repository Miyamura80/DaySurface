#!/usr/bin/env python3
"""Seed the e2e SQLite DB: create the full schema + one API key for Goose.

Run via ``uv run python seed.py`` from the repo root so the app's deps and
imports resolve. Reads ``BACKEND_DB_URI`` + ``E2E_USER_ID`` from the env (set by
lib.sh) and prints the raw API key as the last stdout line so up.sh can capture
it and inject it into Goose's extension headers.

Every ``db.models`` submodule is imported (not just the ``__init__`` re-exports)
so tables like ``google_tokens`` - which ``webhook_settings`` queries - are
registered on ``Base.metadata`` before ``create_all``.
"""

import importlib
import os
import pkgutil

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db.models
from api_server.auth.api_key_auth import create_api_key
from db.base import Base

# Register every table on Base.metadata (import all submodules, not just re-exports).
for _m in pkgutil.iter_modules(db.models.__path__):
    importlib.import_module(f"db.models.{_m.name}")  # noqa: TID251 - discover every ORM model so create_all builds the full schema

uri = os.environ["BACKEND_DB_URI"]
user_id = os.environ.get("E2E_USER_ID", "e2e-user")

engine = create_engine(uri)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

with Session() as s:
    raw_key, _row = create_api_key(s, user_id=user_id, name="goose-e2e", scopes=["*"])

print(raw_key)
