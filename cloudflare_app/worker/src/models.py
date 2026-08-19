from typing import Literal

from pydantic import BaseModel, Field, field_validator


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

    @field_validator("input", mode="before")
    @classmethod
    def _wrap_single_string(cls, value: object) -> object:
        """Accept a question given as one string instead of a list of parts."""
        return [value] if isinstance(value, str) else value


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


class BulkFailItem(BaseModel):
    source_id: str
    row_hash: str = Field(min_length=64, max_length=64)
    notes: str = Field(min_length=1)


class BulkFailRequest(BaseModel):
    # Each item can produce up to three statements, so the cap keeps a batch
    # close in size to the import chunk that already runs in production.
    items: list[BulkFailItem] = Field(max_length=100)
