# Feature Request: AIS & ADS-B Vessel/Aircraft Tracking Data Sources

## Summary

Ingest AIS (Automatic Identification System) maritime vessel data and ADS-B (Automatic Dependent Surveillance-Broadcast) aircraft tracking data as first-class geospatial data sources. These provide high-resolution spatio-temporal movement data that can be cross-referenced against entities, assertions, and events already in the knowledge graph — enabling "who was where when" queries grounded in physical reality rather than text mentions alone.

## Motivation

The core catalyst-data pipeline is built around a **linguistic/semantic/memetic layer** — propositions, entity concordance, and cross-source alignment grounded in text and context. That layer works independently of spatial data. However, AIS/ADS-B tracking data represents a high-value **enrichment layer** for investigation use cases because:

1. **Structured and standardized** — NMEA 0183 (AIS), SBS-1/Beast (ADS-B) are well-defined wire formats with mature parsing libraries.
2. **Inherently spatio-temporal** — every message carries (lat, lon, timestamp, heading, speed), providing physical grounding that text-derived assertions alone cannot offer.
3. **Entity-rich** — vessels have MMSI/IMO numbers, aircraft have ICAO hex codes and callsigns — stable identifiers that bridge to corporate registries, flag states, and operator databases.
4. **Investigation-relevant** — leaked document corpora (open-leaks) frequently reference vessels, shipping routes, offshore jurisdictions, and aviation movements. Cross-referencing text assertions against physical track data is high-value.
5. **Complements the semantic layer** — the linguistic pipeline answers "what is claimed"; movement data answers "what physically happened" — together they enable validation and contradiction detection.

## Data Sources

### Maritime (AIS)

