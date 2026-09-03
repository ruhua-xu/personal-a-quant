"""Structured risk findings; concrete risk engines live outside the domain model."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .enums import RiskSeverity


class RiskViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    severity: RiskSeverity
    message: str
    field: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
