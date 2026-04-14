"""Shared enums used across the KG pipeline."""

from enum import StrEnum


class MentionType(StrEnum):
    PERSON = "PERSON"
    ORG = "ORG"
    GPE = "GPE"
    LOC = "LOC"
    DATE = "DATE"
    LAW = "LAW"
    EVENT = "EVENT"
    MONEY = "MONEY"
    NORP = "NORP"
    FACILITY = "FACILITY"
    DOCUMENT = "DOCUMENT"
    BOOK = "BOOK"
    ROLE = "ROLE"
    # Geopolitical/strategic domain
    STRATEGIC_ASSET = "STRATEGIC_ASSET"  # chokepoints, pipelines, trade routes, military bases
    # Finance domain
    FINANCIAL_INSTRUMENT = "FINANCIAL_INSTRUMENT"  # stocks, bonds, derivatives, funds
    OTHER = "OTHER"


class AlignmentType(StrEnum):
    SAME_AS = "sameAs"
    POSSIBLE_SAME_AS = "possibleSameAs"
    RELATED_TO = "relatedTo"
    PART_OF = "partOf"


class ExtractionMethod(StrEnum):
    LLM = "llm"
    SPACY = "spacy"
    REGEX = "regex"
    MANUAL = "manual"
    STRUCTURED = "structured"
