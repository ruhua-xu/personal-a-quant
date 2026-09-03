"""Structured risk findings; concrete risk engines live outside the domain model."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from .enums import RiskSeverity


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RiskViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: NonEmptyText
    severity: RiskSeverity
    message: NonEmptyText
    field: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("field")
    @classmethod
    def normalize_field(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None
