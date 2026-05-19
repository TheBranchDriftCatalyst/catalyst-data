# Pipeline Lineage: Dagster SDAs + LangGraph Execution

## Dagster Asset Lineage (per code location)

### Media-Ingest

```mermaid
flowchart TD
    subgraph BRONZE ["Bronze (Discovery)"]
        MF[media_files]
        MM[media_metadata]
        MT[media_transcode]
    end

    subgraph SILVER ["Silver (Transform)"]
        MD[media_documents]
        MX[media_transcriptions]
        MR[media_diarization]
        MS[media_segment_merge]
    end

    subgraph GOLD_CHUNKS ["Gold (Chunking)"]
        MC[media_chunks]
    end

    subgraph GOLD_EXTRACT ["Gold (Extraction) — asset_factory"]
        ME["media_mentions\n+ media_assertions\n@multi_asset"]
        MB[media_embeddings]
    end

    subgraph GOLD_SPEAKERS ["Gold (Speaker Analysis)"]
        SE[media_speaker_embeddings]
        SP[media_speaker_profiles]
    end

    subgraph PLATINUM ["Platinum (Knowledge Graph)"]
        EC[media_entity_candidates]
    end

    MF --> MM --> MT
    MM --> MD
    MD --> MX --> MR --> MS --> MC
    MC -->|extract_validated| ME
    MC --> MB
    MR --> SE --> SP
    ME --> EC
    SP -.->|optional| EC

    style GOLD_EXTRACT fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
    style GOLD_CHUNKS fill:#1a3326,stroke:#22c55e,color:#86efac
    style GOLD_SPEAKERS fill:#3b2e1a,stroke:#f59e0b,color:#fcd34d
```

### Congress-Data (Bill Pipeline)

```mermaid
flowchart TD
    subgraph DISCOVERY ["Discovery (Unpartitioned)"]
        BL[bills_list_incremental]
        BM[bills_manifest]
    end

    subgraph BRONZE ["Bronze (per bill partition)"]
        BD[bill_detail]
        BA[bill_actions]
        BC[bill_cosponsors]
        BT[bill_text_versions]
        BX[bill_amendments]
        BF[bill_full_text]
    end

    subgraph SILVER ["Silver"]
        BO[bill_document]
        BK[bill_chunks]
    end

    subgraph GOLD ["Gold — asset_factory"]
        BE["bill_mentions\n+ bill_assertions\n@multi_asset"]
        BB[bill_embeddings]
    end

    BL --> BM
    BM -.->|sensor| BD
    BD --> BA & BC & BT & BX
    BT --> BF
    BD & BF --> BO --> BK
    BK -->|extract_validated| BE
    BK --> BB

    style GOLD fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
```

### Congress-Data (Member Pipeline)

```mermaid
flowchart TD
    subgraph DISCOVERY ["Discovery"]
        ML[members_list_incremental]
        MM[members_manifest]
    end

    subgraph BRONZE ["Bronze (per member partition)"]
        MD[member_detail]
        MCA[member_committee_assignments]
        MSP[member_sponsored]
        MCO[member_cosponsored]
    end

    subgraph SILVER ["Silver"]
        MO[member_document]
        MK[member_chunks]
    end

    subgraph GOLD ["Gold — asset_factory"]
        ME[member_mentions]
        MB[member_embeddings]
    end

    ML --> MM
    MM -.->|sensor| MD
    MD --> MCA & MSP & MCO
    MD --> MO --> MK
    MK -->|extract_validated| ME
    MK --> MB

    style GOLD fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
```

### Open-Leaks

```mermaid
flowchart TD
    subgraph BRONZE ["Bronze (Sources)"]
        WC[wikileaks_cables]
        IO[icij_offshore_entities]
        IR[icij_offshore_relationships]
        EP[epstein_court_docs]
    end

    subgraph SILVER ["Silver"]
        LD[leak_documents]
        LC[leak_chunks]
    end

    subgraph GOLD ["Gold — asset_factory"]
        LM["leak_mentions\n+ leak_assertions\n@multi_asset"]
        LE[leak_embeddings]
    end

    subgraph PLATINUM ["Platinum"]
        LEC[leak_entity_candidates]
        LG[leak_graph]
    end

    WC & IO & IR & EP --> LD --> LC
    LC -->|extract_validated| LM
    LC --> LE
    LM --> LEC
    LM & LEC --> LG

    style GOLD fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
```

---

## LangGraph Execution Chain (inside extract_validated)

### Pipeline Topology

