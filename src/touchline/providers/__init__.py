"""Narrative providers.

Exactly two implementations exist: ``foundry`` (the production path, added
with the Microsoft Foundry client) and ``template`` (the deterministic
no-model path). The template provider is always importable and has no
third-party dependencies, so the pipeline can run with ``pydantic`` alone.
"""

from touchline.providers.base import (
    NarrativeProvider,
    ProviderError,
    ProviderName,
    ProviderUnavailable,
)
from touchline.providers.template import TemplateProvider

__all__ = [
    "NarrativeProvider",
    "ProviderError",
    "ProviderName",
    "ProviderUnavailable",
    "TemplateProvider",
]
