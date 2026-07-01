from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class AuthReq(BaseModel):
    token: str


class JobReq(BaseModel):
    url: str
    quality: str = "best"
    subs: bool = False
    thumb: bool = False
    infojson: bool = False
    incognito: bool = False
    incognito_dir: str | None = None


class ChannelReq(BaseModel):
    url: str
    quality: str = "best"
    interval_minutes: int = 60


class Job(BaseModel):
    id: str
    status: str = "queued"
    percent: float = 0.0
    speed: str = ""
    eta: str = ""
    note: str = ""
    filename: str = ""
    error: str = ""
    filepath: str = ""
    mime: str = ""
    created: float = 0.0
    finished: float = 0.0
    workdir: str = ""
    downloaded: int = 0
    total: int = 0
    title: str = ""
    incognito: bool = False


class BatchReq(BaseModel):
    urls: list[str]
    quality: str = "best"


class SettingDef(BaseModel):
    """Metadata completa de un setting para exponer en la API."""
    key: str
    type: str
    scope: str
    value: Any
    default: Any
    origin: str
    locked: bool
    restart_required: bool = False
    description: str = ""
    placeholder: str = ""
    options: list[dict[str, str]] | None = None
    tokens: list[str] | None = None
    validation: dict[str, Any] | None = None


class SettingUpdate(BaseModel):
    """Request para PATCH /api/settings: body dict {key: value}."""
    pass
