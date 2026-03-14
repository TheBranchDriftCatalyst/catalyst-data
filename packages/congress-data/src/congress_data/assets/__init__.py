from congress_data.assets.assertions import congress_assertions
from congress_data.assets.chunks import congress_chunks
from congress_data.assets.documents import congress_documents
from congress_data.assets.embeddings import congress_embeddings
from congress_data.assets.entities_ner import congress_entities
from congress_data.assets.entity_candidates import congress_entity_candidates
from congress_data.assets.extraction import congress_bills, congress_committees, congress_members
from congress_data.assets.graph import congress_graph
from congress_data.assets.mentions import congress_mentions
from congress_data.assets.propositions import congress_propositions

__all__ = [
    "congress_bills",
    "congress_members",
    "congress_committees",
    "congress_documents",
    "congress_chunks",
    # Legacy (backward compat)
    "congress_entities",
    "congress_propositions",
    # EDC gold layer
    "congress_mentions",
    "congress_entity_candidates",
    "congress_assertions",
    # Unchanged
    "congress_embeddings",
    "congress_graph",
]
