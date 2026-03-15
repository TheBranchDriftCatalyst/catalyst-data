# Feature Request: Geospatial Database & Spatial Indexing Infrastructure

## Summary

Add PostGIS, H3 hexagonal indexing, and a spatial data model as **optional infrastructure** for the catalyst-data platform. This enables spatially-aware data sources (AIS/ADS-B vessel tracking, geographic entity grounding, map visualization) and addresses a gap identified in the ONTOLOGY.md specification.

## Relationship to the Core Pipeline

The catalyst-data platform has two independent grounding layers:

1. **Linguistic/semantic/memetic layer** (implemented) — propositions, entity concordance, predicate normalization, cross-source alignment. This is the primary knowledge layer and operates entirely without spatial data. Most entities (people, organizations, laws, events, concepts) are grounded through text, context, and semantic structure — not geography.

2. **Spatial layer** (this feature) — an **enrichment layer** that adds geographic grounding where it's relevant. Not all entities have meaningful spatial representations, and the core pipeline should never require spatial data to function.

**Key principle:** Spatial grounding is additive, not mandatory. The `geo_id` fields proposed below are all nullable. The concordance engine's spatial signal (`s_geo`) is one of several signals and contributes zero when spatial data is absent — the system degrades gracefully, not catastrophically.

## Motivation

For entities and assertions that **do** have a spatial dimension — places, facilities, jurisdictions, vessel movements, event locations — the system currently has no way to represent or query that dimension:

- Location exists only as unstructured qualifier strings (e.g., `qualifiers["location"] = "Washington, D.C."`)
- No geometry storage, spatial indexing, or proximity queries
- No map visualization capability
- Place entities (GPE, LOC, FACILITY mentions) are extracted but not geocoded

Spatially-aware features — AIS/ADS-B tracking, geographic entity disambiguation, map visualization, jurisdiction detection — are blocked by this gap. But their absence does not impair the core linguistic pipeline.

## What This Provides

### 1. PostGIS on postgres-knowledge

Extend the existing `pgvector/pgvector:pg16` image to include PostGIS, enabling:

- **Geometry columns** — store POINT, LINESTRING, POLYGON, MULTIPOLYGON as native types
- **Spatial indexes** — GiST indexes for fast bounding-box and nearest-neighbor queries
- **Spatial functions** — `ST_Distance`, `ST_Contains`, `ST_Intersects`, `ST_Within`, `ST_Buffer`, `ST_Area`
- **Coordinate reference systems** — SRID 4326 (WGS84) as default, with reprojection support
- **Geography type** — for accurate great-circle distance calculations

### 2. H3 Hexagonal Indexing

