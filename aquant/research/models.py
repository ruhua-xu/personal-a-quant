"""Phase 3A research metadata only: no runner, prices, factors or persistence."""

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from fractions import Fraction
from typing import Annotated, Any, Literal, Self

from pydantic import BeforeValidator, Field, StrictBool, StringConstraints, field_validator, model_validator

from aquant.domain import InstrumentId, SecurityScore
from aquant.markets.china import ChinaEquityRuleBook

from .universe import (
    CnEtfs, Digest, JsonList, JsonObject, ResearchModel, SchemaVersion, Text,
    UniverseSnapshot, UtcDatetime, _cn_etfs, _ContentAddressed,
)


PositiveInt = Annotated[int, Field(strict=True, gt=0)]


def _date_only(value: Any) -> date:
    if type(value) is date:
        return value
    if type(value) is str:
        parsed = date.fromisoformat(value)
        if value == parsed.isoformat():
            return parsed
    raise ValueError("expected a date or YYYY-MM-DD, not datetime/timestamp")


CalendarDate = Annotated[date, BeforeValidator(_date_only)]


class DateRange(ResearchModel):
    start_date: CalendarDate
    end_date: CalendarDate

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.start_date > self.end_date:
            raise ValueError("start_date must not exceed end_date")
        return self


class EvidenceReference(ResearchModel):
    """Portable logical ID + digest, not a filesystem path or verified artifact."""

    reference_id: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")]
    content_hash: Digest
    scope: Text
    limitations: Text


FACTOR_NAMES = frozenset({"momentum_3m", "momentum_6m", "trend_score", "volatility"})


def _weights(value: Any) -> dict[str, str]:
    if type(value) is not dict or set(value) != FACTOR_NAMES:
        raise ValueError("factor_weights must contain exactly the four named factors")
    normalized = {}
    exact_sum = Fraction(0)
    for key, text in value.items():
        if type(text) is not str:
            raise ValueError("weights must be decimal strings, not floats/Decimal objects")
        try:
            number = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError("invalid decimal weight") from exc
        if not number.is_finite() or not 0 <= number <= 1:
            raise ValueError("weights must be finite and between zero and one")
        exact_sum += Fraction(number)  # Not rounded by the ambient Decimal context.
        normalized[key] = "0" if not number else format(number, "f").rstrip("0").rstrip(".")
    if exact_sum != 1:
        raise ValueError("factor weights must sum exactly to one")
    return normalized


