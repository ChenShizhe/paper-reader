"""Pydantic schema definitions for _catalog.yaml and _xref_index.yaml."""

from typing import List, Optional
from pydantic import BaseModel, Field


class PaperMetadata(BaseModel):
    cite_key: str
    title: str = ""
    authors: List[str] = Field(default_factory=list)
    year: Optional[int] = None
    journal: str = ""
    source_format: str = ""
    source_dir: str = ""
    bib_file: Optional[str] = None

    segmentation_version: int = 1
    translation_version: int = 0
    catalog_version: int = 1
    comprehension_pass: int = 0

    created_at: Optional[str] = None
    last_updated: Optional[str] = None

    vault_note: Optional[str] = None
    vault_tags: List[str] = Field(default_factory=list)
    related_papers: List[str] = Field(default_factory=list)

    xref_index: str = "_xref_index.yaml"
    notation_file: Optional[str] = None
    knowledge_gaps_file: Optional[str] = None


class SectionEntry(BaseModel):
    id: str
    heading: str
    section_type: str
    depth: int = 0
    segments: List[str] = Field(default_factory=list)
    comprehension_status: str = "pending"
    summary: Optional[str] = None
    key_terms: List[str] = Field(default_factory=list)
    notes: List[str] = Field(default_factory=list)
    children: List["SectionEntry"] = Field(default_factory=list)


SectionEntry.model_rebuild()


class SegmentEntry(BaseModel):
    id: str
    file: str
    section_id: str
    section_type: str
    token_estimate: int = 0
    has_equations: bool = False
    has_figures: bool = False
    has_tables: bool = False
    comprehension_status: str = "pending"
    translation_tool: Optional[str] = None
    source_pages: List[int] = Field(default_factory=list)
    source_lines: List[int] = Field(default_factory=list)


class CatalogSchema(BaseModel):
    paper: PaperMetadata
    sections: List[SectionEntry]
    segments: List[SegmentEntry]


# ── XRef index schema ──────────────────────────────────────────────────────────

class XrefEquation(BaseModel):
    label: str
    segment: str
    section: str
    description: str = ""


class XrefTheorem(BaseModel):
    label: str
    type: str
    segment: str
    section: str
    assumptions: List[str] = Field(default_factory=list)
    description: str = ""


class XrefFigure(BaseModel):
    label: str
    segment: str
    section: str
    caption: str = ""


class XrefCitation(BaseModel):
    cite_key: str
    first_mention_segment: str
    importance: str = "normal"
    dummy_note_created: bool = False
    vault_note: Optional[str] = None


class XrefIndexSchema(BaseModel):
    cite_key: str
    catalog_version: int = 1
    equations: List[XrefEquation] = Field(default_factory=list)
    theorems: List[XrefTheorem] = Field(default_factory=list)
    figures: List[XrefFigure] = Field(default_factory=list)
    citations: List[XrefCitation] = Field(default_factory=list)
