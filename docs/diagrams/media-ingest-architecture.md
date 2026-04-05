# Media Ingest & Transcription Architecture

## 1. Media Ingest Asset Pipeline (Internal)

```mermaid
graph TD
    subgraph Bronze["Bronze Layer (Discovery)"]
        MF[media_files<br/><i>NFS scan: metube + tubesync</i>]
        MM[media_metadata<br/><i>ffprobe enrichment</i>]
    end

    subgraph Silver["Silver Layer (Normalization)"]
        MD[media_documents<br/><i>MediaDocument model</i>]
        MT[media_transcriptions<br/><i>faster-whisper + pyannote</i>]
        MC[media_chunks<br/><i>800/150 recursive split</i>]
    end

    subgraph Gold["Gold Layer (Vectors)"]
        ME[media_embeddings<br/><i>text-embedding-3-small</i>]
    end

    MF --> MM --> MD --> MT --> MC --> ME

    style Bronze fill:#cd7f32,color:#fff
    style Silver fill:#c0c0c0,color:#000
    style Gold fill:#ffd700,color:#000
```

## 2. Transcription as a Cross-Domain Service

```mermaid
graph LR
    subgraph Sources["Media Sources"]
        MeTube[MeTube<br/><i>YouTube downloads</i>]
        TubeSync[TubeSync<br/><i>RSS video sync</i>]
        Future1[Podcast Feeds<br/><i>planned</i>]
        Future2[Meeting Recordings<br/><i>planned</i>]
    end

    subgraph TranscriptionService["Transcription Service Layer"]
        FW[faster-whisper<br/><i>large-v3 / int8</i>]
        PA[pyannote<br/><i>speaker-diarization-3.1</i>]
        FW --> PA
    end

    subgraph Output["Transcription Output"]
        FT[Full Text]
        ST[Speaker-Attributed Text<br/><i>[SPEAKER_00]: ...</i>]
        SEG[Timed Segments<br/><i>word-level timestamps</i>]
        SPK[Speaker Metadata<br/><i>count, IDs</i>]
    end

    MeTube --> TranscriptionService
    TubeSync --> TranscriptionService
    Future1 -.-> TranscriptionService
    Future2 -.-> TranscriptionService

    TranscriptionService --> FT
    TranscriptionService --> ST
    TranscriptionService --> SEG
    TranscriptionService --> SPK

    subgraph Consumers["Any Domain Can Consume"]
        MI[media-ingest<br/><i>media_transcriptions</i>]
        CD[congress-data<br/><i>hearing transcripts</i>]
        OL[open-leaks<br/><i>deposition audio</i>]
        KG[knowledge-graph<br/><i>speaker-entity linking</i>]
    end

    FT --> MI
    ST --> MI
    FT -.-> CD
    FT -.-> OL
    SPK -.-> KG

    style TranscriptionService fill:#4a90d9,color:#fff
    style Consumers fill:#2d2d2d,color:#fff
```

## 3. Catalyst-Data Full Domain Model (Medallion Architecture)

```mermaid
graph TB
    subgraph Bronze["BRONZE — Raw Ingestion"]
        direction LR
        CB[congress_bills<br/>congress_members<br/>congress_committees]
        LB[wikileaks_cables<br/>icij_offshore_*<br/>epstein_court_docs]
        MB[media_files<br/>media_metadata]
    end

    subgraph Silver["SILVER — Normalized Documents & Chunks"]
        direction LR
        CS[congress_documents<br/>congress_chunks]
        LS[leak_documents<br/>leak_chunks]
        MS[media_documents<br/>media_transcriptions<br/>media_chunks]
    end

    subgraph Gold["GOLD — LLM Extraction & Embeddings"]
        direction LR
        CG[congress_mentions<br/>congress_assertions<br/>congress_embeddings]
        LG[leak_mentions<br/>leak_assertions<br/>leak_embeddings]
        MG[media_embeddings]
    end

    subgraph Platinum["PLATINUM — Unified Knowledge Graph"]
        direction LR
        CE[canonical_entities]
        EA[entity_alignments<br/><i>sameAs / possibleSameAs</i>]
        AG[assertion_graph<br/><i>full provenance</i>]
    end

    subgraph Storage["Dual-Write Storage"]
        PG[(PostgreSQL + pgvector)]
        N4J[(Neo4j)]
    end

    subgraph UI["Data Explorer"]
        SE[Streamlit UI<br/><i>asset browser + semantic search</i>]
    end

    CB --> CS --> CG
    LB --> LS --> LG
    MB --> MS --> MG

    CG --> CE
    LG --> CE
    MG -.-> CE

    CE --> EA
    CE --> AG

    AG --> PG
    AG --> N4J
    EA --> PG
    EA --> N4J

    PG --> SE
    N4J --> SE

    style Bronze fill:#cd7f32,color:#fff
    style Silver fill:#c0c0c0,color:#000
    style Gold fill:#ffd700,color:#000
    style Platinum fill:#e5e4e2,color:#000
    style Storage fill:#1a1a2e,color:#fff
    style UI fill:#16213e,color:#fff
```

## 4. Shared Infrastructure (dagster-io)

