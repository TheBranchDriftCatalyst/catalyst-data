"""Shared enums used across the KG pipeline."""

from enum import Enum


class MentionType(str, Enum):
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
    OTHER = "OTHER"


class AlignmentType(str, Enum):
    SAME_AS = "sameAs"
    POSSIBLE_SAME_AS = "possibleSameAs"
    RELATED_TO = "relatedTo"
    PART_OF = "partOf"


class ExtractionMethod(str, Enum):
    LLM = "llm"
    SPACY = "spacy"
    REGEX = "regex"
    MANUAL = "manual"
    STRUCTURED = "structured"