class _ResearchConfigIdentity(ResearchModel):
    # Schema 1 consists of exactly these expanded fields, not Run audit metadata.
    schema_version: SchemaVersion = 1
    research_id: Text
    strategy_key: Text
    strategy_version: Text
    data_version: Digest
    universe_id: Text
    universe_version: Digest
    frequency: Literal["DAILY"] = "DAILY"
    adjustment: Literal["RAW"] = "RAW"
    price_field: Literal["close"] = "close"
    price_semantics: Literal["raw_price_return"] = "raw_price_return"
    momentum_3m_lookback: PositiveInt = 63
    momentum_6m_lookback: PositiveInt = 126
    trend_lookback: PositiveInt = 200
    volatility_lookback: Annotated[int, Field(strict=True, ge=2)] = 63
    annualization_sessions: PositiveInt = 252
    volatility_ddof: Annotated[int, Field(strict=True, ge=1, le=1)] = 1
    factor_weights: Annotated[dict[str, str], BeforeValidator(_weights)] = Field(
        default_factory=lambda: dict.fromkeys(sorted(FACTOR_NAMES), "0.25"), validate_default=True,
    )
    tie_policy: Literal["average"] = "average"
    score_contract_version: Literal["1"] = "1"
    score_frequency: Literal["DAILY"] = "DAILY"
    rebalance_frequency: Literal["WEEKLY"] = "WEEKLY"
    weekly_anchor: Literal["MONDAY"] = "MONDAY"
    top_n: PositiveInt
    cash_allowed: StrictBool = True
    calendar_policy: Literal["dataset_union_v1"] = "dataset_union_v1"
    calendar_instruments: CnEtfs
    as_of_policy: Literal["session_date_end_v1"] = "session_date_end_v1"
    eligibility_policy: Literal["strict_contiguous_v1"] = "strict_contiguous_v1"
    raw_review: EvidenceReference
    forward_horizons: tuple[PositiveInt, ...] = (5, 20)
    min_ic_observations: Annotated[int, Field(strict=True, ge=2)] = 5
    bucket_count: Annotated[int, Field(strict=True, ge=2)] = 5
    min_bucket_size: PositiveInt = 2
    primary_evaluation_sampling: Literal["weekly_decision_dates"] = "weekly_decision_dates"
    research_period: DateRange
    validation_period: DateRange
    test_period: DateRange
    hypothesis: Text
    parameter_comparisons: JsonList
    promotion_criteria: JsonObject

    @field_validator("parameter_comparisons")
    @classmethod
    def _comparisons(cls, value: list[Any]) -> list[Any]:
        if any(type(item) is not dict for item in value):
            raise ValueError("parameter_comparisons must be an explicit list of JSON objects")
        return value

    @field_validator("forward_horizons", mode="before")
    @classmethod
    def _horizons_input(cls, value: Any) -> Any:
        if not isinstance(value, (list, tuple)) or not value:
            raise ValueError("forward_horizons must be a nonempty list/tuple")
        return value

    @field_validator("forward_horizons")
    @classmethod
    def _horizons(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def _parameters(self) -> Self:
        if self.momentum_3m_lookback >= self.momentum_6m_lookback:
            raise ValueError("momentum_3m_lookback must be smaller than momentum_6m_lookback")
        if not (
            self.research_period.end_date < self.validation_period.start_date
            and self.validation_period.end_date < self.test_period.start_date
        ):
            raise ValueError("research/validation/test periods must be ordered and nonoverlapping")
        return self


class ResearchConfig(_ResearchConfigIdentity, _ContentAddressed):
    _identity_model = _ResearchConfigIdentity
    _hash_field = "parameters_hash"
    parameters_hash: Digest


def validate_research_inputs(config: ResearchConfig, universe: UniverseSnapshot) -> None:
    """Pure cross-object check; NOT an actual dataset/manifest verification.

    Conservative scoring start = first research date at 00:00 in RuleBook time.
    Retrospective membership is retained as declared, never upgraded to PIT.
    """
    config, universe = config.verify(), universe.verify()
    if (config.universe_id, config.universe_version) != (universe.universe_id, universe.version):
        raise ValueError("config universe_id/version must match UniverseSnapshot")
    if config.top_n > len(universe.instruments):
        raise ValueError("top_n exceeds the full universe size")
    members = {item.canonical_key: item for item in universe.instruments}
    if any(members.get(item.canonical_key) != item for item in config.calendar_instruments):
        raise ValueError("calendar_instruments must be a full-identity subset of the universe")
    boundary = datetime.combine(
        config.research_period.start_date, time.min, tzinfo=ChinaEquityRuleBook().timezone,
    )
    if universe.membership_basis == "prospective_frozen" and universe.known_at > boundary:
        raise ValueError("prospective known_at must not exceed scoring start (RuleBook midnight)")


class ResearchRunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ResearchError(ResearchModel):
    code: Text
    message: Text
    last_completed_stage: Text | None = None


class _ResearchReferences(ResearchModel):
    research_run_id: Text
    research_id: Text
    strategy_key: Text
    strategy_version: Text
    data_version: Digest
    universe_id: Text
    universe_version: Digest
    parameters_hash: Digest


class ResearchRun(_ResearchReferences):
    schema_version: SchemaVersion = 1
    git_commit: Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{40}$")]
    git_commit_source: Literal["caller_supplied"] = "caller_supplied"
    runtime_versions: dict[Text, Text] = Field(min_length=1)
    status: ResearchRunStatus
    created_at: UtcDatetime
    started_at: UtcDatetime | None = None
    completed_at: UtcDatetime | None = None
    error: ResearchError | None = None
    parent_run_id: Text | None = None
    replay_of: Text | None = None
    evaluation_stage: Literal["research", "validation", "test"] = "research"
    test_release_ref: EvidenceReference | None = None
    test_consumed: StrictBool = False

    @model_validator(mode="after")
    def _state(self) -> Self:
        if self.started_at is not None and self.started_at < self.created_at:
            raise ValueError("started_at precedes created_at")
        if self.completed_at is not None and self.completed_at < (self.started_at or self.created_at):
            raise ValueError("completed_at precedes start/creation")
        if self.status == ResearchRunStatus.CREATED:
            if self.started_at is not None or self.completed_at is not None:
                raise ValueError("CREATED cannot contain start/completion times")
        elif self.status == ResearchRunStatus.RUNNING:
            if self.started_at is None or self.completed_at is not None:
                raise ValueError("RUNNING requires start and no completion")
        elif self.status == ResearchRunStatus.COMPLETED:
            if self.started_at is None or self.completed_at is None:
                raise ValueError("COMPLETED requires start and completion")
        elif self.completed_at is None or self.error is None:
            raise ValueError("FAILED requires completion time and explicit error")
        if self.status != ResearchRunStatus.FAILED and self.error is not None:
            raise ValueError("only FAILED may carry an error")
        if self.evaluation_stage == "test" and self.test_release_ref is None:
            raise ValueError("test evaluation requires test_release_ref")
        if self.evaluation_stage == "test" and self.started_at is not None and not self.test_consumed:
            raise ValueError("started test evaluation must be marked consumed")
        return self


class FactorSnapshot(ResearchModel):
    """Minimal wrapper. Missing evidence stays None; no calculation methods."""

    schema_version: SchemaVersion = 1
    research_run_id: Text
    signal_date: CalendarDate
    as_of: UtcDatetime
    instrument_id: InstrumentId
    security_score: SecurityScore | None = None

    @field_validator("instrument_id", mode="before")
    @classmethod
    def _instrument(cls, value: Any) -> InstrumentId:
        # Reuse the same CN ETF identity boundary without duplicating market rules.
        return _cn_etfs([value])[0]

    @field_validator("security_score", mode="before")
    @classmethod
    def _score(cls, value: Any) -> SecurityScore | None:
        if value is None:
            return None
        if not isinstance(value, (SecurityScore, dict)):
            raise ValueError("security_score must be a SecurityScore or object")
        payload = value.model_dump(mode="python") if isinstance(value, SecurityScore) else dict(value)
        score = SecurityScore.model_validate(payload)
        if not score.score.is_finite() or not 0 <= score.score <= 1:
            raise ValueError("research SecurityScore must be finite and in [0, 1]")
        payload = score.model_dump(mode="python")
        # Use the research UTC boundary without changing the existing domain model.
        payload["as_of"] = score.as_of.astimezone(timezone.utc)
        return SecurityScore.model_validate(payload)

    @model_validator(mode="after")
    def _score_identity(self) -> Self:
        if self.as_of.astimezone(ChinaEquityRuleBook().timezone).date() != self.signal_date:
            raise ValueError("signal_date must match as_of in RuleBook timezone")
        if self.security_score is not None and (
            self.security_score.instrument_id != self.instrument_id
            or self.security_score.as_of != self.as_of
        ):
            raise ValueError("SecurityScore instrument/as_of must match its wrapper")
        return self


class ResearchResult(_ResearchReferences):
    """Schema placeholder, not a completed experiment or verified artifact bundle."""

    schema_version: SchemaVersion = 1
    adjustment: Literal["RAW"] = "RAW"
    price_field: Literal["close"] = "close"
    price_semantics: Literal["raw_price_return"] = "raw_price_return"
    artifact_references: tuple[EvidenceReference, ...] = ()
    coverage_summary: JsonObject | None = None
    quality_flags: tuple[Text, ...] = ()
    limitations: tuple[Text, ...] = Field(min_length=1)
    promotion_level: Annotated[int, Field(strict=True, ge=0, le=1)] = 0
    audit_checks: JsonObject | None = None