```mermaid
graph TB
    subgraph SharedLib["dagster-io (Shared Library)"]
        direction TB
        subgraph Resources["Dagster Resources"]
            LLM[LLMResource<br/><i>structured output, batching</i>]
            EMB[EmbeddingResource<br/><i>OpenAI / HuggingFace</i>]
            CHK[ChunkingResource<br/><i>recursive text splitting</i>]
            IOM[MinioIOManager<br/><i>S3 medallion storage</i>]
        end

        subgraph Factories["Asset Factories"]
            NER[make_ner_asset<br/><i>generic NER extraction</i>]
            PROP[make_proposition_asset<br/><i>generic S-P-O extraction</i>]
            BM[build_mentions<br/><i>LLM result → Mention</i>]
            BA[build_assertions<br/><i>LLM result → Assertion</i>]
        end

        subgraph Schemas["Extraction Schemas"]
            ME2[MentionExtraction]
            AER[AssertionExtractionResult]
            NR[NERResult]
            PR[PropositionResult]
        end

        subgraph Obs["Observability"]
            LOG[Structured Logging]
            MET[Prometheus Metrics]
            TRC[OpenTelemetry Tracing]
        end
    end

    subgraph Contracts["catalyst-contracts-core"]
        MT2[MentionType enum]
        AT[AlignmentType enum]
        EM[ExtractionMethod enum]
        PV[Provenance model]
    end

    subgraph Domains["Domain Packages"]
        CD2[congress-data<br/><i>+ domain prompts<br/>+ predicate mappings</i>]
        OL2[open-leaks<br/><i>+ domain prompts<br/>+ predicate mappings</i>]
        MI2[media-ingest<br/><i>+ whisper/pyannote<br/>+ audio processing</i>]
        KG2[knowledge-graph<br/><i>+ entity resolution<br/>+ graph construction</i>]
    end

    Contracts --> SharedLib
    SharedLib --> CD2
    SharedLib --> OL2
    SharedLib --> MI2
    SharedLib --> KG2

    style SharedLib fill:#2d5016,color:#fff
    style Contracts fill:#4a1942,color:#fff
    style Domains fill:#1a1a2e,color:#fff
```

## 5. Transcription Data Flow (Detailed)

```mermaid
sequenceDiagram
    participant NFS as NFS Volume<br/>(metube/tubesync)
    participant Whisper as faster-whisper<br/>(large-v3)
    participant Pyannote as pyannote<br/>(diarization-3.1)
    participant Align as Speaker Alignment
    participant Chunk as ChunkingResource<br/>(800/150)
    participant Embed as EmbeddingResource
    participant S3 as MinIO S3

    NFS->>Whisper: audio file path
    Whisper->>Whisper: transcribe(word_timestamps=True)
    Whisper-->>Align: segments[] + words[] + language info

    NFS->>Pyannote: audio file path
    Pyannote->>Pyannote: pipeline(audio)
    Pyannote-->>Align: speaker turns[]

    Align->>Align: word midpoint → speaker turn matching
    Align->>Align: majority vote per segment
    Note over Align: Output: full_text, speaker_text,<br/>segments with speaker labels

    Align->>S3: media_transcriptions (gold/data.jsonl)

    Align->>Chunk: speaker_text (preferred) or full_text
    Chunk->>Chunk: recursive split with speaker context
    Chunk->>S3: media_chunks (silver/data.jsonl)

    Chunk->>Embed: chunk texts[]
    Embed->>Embed: batch embed (100/batch)
    Embed->>S3: media_embeddings (gold/data.jsonl)
```

## 6. K8s Runtime Architecture

```mermaid
graph TB
    subgraph Cluster["Talos Kubernetes Cluster"]
        subgraph NS["catalyst-data namespace"]
            WS[dagster-webserver<br/><i>UI + API</i>]
            DM[dagster-daemon<br/><i>scheduler + sensor</i>]
            PG2[(dagster-postgres<br/><i>run/event storage</i>)]

            subgraph CodeServers["Code Servers (gRPC :4000)"]
                CS2[congress-data]
                OLS[open-leaks]
                MIS[media-ingest]
                KGS[knowledge-graph]
            end

            subgraph RunJobs["K8sRunLauncher Jobs"]
                RJ[dagster-run-*<br/><i>ephemeral pods<br/>no resource limits<br/>burst allowed</i>]
            end
        end

        subgraph Storage2["Storage"]
            MINIO[(MinIO<br/><i>S3-compatible</i>)]
            NFS2[(TrueNAS NFS<br/><i>192.168.1.36</i>)]
            WM[(whisper-models PVC<br/><i>20Gi local-path</i>)]
        end

        subgraph GPU["GPU Worker Nodes"]
            T02[talos02-gpu]
            T06[talos06-gpu]
        end

        subgraph LLM["catalyst-llm namespace"]
            LIT[LiteLLM Proxy<br/><i>:4000/v1</i>]
        end

        subgraph Secrets["External Secrets (1Password)"]
            S3C[dagster-s3-credentials]
            LLMC[llm-credentials]
            HFC[hf-credentials<br/><i>HF_TOKEN</i>]
        end
    end

    WS --> PG2
    DM --> PG2
    DM --> CodeServers
    DM --> RunJobs

    RunJobs --> NFS2
    RunJobs --> MINIO
    RunJobs --> WM
    RunJobs --> LIT
    RunJobs --> GPU

    Secrets --> RunJobs

    style NS fill:#1a1a2e,color:#fff
    style Storage2 fill:#2d2d2d,color:#fff
    style GPU fill:#4a0e0e,color:#fff
    style LLM fill:#0e2f4a,color:#fff
```
