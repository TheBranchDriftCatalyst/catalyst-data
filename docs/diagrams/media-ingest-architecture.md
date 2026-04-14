# Media Ingest & Transcription Architecture

## 1. Media Ingest Asset Pipeline (10 Assets)

```mermaid
graph TD
    subgraph Bronze["Bronze Layer (Discovery)"]
        MF[media_files<br/><i>NFS scan: metube + tubesync</i>]
        MM[media_metadata<br/><i>ffprobe enrichment</i>]
    end

    subgraph Silver["Silver Layer (Processing)"]
        MTC[media_transcode<br/><i>QSV AV1 encode<br/>gpu.intel.com/i915</i>]
        MD[media_documents<br/><i>MediaDocument model</i>]
        MC[media_chunks<br/><i>800/150 recursive split<br/>speaker-attributed</i>]
    end

    subgraph Gold["Gold Layer (ML + Extraction)"]
        MT[media_transcriptions<br/><i>OpenVINO GPU whisper<br/>chunked for long audio</i>]
        MDIA[media_diarization<br/><i>pyannote speaker ID<br/>XPU accelerated</i>]
        MME[media_mentions<br/><i>LangGraph validated NER<br/>16 entity types</i>]
        MA[media_assertions<br/><i>LangGraph validated S-P-O<br/>MCP contract checks</i>]
        ME[media_embeddings<br/><i>text-embedding-3-small</i>]
    end

    MF --> MM --> MD
    MD -.->|sensor registers partitions| MT
    MT --> MDIA --> MC
    MC --> MME
    MC --> MA
    MC --> ME

    style Bronze fill:#cd7f32,color:#fff
    style Silver fill:#c0c0c0,color:#000
    style Gold fill:#ffd700,color:#000
```

## 2. Partitioning Model

```mermaid
graph LR
    subgraph Unpartitioned["Unpartitioned (run once)"]
        A[media_files] --> B[media_metadata] --> C[media_transcode] --> D[media_documents]
    end

    subgraph Sensor["Auto-Discovery"]
        S[media_document_sensor<br/><i>every 5 min<br/>reads S3, registers partitions</i>]
    end

    subgraph Partitioned["Partitioned by document_id"]
        direction TB
        T[media_transcriptions<br/><i>GPU pod</i>]
        DIA[media_diarization<br/><i>CPU pod</i>]
        CH[media_chunks]
        MEN[media_mentions]
        ASS[media_assertions]
        EMB[media_embeddings]
        T --> DIA --> CH --> MEN
        CH --> ASS
        CH --> EMB
    end

    D --> S --> T

    style Unpartitioned fill:#1a1a2e,color:#fff
    style Sensor fill:#4a90d9,color:#fff
    style Partitioned fill:#2d5016,color:#fff
```

## 3. S3 Storage Layout

```
s3://catalyst-data/
├── bronze/default/media/
│   ├── media_files/data.jsonl
│   └── media_metadata/data.jsonl (not in use?)
├── silver/default/media/
│   ├── media_transcode/data.jsonl
│   ├── media_documents/data.jsonl
│   └── media_chunks/{document_id}/data.json       ← per partition
├── gold/default/media/
│   ├── media_transcriptions/{document_id}/data.json
│   ├── media_diarization/{document_id}/data.json
│   ├── media_mentions/{document_id}/data.json
│   ├── media_assertions/{document_id}/data.json
│   └── media_embeddings/{document_id}/data.json
```

## 4. Transcription Backend Architecture

```mermaid
graph LR
    subgraph Input["Source Media"]
        MP4[video.mp4<br/><i>NFS mount</i>]
    end

    subgraph Extract["Audio Extraction"]
        FFM[ffmpeg<br/><i>-vn -ar 16000 -ac 1<br/>pcm_s16le WAV</i>]
    end

    subgraph Backends["Whisper Backend (config switch)"]
        OV[OpenVINO GenAI<br/><i>WhisperPipeline<br/>GPU: gpu.intel.com/i915<br/>50x realtime</i>]
        FW[faster-whisper<br/><i>CTranslate2<br/>CPU int8<br/>4x realtime</i>]
    end

    subgraph Output["Transcription Output"]
        SEG[Segments<br/><i>start, end, text, words</i>]
        LANG[Language Detection]
    end

    MP4 --> FFM --> OV --> SEG
    FFM -.-> FW -.-> SEG
    OV --> LANG
    FW --> LANG

    style Backends fill:#0e2f4a,color:#fff
```

## 5. Diarization Pipeline

```mermaid
sequenceDiagram
    participant T as media_transcriptions
    participant D as media_diarization
    participant S3 as MinIO S3

    T->>S3: Save transcription<br/>(text, segments, language)
    Note over T: GPU pod completes, freed

    S3->>D: Load transcription
    D->>D: ffmpeg extract audio → WAV
    D->>D: pyannote speaker-diarization-3.1
    D->>D: Align speaker turns to segments<br/>(midpoint matching + majority vote)
    D->>D: Build speaker_text<br/>([SPEAKER_00]: ...)
    D->>S3: Save diarized result<br/>(speaker_text, speakers, segments+speaker)

    Note over D: CPU pod, no GPU needed
    Note over D: If fails, transcription is safe in S3
```

## 6. Catalyst-Data Full Domain Model

