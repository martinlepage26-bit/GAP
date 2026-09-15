"""Pydantic models for structured LLM extraction with schema validation.

These models define the canonical output shape for every LLM extraction call.
They serve as both the prompt contract (via Field descriptions) and the
runtime validation layer — invalid extractions are caught before they enter
the database.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, ValidationError
from typing import List, Optional, Literal, Any


# ─── Enumerations ──────────────────────────────────────────────────────────

GapCategory = Literal[
    "feature_gap",
    "price_gap",
    "segment_gap",
    "geography_language_gap",
    "quality_gap",
    "other",
]

PainSignalType = Literal[
    "time_loss",
    "money_loss",
    "risk",
    "stress",
    "compliance",
    "quality_failure",
    "workaround",
]

# ─── Core Models ───────────────────────────────────────────────────────────


class PainSignal(BaseModel):
    """A single piece of evidence indicating pain or cost."""

    type: PainSignalType = Field(
        ...,
        description="The kind of pain evidenced: time_loss, money_loss, risk, "
        "stress, compliance, quality_failure, or workaround",
    )
    evidence: str = Field(
        ...,
        min_length=3,
        description="Direct quote or paraphrased evidence from the source text",
    )


class Extraction(BaseModel):
    """A single atomic unmet-need extraction from a review or discussion."""

    complaint_text: str = Field(
        ...,
        min_length=8,
        description="Atomic complaint rewritten in canonical form. "
        "One issue per extraction — do not combine multiple complaints.",
    )
    suggestion_text: Optional[str] = Field(
        default=None,
        description="Explicit or inferred improvement request from the user",
    )
    category: GapCategory = Field(
        ...,
        description="Gap classification: feature_gap, price_gap, segment_gap, "
        "geography_language_gap, quality_gap, or other",
    )
    segment: Optional[str] = Field(
        default=None,
        description="The affected buyer segment, e.g. 'freelance bookkeepers', "
        "'enterprise compliance teams'",
    )
    workflow: Optional[str] = Field(
        default=None,
        description="The workflow or job-to-be-done context, e.g. 'monthly close', "
        "'invoice reconciliation'",
    )
    severity: int = Field(
        ...,
        ge=1,
        le=5,
        description="Severity from 1 (preference) to 5 (revenue loss / compliance risk)",
    )
    has_workaround: bool = Field(
        ...,
        description="Whether the user describes a manual workaround (spreadsheets, "
        "scripts, copy/paste, third-party add-ons)",
    )
    willingness_to_pay: int = Field(
        ...,
        ge=0,
        le=3,
        description="0=none, 1=weak desire, 2=moderate (current paid workaround), "
        "3=explicit 'I would pay/switch'",
    )
    incumbent_named: Optional[str] = Field(
        default=None,
        description="Name of the competing product mentioned, if any",
    )
    pain_signals: List[PainSignal] = Field(
        default_factory=list,
        description="Structured pain evidence extracted from the text",
    )
    source_quote: str = Field(
        ...,
        min_length=5,
        description="Verbatim short quote from the original text supporting this extraction",
    )


class ExtractionResult(BaseModel):
    """Wrapper for the LLM response containing one or more extractions."""

    items: List[Extraction] = Field(default_factory=list)


# ─── Scoring Models ────────────────────────────────────────────────────────


class GapScoreInput(BaseModel):
    """Input components for the transparent weighted gap score."""

    frequency_score: float = Field(ge=0.0, le=1.0)
    intensity_score: float = Field(ge=0.0, le=1.0)
    breadth_score: float = Field(ge=0.0, le=1.0)
    wtp_score: float = Field(ge=0.0, le=1.0)
    workaround_score: float = Field(ge=0.0, le=1.0)
    segment_score: float = Field(ge=0.0, le=1.0)
    feasibility_score: float = Field(ge=0.0, le=1.0)
    defensibility_score: float = Field(ge=0.0, le=1.0)
    penalty_score: float = Field(ge=0.0, le=1.0, default=0.0)


class GapScoreOutput(BaseModel):
    """Result of the gap scoring formula with all sub-scores exposed."""

    total_score: float = Field(ge=0.0, le=1.0)
    components: GapScoreInput
    algorithm_version: str


# ─── Validation Helpers ────────────────────────────────────────────────────


def validate_extraction(payload: dict) -> tuple[Optional[Extraction], Optional[list[dict]]]:
    """Validate a single extraction payload against the schema.

    Returns (extraction, None) on success, or (None, errors) on failure.
    """
    try:
        extraction = Extraction(**payload)
        return extraction, None
    except ValidationError as e:
        return None, e.errors()


def validate_extraction_list(payload: dict) -> tuple[Optional[ExtractionResult], Optional[list[dict]]]:
    """Validate a full LLM response containing an items list.

    Returns (result, None) on success, or (None, errors) on failure.
    """
    try:
        result = ExtractionResult(**payload)
        return result, None
    except ValidationError as e:
        return None, e.errors()
