"""Entity resolution — deduplicate and canonicalize entity mentions."""

from __future__ import annotations

import json
from collections import Counter, defaultdict

import streamlit as st


class EntityResolver:
    """Resolve free-text entity mentions to canonical forms.

    Uses a 3-pass algorithm:
      1. Exact case-insensitive grouping
      2. Substring containment (same label, ≥2 shared tokens)
      3. Jaccard token overlap (same label, similarity > 0.6, ≥2 shared tokens)
    """

    def __init__(self, entities: list[dict]) -> None:
        self._entities = entities
        self._canonical_map: dict[str, str] = {}
        self._resolved = False

    def resolve(self) -> dict[str, str]:
        """Return a mapping from every raw entity text to its canonical form."""
        if self._resolved:
            return self._canonical_map

        # Collect all unique (text, label) pairs with frequency
        text_label_count: Counter[tuple[str, str]] = Counter()
        for e in self._entities:
            text = e.get("text", "").strip()
            label = e.get("label", "")
            if text:
                text_label_count[(text, label)] += 1

        # Group by label for passes 2 and 3
        by_label: dict[str, list[str]] = defaultdict(list)
        for (text, label), _count in text_label_count.items():
            by_label[label].append(text)

        # Union-Find for merging
        parent: dict[str, str] = {}
        for (text, _label) in text_label_count:
            parent[text] = text

        def find(x: str) -> str:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: str, b: str) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        # --- Pass 1: Exact case-insensitive ---
        lower_groups: dict[str, list[str]] = defaultdict(list)
        for (text, _label) in text_label_count:
            lower_groups[text.strip().lower()].append(text)
        for _key, variants in lower_groups.items():
            if len(variants) > 1:
                for v in variants[1:]:
                    union(variants[0], v)

        # --- Pass 2: Substring containment (same label, ≥2 shared tokens) ---
        for label, texts in by_label.items():
            sorted_texts = sorted(texts, key=len)
            for i, shorter in enumerate(sorted_texts):
                shorter_lower = shorter.lower()
                shorter_tokens = set(shorter_lower.split())
                for longer in sorted_texts[i + 1:]:
                    longer_lower = longer.lower()
                    if shorter_lower in longer_lower:
                        longer_tokens = set(longer_lower.split())
                        shared = shorter_tokens & longer_tokens
                        if len(shared) >= 2:
                            union(shorter, longer)

        # --- Pass 3: Jaccard token overlap (same label, >0.6, ≥2 shared) ---
        for label, texts in by_label.items():
            for i, a in enumerate(texts):
                a_tokens = set(a.lower().split())
                if not a_tokens:
                    continue
                for b in texts[i + 1:]:
                    b_tokens = set(b.lower().split())
                    if not b_tokens:
                        continue
                    shared = a_tokens & b_tokens
                    if len(shared) < 2:
                        continue
                    jaccard = len(shared) / len(a_tokens | b_tokens)
                    if jaccard > 0.6:
                        union(a, b)

        # Build canonical map: pick the most frequent form in each group
        groups: dict[str, list[str]] = defaultdict(list)
        for (text, _label) in text_label_count:
            groups[find(text)].append(text)

        for _root, members in groups.items():
            canonical = max(members, key=lambda t: sum(
                c for (tx, _l), c in text_label_count.items() if tx == t
            ))
            for m in members:
                self._canonical_map[m] = canonical

        self._resolved = True
        return self._canonical_map

    def get_canonical(self, text: str) -> str:
        """Look up the canonical form for a given entity text."""
        if not self._resolved:
            self.resolve()
        return self._canonical_map.get(text, text)

    def get_aliases(self, canonical: str) -> list[str]:
        """Return all raw forms that map to this canonical."""
        if not self._resolved:
            self.resolve()
        return [k for k, v in self._canonical_map.items() if v == canonical]

    def get_entity_groups(self) -> list[dict]:
        """Return entity groups sorted by total count descending.

        Each group: ``{canonical: str, label: str, aliases: list[str], count: int}``
        """
        if not self._resolved:
            self.resolve()

        # Count per entity text
        text_count: Counter[str] = Counter()
        text_label: dict[str, str] = {}
        for e in self._entities:
            text = e.get("text", "").strip()
            label = e.get("label", "")
            if text:
                text_count[text] += 1
                text_label[text] = label

        # Aggregate by canonical
        canon_groups: dict[str, dict] = {}
        for raw, canonical in self._canonical_map.items():
            if canonical not in canon_groups:
                canon_groups[canonical] = {
                    "canonical": canonical,
                    "label": text_label.get(canonical, ""),
                    "aliases": [],
                    "count": 0,
                }
            if raw != canonical:
                canon_groups[canonical]["aliases"].append(raw)
            canon_groups[canonical]["count"] += text_count.get(raw, 0)

        return sorted(canon_groups.values(), key=lambda g: g["count"], reverse=True)


@st.cache_data(ttl=600)
def resolve_entities(entities_json: str) -> dict[str, str]:
    """Cached wrapper. Takes JSON string of entities list, returns canonical mapping."""
    entities = json.loads(entities_json)
    resolver = EntityResolver(entities)
    return resolver.resolve()