```mermaid
graph TB
    subgraph Bronze["BRONZE — Raw Ingestion"]
        direction LR
        CB[congress_bills<br/>congress_members<br/>congress_committees]
        LB[wikileaks_cables<br/>icij_offshore_*<br/>epstein_court_docs]
        MB[media_files<br/>media_metadata]
    end

    subgraph Silver["SILVER — Normalized + Processed"]
        direction LR
        CS[congress_documents<br/>congress_chunks]
        LS[leak_documents<br/>leak_chunks]
        MS[media_transcode<br/>media_documents<br/>media_chunks]
    end

    subgraph Gold["GOLD — ML Extraction"]
        direction LR
        CG[congress_mentions<br/>congress_assertions<br/>congress_embeddings]
        LG[leak_mentions<br/>leak_assertions<br/>leak_embeddings]
        MG[media_transcriptions<br/>media_diarization<br/>media_mentions<br/>media_assertions<br/>media_embeddings]
    end

    subgraph Platinum["PLATINUM — Unified Knowledge Graph"]
        direction LR
        CE[canonical_entities<br/><i>IDF-weighted scoring<br/>cluster coherence<br/>HITL overrides</i>]
        EA[entity_alignments<br/><i>sameAs / possibleSameAs<br/>multi-signal evidence</i>]
        AG[assertion_graph<br/><i>full provenance</i>]
    end

    subgraph Storage["Dual-Write Storage"]
        PG[(PostgreSQL + pgvector)]
        N4J[(Neo4j)]
    end

    subgraph UI["Data Explorer"]
        SE[Streamlit UI<br/><i>data.talos00<br/>12 pages</i>]
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

## 7. K8s Runtime Architecture

```mermaid
graph TB
    subgraph Cluster["Talos Kubernetes Cluster"]
        subgraph NS["catalyst-data namespace"]
            WS[dagster-webserver<br/><i>UI + API</i>]
            DM[dagster-daemon<br/><i>scheduler + sensor</i>]
            PG2[(dagster-postgres)]

            subgraph CodeServers["Code Servers (gRPC :4000)"]
                CS2[congress-data]
                OLS[open-leaks]
                MIS[media-ingest :gpu]
                KGS[knowledge-graph]
                DES[data-explorer :8501]
            end

            subgraph RunJobs["k8s_job_executor Step Pods"]
                TP[transcription pod<br/><i>gpu.intel.com/i915:1<br/>OpenVINO whisper</i>]
                DP[diarization pod<br/><i>CPU only<br/>pyannote</i>]
                TC[transcode pod<br/><i>gpu.intel.com/i915:1<br/>ffmpeg av1_qsv</i>]
                LP[LLM extraction pod<br/><i>mentions + assertions</i>]
            end
        end

        subgraph Storage2["Storage"]
            MINIO[(MinIO S3)]
            NFS2[(TrueNAS NFS<br/><i>192.168.1.36<br/>metube + tubesync + whisper-models</i>)]
        end

        subgraph GPU["GPU Worker Nodes"]
            T02[talos02-gpu<br/><i>14 CPU, 64Gi RAM<br/>4x i915 GPU</i>]
            T06[talos06<br/><i>16 CPU, 64Gi RAM<br/>4x i915 GPU</i>]
        end

        subgraph LLM["catalyst-llm namespace"]
            LIT[LiteLLM Proxy<br/><i>:4000/v1</i>]
        end

        subgraph Secrets["External Secrets (1Password)"]
            S3C[dagster-s3-credentials]
            LLMC[llm-credentials]
            HFC[hf-credentials<br/><i>pyannote model access</i>]
            CGC[congress-data-secrets<br/><i>scoped to congress assets</i>]
        end
    end

    WS --> PG2
    DM --> PG2
    DM --> CodeServers
    DM --> RunJobs

    TP --> NFS2
    TP --> GPU
    TC --> GPU
    DP --> NFS2
    RunJobs --> MINIO
    RunJobs --> LIT

    Secrets -.-> RunJobs

    style NS fill:#1a1a2e,color:#fff
    style Storage2 fill:#2d2d2d,color:#fff
    style GPU fill:#4a0e0e,color:#fff
    style LLM fill:#0e2f4a,color:#fff
```

## 8. Data Models

### Transcription Output (per partition)
```json
{
  "document_id": "media-metube-interview-001",
  "title": "Interview Episode 42",
  "text": "Full concatenated text...",
  "language": "en",
  "language_probability": 0.98,
  "segments": [
    {"start": 0.0, "end": 5.3, "text": "Welcome to the show", "words": [...]}
  ],
  "segment_count": 150,
  "duration_s": 3600.0,
  "transcription_time_s": 72.0,
  "source_path": "/data/metube/youtube.com/interview.mp4"
}
```

### Diarization Output (extends transcription)
```json
{
  "...all transcription fields...",
  "speaker_text": "[SPEAKER_00]: Welcome to the show...\n[SPEAKER_01]: Thanks for having me...",
  "speaker_count": 2,
  "speakers": ["SPEAKER_00", "SPEAKER_01"],
  "segments": [
    {"start": 0.0, "end": 5.3, "text": "Welcome to the show", "speaker": "SPEAKER_00"}
  ],
  "diarization_time_s": 180.0
}
```

### Assertion Output (speech-act predicates)
```json
{
  "subject_text": "Senator Warren",
  "predicate": "claims",
  "predicate_canonical": "claims",
  "object_text": "the bill will reduce costs by 30%",
  "qualifiers": {"time": "during the hearing", "source_attribution": "SPEAKER_01"},
  "confidence": 0.85,
  "negated": false,
  "hedged": true,
  "provenance": {
    "source_document_id": "media-metube-interview-001",
    "chunk_id": "media-metube-interview-001:chunk-5",
    "temporal_start_ms": 45000,
    "temporal_end_ms": 52000,
    "speaker_label": "SPEAKER_01"
  }
}
```
