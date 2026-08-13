from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    password: str
    reviewer_name: str = ""


class LoginResponse(BaseModel):
    token: str
    role: Literal["reviewer", "admin"]
    name: str


class QuestionRecord(BaseModel):
    id: int
    instruction: str
    input: list[str]
    output: str


class ImportStart(BaseModel):
    filename: str
    file_hash: str = Field(min_length=64, max_length=64)


class ImportChunk(BaseModel):
    records: list[QuestionRecord] = Field(max_length=200)
    skipped_count: int = Field(default=0, ge=0)


class ReviewSubmission(BaseModel):
    question_id: int
    decision: Literal["Pass", "Fail"]
    notes: str = ""


class QuestionResponse(BaseModel):
    id: int
    source_id: str
    instruction: str
    input: list[str]
    output: str


class ResetRequest(BaseModel):
    review_id: int
