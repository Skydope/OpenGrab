from __future__ import annotations

from pydantic import BaseModel


class AuthReq(BaseModel):
    token: str


class JobReq(BaseModel):
    url: str
    quality: str = "best"


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
    workdir: str = ""
    downloaded: int = 0
    total: int = 0
    title: str = ""


class BatchReq(BaseModel):
    urls: list[str]
    quality: str = "best"