[H3](https://h3geo.org/) partitions the globe into hierarchical hexagonal cells at 16 resolutions. This provides:

- **Multi-resolution spatial aggregation** — zoom in/out without recomputing
- **Uniform area cells** — unlike rectangular grids, hexagons have consistent neighbors and area
- **Fast adjacency** — `h3.k_ring()` for neighborhood queries in O(1)
- **Compact representation** — a single uint64 per cell
- **Natural fit for heatmaps** — aggregation by cell at any resolution

Resolution guide:

| Resolution | Avg Hex Area | Use Case |
|-----------|-------------|----------|
| 0 | 4,357,449 km² | Continental overview |
| 3 | 12,393 km² | Country-level aggregation |
| 5 | 253 km² | Metro area / regional |
| 7 | 5.16 km² | City-level |
| 9 | 0.105 km² | Neighborhood / port / airport |
| 11 | 0.002 km² | Building-level |

### 3. GeoObject Data Model

A first-class spatial entity model in dagster-io:

```python
@dataclass
class GeoObject:
    """Spatial entity with geometry, H3 covering, and administrative hierarchy."""
    geo_id: str
    geometry_wkt: str              # WKT representation (POINT, POLYGON, etc.)
    geometry_type: str             # "point", "polygon", "linestring", "multipolygon"
    centroid_lat: float
    centroid_lon: float
    h3_cells: dict[int, list[str]] # resolution → cell IDs (multi-res covering)
    admin_hierarchy: list[str]     # ["United States", "District of Columbia", "Washington"]
    spatial_type: str              # "city", "country", "port", "airport", "building", "region", "route"
    bounds: tuple[float, float, float, float] | None  # (min_lon, min_lat, max_lon, max_lat)
    source: str | None             # "osm", "geonames", "manual", "derived"
    external_ids: dict[str, str]   # {"osm_id": "...", "geonames_id": "...", "locode": "...", "icao": "..."}
```

### 4. Spatial Extensions to Existing Models

All fields are **nullable** — the vast majority of entities and assertions will have no spatial grounding, and that's expected. Only place-type entities (GPE, LOC, FACILITY) and spatially-relevant assertions benefit from these fields.

```python
# CanonicalEntity — optional spatial grounding (null for most entities)
class CanonicalEntity:
    ...
    geo_id: str | None             # FK to GeoObject (only for place entities)
    location_h3_res7: str | None   # primary H3 cell (only when geo-grounded)

# Assertion — optional validity geometry (null for most assertions)
class Assertion:
    ...
    validity_geo_id: str | None    # FK to GeoObject (only for spatially-scoped assertions)
    validity_h3_res7: str | None   # H3 cell (only when spatially-scoped)

# Mention — optional location grounding (null for non-place mentions)
class Mention:
    ...
    geo_id: str | None             # FK to GeoObject (only if mention is a place)
```

## Database Schema

### PostGIS Tables

```sql
-- Enable extensions
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS h3;           -- pgx h3 extension
CREATE EXTENSION IF NOT EXISTS h3_postgis;   -- h3 + postgis bridge

-- Spatial entities
CREATE TABLE geo_objects (
    geo_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    geometry geometry(Geometry, 4326) NOT NULL,
    geometry_type VARCHAR(20) NOT NULL,
    centroid geography(Point, 4326) GENERATED ALWAYS AS (ST_Centroid(geometry)::geography) STORED,
    h3_res3 h3index,
    h3_res5 h3index,
    h3_res7 h3index,
    h3_res9 h3index,
    admin_hierarchy TEXT[] DEFAULT '{}',
    spatial_type VARCHAR(50) NOT NULL,
    bounds BOX2D GENERATED ALWAYS AS (ST_Extent(geometry)) STORED,
    source VARCHAR(50),
    external_ids JSONB DEFAULT '{}',
    properties JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Spatial indexes
CREATE INDEX idx_geo_objects_geometry ON geo_objects USING GIST (geometry);
CREATE INDEX idx_geo_objects_centroid ON geo_objects USING GIST (centroid);
CREATE INDEX idx_geo_objects_h3_res7 ON geo_objects (h3_res7);
CREATE INDEX idx_geo_objects_h3_res5 ON geo_objects (h3_res5);
CREATE INDEX idx_geo_objects_spatial_type ON geo_objects (spatial_type);
CREATE INDEX idx_geo_objects_external_ids ON geo_objects USING GIN (external_ids);
CREATE INDEX idx_geo_objects_admin ON geo_objects USING GIN (admin_hierarchy);

-- Spatial relations between entities
CREATE TABLE spatial_relations (
    relation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    source_geo_id UUID NOT NULL REFERENCES geo_objects(geo_id),
    target_geo_id UUID NOT NULL REFERENCES geo_objects(geo_id),
    relation_type VARCHAR(30) NOT NULL,  -- intersects, within, contains, adjacent_to, near, overlaps
    distance_km REAL,
    confidence REAL DEFAULT 1.0,
    derived_method VARCHAR(50),          -- "postgis_computed", "h3_adjacency", "admin_hierarchy"
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_geo_id, target_geo_id, relation_type)
);

CREATE INDEX idx_spatial_relations_source ON spatial_relations(source_geo_id);
CREATE INDEX idx_spatial_relations_target ON spatial_relations(target_geo_id);
CREATE INDEX idx_spatial_relations_type ON spatial_relations(relation_type);

-- Link canonical entities to spatial objects
ALTER TABLE canonical_entities ADD COLUMN geo_id UUID REFERENCES geo_objects(geo_id);
ALTER TABLE canonical_entities ADD COLUMN location_h3_res7 h3index;
CREATE INDEX idx_canonical_entities_geo ON canonical_entities(geo_id);
CREATE INDEX idx_canonical_entities_h3 ON canonical_entities(location_h3_res7);

-- Link assertions to spatial validity
ALTER TABLE assertions ADD COLUMN validity_geo_id UUID REFERENCES geo_objects(geo_id);
ALTER TABLE assertions ADD COLUMN validity_h3_res7 h3index;
CREATE INDEX idx_assertions_geo ON assertions(validity_geo_id);
CREATE INDEX idx_assertions_h3 ON assertions(validity_h3_res7);

-- Temporal intervals for assertions (ONTOLOGY 7.5)
CREATE TABLE temporal_intervals (
    interval_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    assertion_id UUID REFERENCES assertions(assertion_id),
    entity_id UUID REFERENCES canonical_entities(canonical_id),
    start_date DATE,
    end_date DATE,
    precision VARCHAR(10),  -- "day", "month", "year", "decade"
    granularity VARCHAR(30) -- "point_in_time", "range", "recurring"
);

CREATE INDEX idx_temporal_start ON temporal_intervals(start_date);
CREATE INDEX idx_temporal_end ON temporal_intervals(end_date);
CREATE INDEX idx_temporal_assertion ON temporal_intervals(assertion_id);
CREATE INDEX idx_temporal_entity ON temporal_intervals(entity_id);
```

### Example Queries Enabled

```sql
-- Find all entities within 50km of a point
SELECT ce.canonical_name, ST_Distance(
    go.centroid, ST_MakePoint(-77.0369, 38.9072)::geography
) / 1000 AS distance_km
FROM canonical_entities ce
JOIN geo_objects go ON ce.geo_id = go.geo_id
WHERE ST_DWithin(go.centroid, ST_MakePoint(-77.0369, 38.9072)::geography, 50000);

-- Aggregate entity density by H3 res-5 cell
SELECT h3_res5, COUNT(*) as entity_count
FROM canonical_entities ce
JOIN geo_objects go ON ce.geo_id = go.geo_id
WHERE go.h3_res5 IS NOT NULL
GROUP BY h3_res5
ORDER BY entity_count DESC;

-- Find assertions about events in a specific jurisdiction
SELECT a.subject_text, a.predicate, a.object_text, a.confidence
FROM assertions a
JOIN geo_objects go ON a.validity_geo_id = go.geo_id
WHERE 'Panama' = ANY(go.admin_hierarchy)
  AND a.confidence > 0.7;

-- Spatial join: entities co-located at H3 res-7
SELECT a.canonical_name, b.canonical_name, a.location_h3_res7
FROM canonical_entities a
JOIN canonical_entities b ON a.location_h3_res7 = b.location_h3_res7
WHERE a.canonical_id < b.canonical_id;
```

## K8s Changes

### 1. Database Image

Replace or extend the current postgres-knowledge image to include PostGIS + H3:

```yaml
# k8s/platform/postgres-knowledge.yaml
# Current: pgvector/pgvector:pg16
# New: custom image or postgis/postgis:16-3.4 with pgvector + h3 added

containers:
  - name: postgres-knowledge
    image: postgis/postgis:16-3.4
    # PostGIS includes pg_trgm by default
    # Need to add: pgvector, h3-pg extensions via init script
    env:
      - name: POSTGRES_INITDB_ARGS
        value: "--data-checksums"
    volumeMounts:
      - name: init-extensions
        mountPath: /docker-entrypoint-initdb.d/
volumes:
  - name: init-extensions
    configMap:
      name: postgres-knowledge-init
```

Init script:

```sql
-- 00-extensions.sql
CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector (for embeddings)
CREATE EXTENSION IF NOT EXISTS pg_trgm;      -- trigram matching
-- H3 requires separate installation; use pgxn or compile
-- CREATE EXTENSION IF NOT EXISTS h3;
-- CREATE EXTENSION IF NOT EXISTS h3_postgis;
```

**Alternative:** Build a custom Docker image:

```dockerfile
FROM postgis/postgis:16-3.4
RUN apt-get update && apt-get install -y postgresql-16-pgvector
# H3 extension requires compilation from https://github.com/zachasme/h3-pg
```

### 2. Resource Adjustment

PostGIS + spatial indexes increase memory requirements:

```yaml
resources:
  requests:
    cpu: 200m
    memory: 512Mi   # was 256Mi
  limits:
    cpu: "1"
    memory: 1Gi     # was 512Mi
```

### 3. GraphDB Resource Update

Extend `packages/knowledge-graph/src/knowledge_graph/resources.py` to support spatial operations:

```python
class GraphDBResource:
    ...
    def upsert_geo_object(self, geo: GeoObject) -> None: ...
    def find_nearby(self, lat: float, lon: float, radius_km: float) -> list[dict]: ...
    def h3_aggregate(self, resolution: int, bounds: tuple | None = None) -> list[dict]: ...
    def spatial_join(self, geo_id: str, relation: str = "intersects") -> list[dict]: ...
```

## Concordance Integration

Spatial proximity becomes an **optional concordance signal** (ONTOLOGY Section 5.3). When both entities have geo-grounding, it contributes to the score. When either lacks it (the common case for non-place entities), it contributes zero and the existing lexical/embedding signals dominate:

```python
# concordance.py — add to CrossSourceAligner._score_pair()

def _geo_similarity(self, a: EntityCandidate, b: EntityCandidate) -> float:
    """Spatial concordance signal based on entity locations."""
    if not (a.geo_id and b.geo_id):
        return 0.0
    # Same H3 res-7 cell = strong signal
    if a.location_h3_res7 == b.location_h3_res7:
        return 0.85
    # Adjacent H3 cells = moderate signal
    if h3.are_neighbor_cells(a.location_h3_res7, b.location_h3_res7):
        return 0.60
    # Within 50km = weak signal
    dist = self._compute_distance(a, b)
    if dist and dist < 50:
        return max(0.3, 0.6 - dist / 100)
    return 0.0
```

This addresses the missing `s_geo` term in the concordance scoring function:

```
score(u, e_j) = α·s_name + β·s_context + γ·s_structure + δ·s_geo + ε·s_time
                                                          ^^^^^^^^
                                                          currently 0
```

## Toponym Resolution Pipeline

### Reference Data

Pre-load a gazetteer of commonly referenced places:

| Source | Coverage | Records | Use |
|--------|----------|---------|-----|
| [GeoNames](https://www.geonames.org/export/) | Global cities, admin regions | ~12M | Primary gazetteer |
| [Natural Earth](https://www.naturalearthdata.com/) | Country/state boundaries, coastlines | ~5K polygons | Admin hierarchies, boundaries |
| [World Port Index](https://msi.nga.mil/Publications/WPI) | Global ports | ~3,600 | Port geofencing (for AIS) |
| [OurAirports](https://ourairports.com/data/) | Global airports | ~75K | Airport geofencing (for ADS-B) |
| [OpenStreetMap Nominatim](https://nominatim.openstreetmap.org/) | Global geocoding | API | Fallback geocoder |

### Toponym Linking Asset

```python
@asset(group_name="spatial", description="Link place mentions to GeoObjects")
def toponym_resolution(
    mentions: list[Mention],        # GPE + LOC + FACILITY type mentions
    gazetteer: GazetteerResource,   # pre-loaded GeoNames + NaturalEarth
) -> list[dict]:
    """
    For each place mention:
    1. Candidate generation (fuzzy match against gazetteer)
    2. Disambiguation (context from surrounding entities, admin hierarchy)
    3. GeoObject creation (geometry + H3 covering)
    4. Link back to mention via geo_id
    """
```

## Visualization Enablement

With spatial infrastructure in place, the data-explorer gains:

### Map Page (new)

```python
# packages/data-explorer/src/data_explorer/streamlit/pages/11_Map_View.py
# Uses pydeck (deck.gl Python bindings) or folium

# Layer 1: H3 hexagon heatmap (entity density per cell)
# Layer 2: Entity markers (geo-grounded canonical entities)
# Layer 3: Assertion arcs (subject_location → object_location)
# Layer 4: Track lines (vessel/aircraft movements, if transport-tracking is active)
# Controls: resolution slider, entity type filter, time range filter
```

### Existing Pages Enhanced

- **Knowledge Graph** — optional map backdrop behind force layout for geo-grounded nodes
- **Entity Concordance** — map panel showing candidate locations for disambiguation
- **Cross-Source Linker** — geographic proximity as visible alignment evidence

## Implementation Order

1. **Database:** Switch to PostGIS image, add extensions, create `geo_objects` + `spatial_relations` tables
2. **Model:** Add `GeoObject` to `dagster_io/models.py`, add `geo_id` columns to existing models
3. **Gazetteer:** Load GeoNames + Natural Earth as reference data
4. **Toponym asset:** Link GPE/LOC/FACILITY mentions to GeoObjects
5. **H3 covering:** Compute multi-resolution cell assignments for all GeoObjects
6. **Concordance:** Add `s_geo` signal to `CrossSourceAligner`
7. **GraphDB resource:** Add spatial query methods
8. **Visualization:** Map page in data-explorer

Steps 1-3 can proceed immediately. Steps 4-8 depend on the gazetteer being loaded.

## Dependencies

- **Blocks:** AIS/ADS-B vessel tracking, map visualization, geographic entity disambiguation
- **Blocked by:** Nothing — this is foundational infrastructure
- **Enhances:** Entity concordance (optional geo signal), assertion quality (spatial grounding where applicable), data-explorer (map view)
- **Does NOT block:** Core linguistic pipeline, entity concordance (non-spatial signals), assertion extraction, cross-source alignment

## Priority

**Medium-High** — This is important infrastructure for spatial features but is **not a blocker** for the core pipeline. The linguistic/semantic/memetic layer (mentions, propositions, assertions, concordance, canonicalization) operates fully without spatial data. Most entities — people, organizations, laws, concepts — are grounded through text context and semantic structure, not geography.

Spatial grounding is most valuable for:
- Place entities (GPE, LOC, FACILITY) — a subset of all entities
- Investigation-oriented features (vessel tracking, jurisdiction detection)
- Map visualization in the data-explorer
- Geographic disambiguation of ambiguous place names ("Springfield")

## References

- ONTOLOGY.md Sections 7.1-7.5 (Spatial Grounding), 9.5 (Spatial Grounding Layer), 10.1 (GeoObject model), 12.3 (Spatial Index)
- [PostGIS Documentation](https://postgis.net/docs/)
- [H3 Documentation](https://h3geo.org/docs/)
- [h3-pg PostgreSQL Extension](https://github.com/zachasme/h3-pg)
- [GeoSPARQL 1.1 Standard](https://opengeospatial.github.io/ogc-geosparql/geosparql11/)
- [GeoNames Export](https://download.geonames.org/export/dump/)
- [Natural Earth Data](https://www.naturalearthdata.com/downloads/)
