"""
Shared return type for feature modules.
"""

from dataclasses import dataclass


@dataclass
class FeatureResult:
    ok: bool
    data: dict
    display: str
    spoken: str
    error: str | None = None
