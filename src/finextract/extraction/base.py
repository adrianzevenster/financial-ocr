from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from finextract.domain import DocumentType, ExtractionResult, PolicyConfig

ExtractorResult = ExtractionResult

EXTRACTOR_REGISTRY: dict[str, type["DocumentExtractor"]] = {}


def register_extractor(name: str):
    """Decorator to register an extractor class by name."""

    def decorator(cls: type[DocumentExtractor]) -> type[DocumentExtractor]:
        EXTRACTOR_REGISTRY[name] = cls
        return cls

    return decorator


class DocumentExtractor(ABC):
    """Provider-neutral interface for document entity extraction."""

    @property
    @abstractmethod
    def version(self) -> str:
        """Extractor implementation version string."""

    @property
    @abstractmethod
    def schema_version(self) -> str:
        """Schema version this extractor targets (e.g. 'invoice-v1')."""

    @abstractmethod
    def classify(self, text: str) -> tuple[DocumentType, float]:
        """Identify the document type and return a confidence score in [0, 1]."""

    @abstractmethod
    def extract(
        self,
        text: str,
        page_texts: list[str],
        policy: PolicyConfig,
    ) -> ExtractionResult:
        """Extract entities from document text according to the given policy.

        Implementations must:
        - Never mutate files or external state.
        - Return candidate values with evidence; leave normalized_value=None.
        - Return ExtractionResult even for partial extractions; missing fields
          appear absent from result.fields rather than raising.
        """