| Source | Type | Coverage | Access |
|--------|------|----------|--------|
| [MarineTraffic API](https://www.marinetraffic.com/en/ais-api-services) | Commercial API | Global, real-time + historical | API key, paid tiers |
| [AISHub](https://www.aishub.net/) | Community feed | Global, real-time | Free (data sharing agreement) |
| [UN Global Platform AIS](https://unstats.un.org/wiki/display/AIS/) | Bulk historical | Global, monthly dumps | Free for research |
| [Spire Maritime](https://spire.com/maritime/) | Commercial API | Global, real-time + historical | API key, paid |
| Local RTL-SDR receiver | Self-hosted | Line-of-sight (~50nm) | Hardware ($30 dongle + antenna) |

**AIS message types relevant:**

| Type | Content |
|------|---------|
| 1-3 | Position report (lat, lon, SOG, COG, heading, MMSI, nav status) |
| 5 | Static/voyage data (IMO, callsign, vessel name, type, destination, ETA, dimensions) |
| 18-19 | Class B position report (smaller vessels) |
| 24 | Class B static data |

### Aviation (ADS-B)

| Source | Type | Coverage | Access |
|--------|------|----------|--------|
| [OpenSky Network](https://opensky-network.org/) | Community + API | Global, real-time + historical (2013+) | Free (academic), API key |
| [ADS-B Exchange](https://www.adsbexchange.com/) | Community feed | Global, real-time | Free API (rate-limited), bulk via RapidAPI |
| [FlightAware Firehose](https://www.flightaware.com/commercial/firehose/) | Commercial | Global, real-time | Paid |
| Local RTL-SDR receiver (1090MHz) | Self-hosted | Line-of-sight (~250nm) | Hardware ($30 dongle + antenna) |

**ADS-B fields:**

| Field | Content |
|-------|---------|
| ICAO hex | Unique aircraft identifier |
| Callsign | Flight number or registration |
| Latitude/Longitude | Position |
| Altitude | Barometric + geometric |
| Ground speed, track | Movement vector |
| Vertical rate | Climb/descend |
| Squawk | Transponder code (emergency codes: 7500 hijack, 7600 comms failure, 7700 emergency) |

### Supplementary Registries (for entity linking)

| Registry | Content | Use |
|----------|---------|-----|
| [ITU MARS](https://www.itu.int/en/ITU-R/terrestrial/mars/Pages/default.aspx) | MMSI → vessel name, flag state, owner | Entity linking |
| [Equasis](https://www.equasis.org/) | IMO → vessel details, inspection history, P&I club | Entity enrichment |
| [ICAO Aircraft Registry](https://www.icao.int/publications/DOC8643/Pages/Search.aspx) | ICAO hex → registration, type, operator | Entity linking |
| [OpenFlights](https://openflights.org/data.html) | Airport codes, airline codes, route data | Spatial reference |
| [FAA N-number](https://registry.faa.gov/AircraftInquiry/) | US registration → owner, address | Entity linking (US aircraft) |

## Data Model

### Track Point (bronze)

```python
@dataclass
class TrackPoint:
    """Single position report from AIS or ADS-B."""
    source_type: Literal["ais", "adsb"]
    identifier: str          # MMSI (AIS) or ICAO hex (ADS-B)
    timestamp: datetime
    latitude: float
    longitude: float
    altitude_m: float | None # ADS-B only
    speed_knots: float | None
    heading: float | None    # degrees true
    course: float | None     # COG (AIS) or track (ADS-B)
    raw_message: str | None  # original NMEA/SBS sentence
```

### Track Segment (silver)

```python
@dataclass
class TrackSegment:
    """Contiguous movement segment between stops/gaps."""
    segment_id: str
    identifier: str
    source_type: Literal["ais", "adsb"]
    start_time: datetime
    end_time: datetime
    start_point: tuple[float, float]  # (lat, lon)
    end_point: tuple[float, float]
    geometry_wkt: str         # LINESTRING from interpolated points
    h3_cells: list[str]       # H3 res-7 cells traversed
    distance_nm: float
    duration_hours: float
    point_count: int
```

### Vessel / Aircraft Entity (silver)

```python
@dataclass
class TransportEntity:
    """Vessel or aircraft as a canonical entity."""
    entity_id: str
    source_type: Literal["vessel", "aircraft"]
    primary_identifier: str   # MMSI or ICAO hex
    secondary_ids: dict       # {"imo": "...", "callsign": "...", "registration": "..."}
    name: str | None
    entity_type: str          # "cargo_vessel", "tanker", "private_jet", "commercial_airline", etc.
    flag_state: str | None    # ISO country code
    operator: str | None
    owner: str | None         # from registry lookup
    dimensions: dict | None   # length, beam, draft (vessels) or wingspan, MTOW (aircraft)
```

### Port/Airport Visit (gold)

```python
@dataclass
class FacilityVisit:
    """Detected arrival/departure at a port or airport."""
    visit_id: str
    entity_id: str            # vessel/aircraft
    facility_id: str          # port LOCODE or airport ICAO
    facility_name: str
    facility_location: tuple[float, float]
    arrival_time: datetime | None
    departure_time: datetime | None
    duration_hours: float | None
    h3_cell: str              # H3 res-7 cell of facility
```

## Pipeline Design

```
Bronze                    Silver                     Gold                      Platinum
──────                    ──────                     ────                      ────────
ais_raw_positions    →  ais_track_segments      →  vessel_port_visits     →  transport_entity_graph
adsb_raw_positions   →  adsb_track_segments     →  aircraft_airport_visits    (cross-ref with KG entities)
                     →  transport_entities       →  facility_visits
                        (registry enrichment)       (port/airport detection)

                                                    ↓
                                              assertion_linking
                                              (match vessel/aircraft entities
                                               to mentions in open-leaks/
                                               congress-data assertions)
```

### Asset Breakdown

| Asset | Layer | Description |
|-------|-------|-------------|
| `ais_positions` | bronze | Ingest AIS position reports from API/feed |
| `adsb_positions` | bronze | Ingest ADS-B position reports from API/feed |
| `vessel_registry` | bronze | Fetch vessel metadata from ITU/Equasis |
| `aircraft_registry` | bronze | Fetch aircraft metadata from ICAO/FAA |
| `ais_track_segments` | silver | Segment continuous vessel tracks, compute geometry |
| `adsb_track_segments` | silver | Segment continuous aircraft tracks, compute geometry |
| `transport_entities` | silver | Merge registry data with observed identifiers |
| `vessel_port_visits` | gold | Detect port arrivals/departures from track gaps + geofences |
| `aircraft_airport_visits` | gold | Detect airport visits from altitude + proximity |
| `facility_visits` | gold | Unified port/airport visit timeline |
| `transport_entity_graph` | platinum | Link transport entities to KG canonical entities |
| `movement_assertions` | platinum | Generate assertions from observed movements ("Vessel X visited Port Y on Date Z") |

## Spatial Infrastructure Requirements

This feature **depends on** the spatial grounding layer from ONTOLOGY.md Section 7. Specifically:

### Must Have

- [ ] **PostGIS** extension on postgres-knowledge (geometry storage, spatial queries)
- [ ] **H3** cell covering (multi-resolution spatial indexing)
- [ ] **GeoObject model** in dagster-io/models.py (geometry_wkt, centroid, h3_cells, admin_hierarchy)
- [ ] **Temporal interval model** (start_time, end_time, granularity)
- [ ] **Map visualization** in data-explorer (deck.gl or MapLibre for track rendering)

### Nice to Have

- [ ] **GeoSPARQL** vocabulary for spatial relation predicates
- [ ] **Port/airport geofence database** (pre-computed H3 cell sets for ~5000 major ports, ~4000 airports)
- [ ] **EEZ/territorial waters boundaries** for jurisdiction detection
- [ ] **Restricted airspace polygons** for flight path analysis

## K8s Considerations

### New Package

```
packages/
  transport-tracking/
    src/transport_tracking/
      __init__.py
      assets/
        ais_positions.py
        adsb_positions.py
        track_segments.py
        transport_entities.py
        facility_visits.py
        movement_assertions.py
      client/
        ais_client.py       # AISHub/MarineTraffic API
        adsb_client.py      # OpenSky/ADS-B Exchange API
        registry_client.py  # Vessel/aircraft registry lookups
    prompts/
      movement-assertion.prompt  # Generate assertions from movement patterns
    pyproject.toml
```

### Deployment

```yaml
# k8s/transport-tracking/deployment.yaml
# Similar to congress-data/open-leaks pattern
# Additional env vars:
#   AIS_API_KEY, AIS_API_URL
#   ADSB_API_KEY, ADSB_API_URL
#   POSTGIS_URL (postgres-knowledge with PostGIS)
```

### Storage Estimates

| Data Type | Volume | Storage |
|-----------|--------|---------|
| AIS positions (global, 1 month) | ~3B messages | ~500GB raw, ~50GB compressed |
| ADS-B positions (global, 1 month) | ~10B messages | ~1.5TB raw, ~150GB compressed |
| Track segments (derived) | ~50M segments/month | ~5GB |
| Port/airport visits (derived) | ~5M visits/month | ~500MB |

For a focused use case (specific vessels/aircraft of interest only), reduce by 99%+. Start with targeted ingestion.

## Cross-Reference Use Cases

### 1. Vessel-Entity Linking
Match vessel names/owners from AIS registry data against entities mentioned in leaked documents. Example: "Shell company X owns vessel Y" (assertion from open-leaks) + "Vessel Y visited Port Z on Date W" (assertion from AIS data).

### 2. Sanctions Monitoring
Cross-reference vessel tracks against sanctioned entity lists. Detect flag-state changes, AIS gaps (dark voyages), and ship-to-ship transfers.

### 3. Congressional Oversight
Link aircraft registrations to political donors, lobbyists, or government officials. Cross-reference with congressional hearing dates and locations.

### 4. Temporal Correlation
"Entity X met Entity Y" (assertion from text) + "Aircraft registered to X landed at same airport as aircraft registered to Y within 24h" (assertion from ADS-B).

### 5. Spatial Anomaly Detection
Vessels deviating from declared routes, aircraft circling specific locations, port visits to sanctioned jurisdictions — all generate assertions that enrich the knowledge graph.

## Priority

**Medium-High** — This is a Phase 2 feature (spatial grounding) per ONTOLOGY.md Section 14. It is blocked by the spatial infrastructure prerequisites (PostGIS, H3, GeoObject model) but is one of the highest-value data sources for the investigation use case.

## References

- ONTOLOGY.md Sections 7 (Spatial Grounding), 9.5 (Spatial Grounding Layer), 10 (HGO Visualization Engine)
- [IMO AIS standards](https://www.imo.org/en/OurWork/Safety/Pages/AIS.aspx)
- [ICAO Annex 10 Vol IV (ADS-B)](https://www.icao.int/safety/acp/repository)
- [H3 Geo](https://h3geo.org/)
- [OpenSky Network API](https://openskynetwork.github.io/opensky-api/)
- [Global Fishing Watch (AIS analytics reference)](https://globalfishingwatch.org/)
