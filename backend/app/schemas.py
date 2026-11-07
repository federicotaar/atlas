import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=256)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str
    created_at: datetime


class TokenOut(BaseModel):
    token: str
    user: UserOut


class SourceIn(BaseModel):
    kind: str = Field(min_length=1, max_length=50)
    label: str = Field(min_length=1, max_length=200)


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    label: str
    created_at: datetime


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    status: str = "active"
    start_date: date | None = None
    target_date: date | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    name: str
    description: str
    status: str
    start_date: date | None
    target_date: date | None
    created_at: datetime


class EventIn(BaseModel):
    type: str = Field(min_length=1, max_length=100)
    payload: dict = {}
    project_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    event_at: datetime


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    type: str
    payload: dict
    project_id: uuid.UUID | None
    source_id: uuid.UUID | None
    event_at: datetime
    recorded_at: datetime


class MemoryIn(BaseModel):
    content: str = Field(min_length=1)
    kind: str = "fact"
    origin: str = "stated"
    source_id: uuid.UUID
    event_at: datetime | None = None
    confidence: float = 0.9
    supersedes: uuid.UUID | None = None


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    content: str
    kind: str
    origin: str
    source_id: uuid.UUID
    event_at: datetime | None
    recorded_at: datetime
    confidence: float
    superseded_by: uuid.UUID | None
    visibility: str


class MemoryCreatedOut(BaseModel):
    memory: MemoryOut
    possible_duplicates: list[MemoryOut] = []


class SearchIn(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=8, ge=1, le=50)


class SearchHitOut(BaseModel):
    memory: MemoryOut
    score: float
    relevance: float
    semantic: float
    text_score: float


class CitationOut(BaseModel):
    ref: str  # M1, M2, ...
    memory_id: uuid.UUID
    content: str
    kind: str
    origin: str
    confidence: float
    recorded_at: datetime
    source: SourceOut | None


class AskIn(BaseModel):
    text: str = Field(min_length=1)


class AskOut(BaseModel):
    classification: str
    answer: str
    citations: list[CitationOut] = []
    saved_memory: MemoryOut | None = None
    model: str | None = None


class ExportOut(BaseModel):
    user: UserOut
    sources: list[SourceOut]
    projects: list[ProjectOut]
    events: list[EventOut]
    memories: list[MemoryOut]
