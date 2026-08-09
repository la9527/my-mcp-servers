"""Enforce provider capabilities before a classification pipeline starts."""

from __future__ import annotations

from dataclasses import dataclass

from photos_mcp.domain.models.source import (
    PhotoProvider,
    SourceCapabilities,
    SourceCapability,
)


class SourcePolicyViolation(ValueError):
    def __init__(self, capability: SourceCapability, provider: PhotoProvider) -> None:
        self.capability = capability
        self.provider = provider
        super().__init__(f"{capability.value} is not allowed for {provider.value}")


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    provider: PhotoProvider
    capabilities: SourceCapabilities

    @classmethod
    def for_provider(cls, provider: PhotoProvider) -> "SourcePolicy":
        return cls(provider, SourceCapabilities.for_provider(provider))

    def require(self, capability: SourceCapability) -> None:
        if not self.capabilities.supports(capability):
            raise SourcePolicyViolation(capability, self.provider)

    def validate_analysis(
        self,
        *,
        face_quality: bool,
        face_clustering: bool,
    ) -> None:
        if face_quality:
            self.require(SourceCapability.FACE_QUALITY)
        if face_clustering:
            self.require(SourceCapability.FACE_CLUSTERING)

