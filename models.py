from __future__ import annotations

import asyncio

from pydantic import BaseModel, Field


class AuthReq(BaseModel):
    token: str


class JobReq(BaseModel):
    url: str
    quality: str = "best"


class Job(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

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
    workdir: str = ""
    downloaded: int = 0
    total: int = 0
    title: str = ""
    event: asyncio.Event = Field(default_factory=asyncio.Event, exclude=True)