```mermaid
flowchart TD
    START([extract_validated called]) --> BUILD[_build_graph_v2]
    BUILD --> RESOLVE[Resolve client\nLLM / GLiNER / NuExtract]
    RESOLVE --> PIPELINE[build_pipeline]

    PIPELINE --> CHUNK{ChunkConfig\nprovided?}
    CHUNK -->|yes| CN[ChunkNode\nsplit raw_text]
    CHUNK -->|no| SKIP[Use pre-chunked input]
    CN --> PER_CHUNK
    SKIP --> PER_CHUNK

    subgraph PER_CHUNK ["Per-Chunk Loop (ThreadPoolExecutor)"]
        direction TB
        subgraph NER_STAGE ["stage_ner"]
            EX_NER[extract_ner\nLLM/encoder call] --> VAL_NER[validate_ner\nMCP contract check]
            VAL_NER --> ROUTE_NER{verdict?}
            ROUTE_NER -->|valid| ACCEPT_NER[accepted_mentions]
            ROUTE_NER -->|"invalid/ambiguous\n& retries < max"| REP_NER[repair_ner\nLLM fix + span hints]
            REP_NER --> VAL_NER
            ROUTE_NER -->|"retries >= max"| ACCEPT_NER
        end

        subgraph SPO_STAGE ["stage_spo (skipped for encoders)"]
            EX_SPO[extract_spo\n+ accepted_mentions\nas constraints] --> VAL_SPO[validate_spo\nMCP contract check]
            VAL_SPO --> ROUTE_SPO{verdict?}
            ROUTE_SPO -->|valid| ACCEPT_SPO[accepted_propositions]
            ROUTE_SPO -->|"invalid/ambiguous\n& retries < max"| REP_SPO[repair_spo\nLLM fix]
            REP_SPO --> VAL_SPO
            ROUTE_SPO -->|"retries >= max"| ACCEPT_SPO
        end

        ACCEPT_NER -->|"upstream_context\naccepted_mentions"| EX_SPO
    end

    ACCEPT_SPO --> AGGREGATE[Aggregate across chunks]
    AGGREGATE --> RETURN["Return\n(all_mentions, all_assertions)"]

    style NER_STAGE fill:#1e3a5f,stroke:#3b82f6,color:#93c5fd
    style SPO_STAGE fill:#1a3326,stroke:#22c55e,color:#86efac
    style PER_CHUNK fill:#0f172a,stroke:#475569,color:#94a3b8
```

### Stage Subgraph Detail (NER or SPO)

```mermaid
stateDiagram-v2
    [*] --> extract: Start stage

    extract --> validate: candidates produced

    validate --> done: verdict=valid
    validate --> repair: verdict=invalid/ambiguous\n& retries < max
    validate --> done: retries >= max (give up)

    repair --> validate: repaired candidates\nretry_count++

    done --> [*]: accepted = valid_items

    note right of extract
        Load prompt from PROMPT_REGISTRY_DIR
        Call client.structured_output(schema)
        correct_candidate_spans() post-process
    end note

    note right of validate
        MCP call: validate_mentions/validate_propositions
        Returns verdict + errors[]
        Ambiguous = partial accept (valid subset)
    end note

    note right of repair
        Compute span hints (correct offsets)
        Send errors + hints + candidates to LLM
        correct_candidate_spans() post-process
    end note
```

### How Dagster and LangGraph Interact

```mermaid
flowchart LR
    subgraph DAGSTER ["Dagster (Orchestration)"]
        direction TB
        SENSOR[Sensor\ndetects new data] --> LAUNCH[Launch Run]
        LAUNCH --> STEP[Op Step\nasset materialization]
        STEP --> IO[IO Manager\nwrite to S3/Minio]
        IO --> LINEAGE[SDA Lineage\nchunks → mentions → assertions]
    end

    subgraph LANGGRAPH ["LangGraph (Execution)"]
        direction TB
        GRAPH[Compiled StateGraph]
        GRAPH --> NODES[Node execution\nextract → validate → repair]
        NODES --> AUDIT[Audit events\ntimestamps + durations]
    end

    subgraph CONFIG ["Config Flow"]
        direction TB
        ENV["ENV VARS\nLLM_MODEL\nLLM_CONTEXT_WINDOW\nPROMPT_REGISTRY_DIR"]
        RES["Dagster Resources\nLLMResource\nEmbeddingResource\nChunkingResource"]
        CC["ChunkConfig\n(25% of context window)"]
    end

    STEP -->|"calls extract_validated()"| GRAPH
    ENV --> RES --> CC --> GRAPH
    GRAPH -->|"returns ExtractionResult"| STEP
    AUDIT -->|"surfaced via result.stats / result.audit_events"| IO

    style DAGSTER fill:#1e293b,stroke:#6366f1,color:#a5b4fc
    style LANGGRAPH fill:#1a2e1a,stroke:#22c55e,color:#86efac
    style CONFIG fill:#2e1a1a,stroke:#ef4444,color:#fca5a5
```

### Parallelism Boundaries

```mermaid
flowchart TD
    subgraph DAGSTER_PARALLEL ["Dagster Parallelism"]
        direction LR
        P1[bill_119-hr-1\npartition run]
        P2[bill_119-hr-2\npartition run]
        P3[bill_119-hr-3\npartition run]
    end

    subgraph LANGGRAPH_PARALLEL ["LangGraph Parallelism (within one partition)"]
        direction LR
        C1[chunk_001\nThreadPool]
        C2[chunk_002\nThreadPool]
        C3[chunk_003\nThreadPool]
    end

    subgraph LANGGRAPH_SEQUENTIAL ["LangGraph Sequential (within one chunk)"]
        direction LR
        NER[NER stage] --> SPO[SPO stage]
    end

    P1 --> C1 & C2 & C3
    C1 --> LANGGRAPH_SEQUENTIAL

    style DAGSTER_PARALLEL fill:#1e293b,stroke:#6366f1,color:#a5b4fc
    style LANGGRAPH_PARALLEL fill:#1a2e1a,stroke:#22c55e,color:#86efac
    style LANGGRAPH_SEQUENTIAL fill:#2e1a1a,stroke:#ef4444,color:#fca5a5
```

| Scope | Owner | Mechanism |
|-------|-------|-----------|
| Cross-document | Dagster | Partition runs (separate K8s pods) |
| Cross-chunk (within doc) | LangGraph | ThreadPoolExecutor (max_concurrency=5) |
| Cross-stage (within chunk) | LangGraph | Sequential (NER must complete before SPO) |
| Repair loop (within stage) | LangGraph | Conditional edges (validate → repair → validate) |
