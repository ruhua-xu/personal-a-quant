"""Offline research contracts. Importing this package does not run research."""

from .models import (
    DateRange, EvidenceReference, FactorSnapshot, ResearchConfig, ResearchError,
    ResearchResult, ResearchRun, ResearchRunStatus, validate_research_inputs,
)
from .universe import UniverseDefinition, UniverseSnapshot

__all__ = [
    "DateRange", "EvidenceReference", "FactorSnapshot", "ResearchConfig",
    "ResearchError", "ResearchResult", "ResearchRun", "ResearchRunStatus",
    "UniverseDefinition", "UniverseSnapshot", "validate_research_inputs",
]
