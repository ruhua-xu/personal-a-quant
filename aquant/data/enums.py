"""Provider-independent historical bar metadata."""

from enum import StrEnum


class BarFrequency(StrEnum):
    DAILY = "DAILY"


class AdjustmentMode(StrEnum):
    RAW = "RAW"
    FORWARD = "FORWARD"
    BACKWARD = "BACKWARD"
