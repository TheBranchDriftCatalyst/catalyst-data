# Catalyst-Data Domain Model UML

## Entity Relationship Diagram

```mermaid
classDiagram
    direction TB

    class Provenance {
        +str source_document_id
        +str chunk_id
        +int? span_start
        +int? span_end
        +int? temporal_start_ms
        +int? temporal_end_ms
        +str? speaker_label
        +str? source_media_uri
        +ExtractionMethod extraction_method
        +str extraction_model
        +float confidence
        +str timestamp
        +str code_location
    }

    class Mention {
        +str mention_id ‹SHA256›
        +str document_id
        +str chunk_id
        +str text
        +MentionType mention_type
        +int? span_start
        +int? span_end
        +str context
        +str content_hash
        +Provenance provenance
    }

    class Assertion {
        +str assertion_id ‹SHA256›
        +str subject_text
        +str subject_mention_id
        +str predicate
        +str predicate_canonical
        +str object_text
        +str object_mention_id
        +dict qualifiers
        +float confidence
        +bool negated
        +bool hedged
        +str content_hash
        +Provenance provenance
    }

    class EntityCandidate {
        +str candidate_id ‹SHA256›
        +str canonical_name
        +MentionType candidate_type
        +list~str~ aliases
        +list~str~ mention_ids
        +int mention_count
        +dict external_ids
        +list~float~? embedding
        +list~str~ source_documents
        +str code_location
        +str content_hash
    }

    class CanonicalEntity {
        +str canonical_id ‹UUID›
        +str canonical_name
        +MentionType entity_type
        +list~str~ aliases
        +str description
        +dict external_ids
        +list~str~ source_candidate_ids
        +list~str~ source_code_locations
        +list~float~? embedding
        +int mention_count
        +str first_seen
        +str last_seen
    }

    class AlignmentEdge {
        +str edge_id ‹SHA256›
        +str source_entity_id
        +str target_entity_id
        +AlignmentType alignment_type
        +float score
        +list~str~ evidence
        +str method
    }

    class TextChunk {
        +str chunk_id
        +str document_id
        +str text
        +int index
        +int total_chunks
        +dict metadata
        +str content_hash
    }

    class MediaDocument {
        +str id
        +str title
        +str source_path
        +str source
        +str document_type
        +str domain
        +dict metadata
    }

    class MentionType {
        <<enumeration>>
        PERSON
        ORG
        GPE
        LOC
        DATE
        LAW
        EVENT
        MONEY
        NORP
        FACILITY
        OTHER
    }

    class AlignmentType {
        <<enumeration>>
        sameAs
        possibleSameAs
        relatedTo
        partOf
    }

    class ExtractionMethod {
        <<enumeration>>
        llm
        spacy
        regex
        manual
        structured
    }

    %% Relationships
    Mention --> Provenance : provenance
    Assertion --> Provenance : provenance
    Mention --> MentionType : mention_type
    EntityCandidate --> MentionType : candidate_type
    CanonicalEntity --> MentionType : entity_type
    AlignmentEdge --> AlignmentType : alignment_type
    Provenance --> ExtractionMethod : extraction_method

    TextChunk ..> Mention : extracted from
    TextChunk ..> Assertion : extracted from
    Mention ..> EntityCandidate : grouped into
    EntityCandidate ..> CanonicalEntity : resolved to
    EntityCandidate ..> AlignmentEdge : linked by
    MediaDocument ..> TextChunk : chunked into
    Assertion ..> Mention : references subject/object
```

## Extraction Pipeline Flow

```mermaid
flowchart TB
    subgraph Bronze["Bronze Layer"]
        SRC[Data Sources<br/><i>congress.gov, WikiLeaks, ICIJ,<br/>MeTube, TubeSync</i>]
    end

    subgraph Silver["Silver Layer"]
        DOC[Documents]
        CHK[TextChunks<br/><i>800/150 for speech<br/>1000/200 for text</i>]
    end

    subgraph Gold["Gold Layer — Domain Extractors"]
        direction LR
        subgraph Extractors["Specialized Extractors"]
            EX1[Congress Extractor<br/><i>legislators, bills,<br/>votes, committees</i>]
            EX2[Leaks Extractor<br/><i>offshore entities,<br/>transactions, diplomats</i>]
            EX3[Media Extractor<br/><i>speakers, speech acts,<br/>claims, references</i>]
            EX4[Financial Extractor<br/><i>tickers, predictions,<br/>sentiment, targets</i>]
            EX5[Geopolitical Extractor<br/><i>nations, treaties,<br/>conflicts, sanctions</i>]
        end
        MEN[Mentions<br/><i>PERSON, ORG, GPE,<br/>MONEY, TICKER...</i>]
        ASS[Assertions<br/><i>S-P-O with qualifiers,<br/>confidence, hedging</i>]
        EMB[Embeddings<br/><i>text-embedding-3-small<br/>1536-dim</i>]
    end

    subgraph Platinum["Platinum Layer — Knowledge Graph"]
        EC[EntityCandidates<br/><i>within-source grouping</i>]
        CE[CanonicalEntities<br/><i>cross-source resolution</i>]
        AE[AlignmentEdges<br/><i>sameAs, possibleSameAs</i>]
        AG[Assertion Graph<br/><i>full provenance</i>]
    end

    subgraph Storage["Dual-Write"]
        PG[(PostgreSQL + pgvector)]
        N4J[(Neo4j)]
    end

    SRC --> DOC --> CHK
    CHK --> EX1 & EX2 & EX3 & EX4 & EX5
    EX1 & EX2 & EX3 & EX4 & EX5 --> MEN & ASS
    CHK --> EMB
    MEN --> EC --> CE
    CE --> AE
    ASS --> AG
    CE & AE & AG --> PG & N4J

    style Bronze fill:#cd7f32,color:#fff
    style Silver fill:#c0c0c0,color:#000
    style Gold fill:#ffd700,color:#000
    style Platinum fill:#e5e4e2,color:#000
    style Extractors fill:#2d5016,color:#fff
```

