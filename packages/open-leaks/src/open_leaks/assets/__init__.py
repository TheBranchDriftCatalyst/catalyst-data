from open_leaks.assets.assertions import leak_assertions
from open_leaks.assets.chunks import leak_chunks
from open_leaks.assets.documents import leak_documents
from open_leaks.assets.embeddings import leak_embeddings
from open_leaks.assets.entities_ner import leak_entities
from open_leaks.assets.entity_candidates import leak_entity_candidates
from open_leaks.assets.extraction import (
    epstein_court_docs,
    icij_offshore_entities,
    icij_offshore_relationships,
    wikileaks_cables,
)
from open_leaks.assets.graph import leak_graph
from open_leaks.assets.mentions import leak_mentions
from open_leaks.assets.propositions import leak_propositions

__all__ = [
    "wikileaks_cables",
    "icij_offshore_entities",
    "icij_offshore_relationships",
    "epstein_court_docs",
    "leak_documents",
    "leak_chunks",
    # Legacy (backward compat)
    "leak_entities",
    "leak_propositions",
    # EDC gold layer
    "leak_mentions",
    "leak_entity_candidates",
    "leak_assertions",
    # Unchanged
    "leak_embeddings",
    "leak_graph",
]
