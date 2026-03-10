"""Domain entities for open-leaks pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Cable(BaseModel):
    """A WikiLeaks diplomatic cable."""

    id: str = Field(description="Cable reference ID")
    date: str = Field(default="", description="Date of cable (ISO format)")
    subject: str = Field(default="", description="Cable subject line")
    origin: str = Field(default="", description="Originating embassy/post")
    classification: str = Field(default="", description="Classification level")
    content: str = Field(default="", description="Full cable text")
    tags: list[str] = Field(default_factory=list, description="TAGS metadata")
    source_url: str | None = Field(default=None, description="Source URL")


class OffshoreEntity(BaseModel):
    """An entity from ICIJ offshore leaks databases."""

    id: str = Field(description="ICIJ node ID")
    name: str = Field(default="", description="Entity name")
    entity_type: str = Field(default="", description="Type: Entity, Officer, Intermediary, Address")
    jurisdiction: str = Field(default="", description="Jurisdiction code")
    country: str = Field(default="", description="Country of origin")
    source_dataset: str = Field(default="", description="Dataset: panama, paradise, pandora, offshore")
    status: str = Field(default="", description="Entity status")
    incorporation_date: str = Field(default="", description="Date of incorporation")
    source_url: str | None = Field(default=None, description="ICIJ database URL")


class OffshoreRelationship(BaseModel):
    """A relationship between ICIJ offshore entities (edge data)."""

    id: str = Field(description="Relationship ID")
    source_id: str = Field(description="Source entity node ID")
    target_id: str = Field(description="Target entity node ID")
    rel_type: str = Field(default="", description="Relationship type (e.g. officer_of, intermediary_of)")
    source_dataset: str = Field(default="", description="Dataset: panama, paradise, pandora, offshore")
    start_date: str = Field(default="", description="Relationship start date")
    end_date: str = Field(default="", description="Relationship end date")


class CourtDocument(BaseModel):
    """A court document from the Epstein files."""

    id: str = Field(description="Document identifier")
    title: str = Field(default="", description="Document title or filename")
    case_number: str = Field(default="", description="Court case number")
    document_type: str = Field(default="", description="Type: deposition, motion, order, exhibit")
    date_filed: str = Field(default="", description="Date filed (ISO format)")
    content: str = Field(default="", description="Extracted text content")
    page_count: int = Field(default=0, description="Number of pages")
    source_url: str | None = Field(default=None, description="Source URL")