## Multi-Extractor Architecture

```mermaid
flowchart LR
    subgraph Input
        CHUNK[TextChunk<br/><i>text, metadata,<br/>speaker, language</i>]
    end

    subgraph ExtractorRegistry["Extractor Registry"]
        direction TB
        REG[ExtractorConfig<br/><i>name, prompt, schema,<br/>predicate_mappings,<br/>mention_types</i>]
    end

    subgraph LLM["LLM Pipeline"]
        direction TB
        PROMPT[Domain Prompt<br/><i>loaded from registry</i>]
        CHAIN[Structured Output Chain<br/><i>with_structured_output(Schema)</i>]
        BATCH[invoke_batch<br/><i>parallel chunk processing</i>]
    end

    subgraph Validation["Contract Validation (MCP)"]
        direction TB
        VM[validate_mentions<br/><i>span check, type check,<br/>duplicate detection</i>]
        VP[validate_propositions<br/><i>reference check, predicate<br/>format, score range</i>]
        RP[generate_repair_plan<br/><i>auto-fix common issues</i>]
    end

    subgraph Output["Domain Models"]
        MEN2[list~Mention~<br/><i>with Provenance</i>]
        ASS2[list~Assertion~<br/><i>with Provenance +<br/>normalized predicates</i>]
    end

    CHUNK --> REG
    REG --> PROMPT --> CHAIN --> BATCH
    BATCH --> VM & VP
    VM --> RP
    VP --> RP
    RP --> MEN2 & ASS2

    style ExtractorRegistry fill:#4a1942,color:#fff
    style Validation fill:#0e2f4a,color:#fff
```

## Extractor Configuration Pattern

Each domain extractor is defined by:

```yaml
# Example: FinancialDataExtractor
name: financial
prompt_id: "extractors/financial"
mention_schema: MentionExtractionResult  # or extended FinancialMentionResult
assertion_schema: AssertionExtractionResult  # or extended FinancialAssertionResult
mention_types:
  - PERSON    # analysts, fund managers
  - ORG       # companies, funds, exchanges
  - MONEY     # prices, targets, amounts
  - TICKER    # $AAPL, $SPY (NEW)
  - DATE      # earnings dates, expiry
  - EVENT     # earnings calls, FOMC meetings
predicate_mappings:
  "predicts": "predicts"
  "recommends": "recommends"
  "upgrades": "upgrades"
  "downgrades": "downgrades"
  "buys": "buys"
  "sells": "sells"
  "is bullish on": "bullish"
  "is bearish on": "bearish"
  "targets": "price_target"
  "expects": "predicts"
qualifiers:
  - time       # "by Q3 2026"
  - condition  # "if the Fed cuts rates"
  - price      # "$200 price target" (NEW)
  - timeframe  # "within 6 months" (NEW)
  - conviction # "high conviction" (NEW)
```

```yaml
# Example: GeopoliticalAnalystExtractor
name: geopolitical
prompt_id: "extractors/geopolitical"
mention_types:
  - PERSON    # heads of state, diplomats
  - ORG       # NATO, UN, EU, BRICS
  - GPE       # nations, regions
  - EVENT     # summits, conflicts, elections
  - LAW       # treaties, sanctions, resolutions
  - NORP      # ethnic/religious groups
predicate_mappings:
  "sanctions": "sanctions"
  "invades": "military_action"
  "allies with": "allied_with"
  "threatens": "threatens"
  "negotiates": "negotiates"
  "withdraws from": "withdraws"
  "signs": "ratifies"
  "vetoes": "vetoes"
  "condemns": "condemns"
  "recognizes": "recognizes"
qualifiers:
  - time       # "during the G7 summit"
  - location   # "in the South China Sea"
  - condition  # "unless sanctions are lifted"
  - manner     # "unilaterally"
```
