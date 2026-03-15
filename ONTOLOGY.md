# LLM-Generated Knowledge Graphs for Proposition Extraction, Entity Concordance, and Spatial Visualization

## Abstract

Large language models have made knowledge graph construction cheaper and more flexible, but they have not made it clean. The real problem is no longer “can I get triples out of text?” The hard part is building a pipeline that turns noisy natural language into **stable propositions, canonical entities, provenance-aware edges, and spatially grounded graph objects** that can be queried, audited, diffused, and visualized. Recent work has shifted the center of gravity from narrowly supervised extraction pipelines toward hybrid systems that combine open extraction, schema induction, canonicalization, entity linking, and post-hoc validation. In parallel, geospatial knowledge graph work has matured around standards like **GeoSPARQL 1.1**, hierarchical spatial indexing such as **H3**, and large cross-domain geo-KGs such as **KnowWhereGraph**, making it realistic to build map-native, graph-native knowledge systems instead of bolting coordinates onto a text graph as an afterthought. ([ACL Anthology][1])

This document lays out the mathematical and linguistic foundations, the pipeline design, the spatial data model, the concordance layer, the visualization architecture, and the diffusion-style methods that matter if your target is not a demo but an actual system.

---

## 1. The actual problem

Most people talk about KG construction as if the pipeline were:

`text -> triples -> graph`

That pipeline is toy-grade. The real system is closer to:

`documents -> discourse units -> proposition candidates -> normalized semantic frames -> entity candidates -> concordance/canonicalization -> relation typing -> provenance + confidence -> temporal/spatial grounding -> graph storage -> embeddings + diffusion/layout indices -> interactive exploration`

The reason is simple: raw SPO triples are not knowledge. They are **claims**. A production KG system must distinguish between:

* the **surface mention** in text,
* the **canonical entity** it refers to,
* the **proposition** being asserted,
* the **source and context** of that assertion,
* the **time and place** where it holds,
* and the **confidence or evidence** supporting it.

Recent LLM-based KG construction work explicitly moved toward this broader view. The EDC framework—**Extract, Define, Canonicalize**—is important because it formalizes a three-stage view: open extraction first, schema definition second, canonicalization third, instead of pretending the schema is known and stable at extraction time. That is the right direction for heterogeneous corpora and evolving ontologies. ([ACL Anthology][1])

---

## 2. Core conceptual split: mentions, entities, propositions, and facts

You need four distinct object types.

### 2.1 Mention

A mention is a span in a source artifact.

Example:
“Apple” in a sentence could refer to the company, the fruit, or a place name.

Formally:

\[
m = (d, s, e, \tau)
\]

where \( m \) is a mention, \( d \) is the document, \( s \) and \( e \) are the span start and end offsets, and \( \tau \) is the local context.

where (d) is the document, (s,e) are span offsets, and (\tau) is local context.

### 2.2 Entity

An entity is a canonical referent, ideally with a stable ID.

\[
e_i \in \mathcal{E}
\]

Examples: `wd:Q312` for Apple Inc., a local ontology node, or an H3 cell-backed region object.

### 2.3 Proposition

A proposition is a normalized semantic unit, not necessarily yet a graph edge.

\[
p = (\text{predicate}, a_1, a_2, \dots, a_n, \sigma)
\]

where (a_i) are argument slots and (\sigma) contains modality, negation, tense, evidentiality, and scope.

This is where plain triples fail: many real propositions are not binary, and a lot of meaning sits in qualifiers.

### 2.4 Fact assertion

A fact assertion is a proposition anchored to source, time, place, and confidence.

\[
f = (p, \text{source}, t, g, c)
\]

where (t) is temporal validity, (g) is geospatial grounding, and (c) is confidence.

If you skip this separation, your graph becomes unrecoverable sludge.

---

## 3. Linguistic foundation: how to extract propositions correctly

If you want a graph that means something, the extraction layer cannot rely on raw next-token vibes. It needs a linguistically informed intermediate representation.

### 3.1 OpenIE is useful but insufficient

Open Information Extraction extracts schema-free tuples from text and remains a central substrate for KG building. A recent survey traces OpenIE from rule-based systems to neural and LLM-era methods, while also emphasizing persistent issues: overlong predicates, underspecified arguments, factuality errors, and evaluation instability. ([ACL Anthology][2])

OpenIE is useful because it gives you broad recall. It is insufficient because:

* it often confuses syntax with semantics,
* it fragments one proposition into several partial tuples,
* it struggles with negation, coordination, and coreference,
* and it typically does not solve canonicalization.

Use it as a **proposal generator**, not the final truth layer.

### 3.2 Semantic role labeling is the better proposition spine

Semantic role labeling identifies predicate-argument structure rather than just text spans around verbs. Modern SRL surveys position SRL as a central semantic layer for downstream reasoning because it captures who did what to whom, when, where, and with what roles. ([arXiv][3])

For your pipeline, SRL gives you:

* normalized argument slots,
* cleaner handling of adjuncts like place/time,
* a bridge from syntax to proposition frames,
* better downstream mapping into n-ary graph structures.

If your endgame is a proposition graph, SRL or frame-semantic structure should be treated as first-class.

### 3.3 AMR is even better when you need graph-native semantics

Abstract Meaning Representation encodes sentence meaning as a rooted directed graph. A recent survey on AMR applications makes the case that AMR remains one of the most useful symbolic intermediate forms for tasks that require explicit semantic structure beyond plain spans or labels. ([ACL Anthology][4])

AMR matters for you because it:

* collapses paraphrases better than raw dependency paths,
* exposes reentrancy and co-reference-like structure,
* maps naturally into graph objects,
* supports proposition decomposition before canonicalization.

The cost is complexity. AMR parsing is slower and noisier than NER+RE, but for a high-value knowledge system it is worth the expense on at least a subset of data.

### 3.4 Mathematical proposition extraction is not the same as ordinary IE

For mathematics or formal/scientific text, the unit of meaning is often not a named entity relation but a **typed proposition**: theorem, definition, proof step, assumption, equivalence, implication, bound, operator action, parameter constraint.

A mathematical pipeline should parse:

* LaTeX / MathML / operator trees,
* discourse markers like “let”, “assume”, “there exists”, “iff”,
* theorem-proof dependencies,
* symbol tables and scoped variable bindings.

Recent work on automated mathematical KGs and LLM-assisted mathematical knowledge management points in this direction: mathematical knowledge needs special treatment for definitions, proofs, and symbolic expressions, not just generic entity-relation extraction. ([arXiv][5])

So for math/scientific corpora, treat propositions as typed logical objects:

\[
\phi ::= P(x_1,\dots,x_n) \mid \phi \land \psi \mid \phi \to \psi \mid \forall x,\phi \mid \exists x,\phi
\]

Then derive KG edges from these objects, not the other way around.

---

## 4. Triple extraction: SPO is the floor, not the ceiling

### 4.1 Why SPO persists

SPO triples remain the lowest-friction export format:
[
(s, p, o)
]
They are simple, composable, and map cleanly into RDF and property graphs.

### 4.2 Why SPO breaks

SPO fails on:

* qualifiers,
* uncertainty,
* modality,
* temporal validity,
* geospatial extent,
* statement provenance,
* event-centric relations,
* and nested claims.

For example, “The company may build a plant near Phoenix by 2027” is not a clean fact triple. It includes modality, location, and future time.

### 4.3 Better representation: qualified assertions

Use a main proposition plus attached qualifiers:

[
\text{assertion} = (s, p, o, Q, \Pi)
]

where (Q) is a set of qualifiers and (\Pi) is provenance.

This is where RDF-star / quoted triples are relevant. W3C’s RDF-star work extends RDF/SPARQL toward quoted triples and statement-about-statement representation, which is exactly what you need for confidence, source, and temporal annotations without ugly full reification everywhere. ([W3C][6])

Bluntly: if your graph stores extracted assertions without provenance and qualifiers, it is barely better than a bag of tuples.

---

## 5. Entity concordance: the most important part nobody respects

The user asked specifically about **entity concordance**, and that is the right instinct. Extraction is easy. Concordance is where systems die.

Entity concordance includes:

1. mention detection,
2. candidate generation,
3. disambiguation / entity linking,
4. cross-source alignment,
5. canonical merge policy,
6. persistent ID management,
7. synonym and alias handling,
8. spatial and temporal disambiguation.

### 5.1 Entity linking

Entity linking maps text mentions to KB entities. ReLiK is a notable 2024 system because it unifies retrieval and reading for entity linking and relation extraction, using a retriever-reader architecture rather than brute-force full-catalog classification. That architecture is especially relevant when your ontology is large and evolving. ([ACL Anthology][7])

Generative EL approaches like EntGPT show that LLMs can improve linking with prompt engineering and instruction tuning, but they are still vulnerable to hallucinated candidates and prompt sensitivity. Use them as a scoring layer, not a sole authority. ([arXiv][8])

### 5.2 Entity alignment across graphs

If you ingest multiple sources, you need entity alignment across KGs. Recent work like LLM-Align uses LLM reasoning over selected attributes and relation neighborhoods, plus multi-round voting to reduce hallucination and positional bias. That is useful, but the main lesson is architectural: **alignment should combine lexical, structural, and attribute evidence**. Do not rely on names alone. ([arXiv][9])

### 5.3 Canonicalization strategy

You need a tiered merge policy.

For each mention-derived entity candidate (u), compute a concordance score against canonical nodes (e_j):

\[
text{score}(u,e_j) =
alpha \cdot s_{\text{name}}

* \beta \cdot s_{\text{context}}
* \gamma \cdot s_{\text{structure}}
* \delta \cdot s_{\text{geo}}
* \epsilon \cdot s_{\text{time}}
\]

Then:

* if score > hard threshold: merge,
* if in gray zone: keep as candidate alias / unresolved,
* if below threshold: create new entity.

This is one place where you absolutely want **human-supervised review queues** for the gray band. Human-supervised KG construction pipelines explicitly combine extraction with downstream fusion and review because fully automatic merge policies still over-collapse entities. ([Webis Downloads][10])

### 5.4 Practical concordance object model

You want at least:

* `Mention`
* `EntityCandidate`
* `CanonicalEntity`
* `Alias`
* `ExternalIdentifier`
* `AlignmentEdge`
* `MergeDecision`
* `EvidenceBundle`

That model keeps the system auditable.

---

## 6. Schema induction versus ontology-first design

This is the fundamental design fork.

### 6.1 Ontology-first

You define the schema first, then extract into it.

Good for:

* narrow domains,
* regulated data,
* geospatial infrastructure,
* enterprise master data.

### 6.2 Schema-later / induction-first

You extract open triples or proposition frames first, then induce relation types and entity classes.

Good for:

* heterogeneous corpora,
* research discovery,
* exploratory systems,
* evolving domains.

EDC is important because it operationalizes a hybrid: open extraction, then schema definition, then canonicalization. That is the right model when you want discovery without giving up long-term graph hygiene. ([ACL Anthology][1])

For your use case, I would use a **two-tier ontology**:

* **Tier 1 core ontology**: document, source, assertion, entity, place, event, time, geometry, cell, claim, evidence
* **Tier 2 induced ontology**: domain-specific predicates and fine-grained types discovered from corpus structure

This keeps the graph stable while still allowing semantic growth.

---

## 7. Spatial grounding: where most “knowledge graphs” are embarrassingly weak

If you want an HGO spatial visualization engine, the graph must treat **space as first-class structure**, not a text attribute.

### 7.1 What makes a graph geospatial

A geospatial KG contains explicit georeferences such as coordinates, place names, geometries, or well-defined spatial relations, with the primary goal of modeling how entities relate in space. Recent overviews emphasize that GeoKGs are useful precisely because they integrate diverse geospatial data sources and support symbolic plus subsymbolic GeoAI workflows. ([arXiv][11])

### 7.2 GeoSPARQL 1.1 is the baseline standard

GeoSPARQL 1.1 extends the original standard with more geometry support, spatial measurement properties, SHACL validation support, and a W3C profile framing. If you want interoperability and sane semantics, ignore homebrew geospatial predicates and start from GeoSPARQL. ([Open Geospatial][12])

### 7.3 H3 is the practical indexing substrate

H3 partitions the world into hierarchical hexagonal cells and exposes operations for point-to-cell conversion, parent-child relationships, neighborhoods, and boundaries. That makes it an ideal lattice for:

* spatial aggregation,
* multi-resolution visualization,
* concordance around place ambiguity,
* graph diffusion over adjacency. ([H3Geo][13])

A good spatial KG stores both:

* exact geometry when available,
* H3 cell cover(s) for indexing and visualization.

### 7.4 Place matching and conflation are hard

A 2024 semantic-spatial data conflation paper directly addresses the fact that place matching is a key challenge in KGs with georeferenced location nodes. That matters because place linking cannot rely on string similarity alone; it needs semantic and spatial awareness together. ([MDPI][14])

That gives you a good merge scoring function for place entities:

[
\text{place_score}(u,e) =
\lambda_1 \cdot s_{\text{toponym}}

* \lambda_2 \cdot s_{\text{admin-hierarchy}}
* \lambda_3 \cdot s_{\text{geometry-overlap}}
* \lambda_4 \cdot s_{\text{contextual-type}}
  ]

### 7.5 Spatio-temporal graph construction

Recent spatio-temporal KG work and surveys stress that once space and time are explicit, the graph often stops being a triple graph and becomes a richer tuple model where facts carry both temporal and spatial annotation. That is the right direction for disaster, mobility, environmental, and event knowledge systems. ([AGILE-GISS][15])

For your system, a fact should often be a 5-tuple or qualified assertion:

[
(subject, predicate, object, t, g)
]

or more realistically:

[
(subject, predicate, object, \text{qualifiers}, \text{provenance})
]

where qualifiers include time and geometry.

---

## 8. Diffusion techniques: what actually belongs in your stack

“Spatial data diffusion techniques” can mean three different things. People often blur them together. Don’t.

### 8.1 Diffusion for manifold learning and spatialization

This is the useful one for visualization. Diffusion maps build a Markov process over local similarities and embed nodes according to diffusion distance. They are valuable when you want a layout that respects multi-hop relational structure rather than just local force repulsion.

Given graph affinity matrix (W), define transition matrix (P = D^{-1}W).
Diffusion distance after (t) steps between nodes (i,j) is:

[
D_t^2(i,j) = \sum_k \frac{(P^t_{ik} - P^t_{jk})^2}{\phi_0(k)}
]

Embedding via leading eigenvectors of (P) gives a low-dimensional coordinate system preserving diffusion geometry.

For a KG explorer, this is useful for:

* concept-space overview,
* cluster separation,
* topic/ontology branch visualization,
* robust initialization before local force layout.

### 8.2 Diffusion over graphs for denoising or propagation

This is message propagation / graph diffusion. It is useful for:

* confidence smoothing,
* label propagation,
* weak supervision spread,
* candidate alignment expansion,
* neighborhood-aware scoring.

You can define:

[
H^{(t+1)} = \alpha P H^{(t)} + (1-\alpha)H^{(0)}
]

for node-state propagation. In practice, this is useful for concordance and community-aware ranking.

### 8.3 Generative diffusion models on graphs

This is currently more relevant to graph generation, completion, or learned embedding spaces than to interactive knowledge visualization. There are heterogeneous graph diffusion models and diffusion-based KG embedding papers, but for an HGO visualization engine this is not your first priority unless you want generative completion or uncertainty-aware graph synthesis. ([arXiv][16])

So the blunt answer:

* **For visualization:** use diffusion maps / spectral methods / manifold learning.
* **For graph inference:** use graph diffusion / propagation.
* **For generation/completion:** consider generative diffusion only later.

---

## 9. A concrete architecture for your pipeline

## 9.1 Ingestion layer

Inputs:

* PDFs, HTML, Markdown, CSV, GeoJSON, shapefiles, APIs
* optionally satellite / raster metadata
* optionally Wikidata / OSM / domain KBs

Outputs:

* normalized document objects
* chunk graph
* source metadata and provenance

### 9.2 Linguistic analysis layer

Stages:

1. sentence segmentation
2. dependency parse
3. NER / mention detection
4. coreference
5. SRL / frame parsing
6. AMR parse on high-value segments
7. OpenIE as recall-oriented backup

Outputs:

* mention spans
* predicate-argument frames
* proposition candidates
* discourse links

### 9.3 LLM extraction layer

Use an LLM for:

* open triple generation,
* schema suggestion,
* relation normalization,
* entity type inference,
* explanation / rationale generation,
* contradiction detection.

But do not let it directly write to canonical graph tables.

Instead:

* produce JSON candidates,
* validate structure,
* cross-check against deterministic parsers and retrievers,
* score confidence.

This is essentially the lesson of recent LLM+KG construction work: the model is powerful in open extraction and schema assistance, but canonicalization and fusion still need explicit post-processing. ([ACL Anthology][1])

### 9.4 Concordance and fusion layer

Subcomponents:

* lexical candidate generator
* embedding retrieval
* structural neighborhood scorer
* geospatial conflation scorer
* temporal overlap scorer
* LLM adjudicator for ambiguous cases
* human review queue

Store:

* `sameAs`, `possibleSameAs`, `overlapsWith`, `locatedIn`, `derivedFrom`, `mentions`, `asserts`

### 9.5 Spatial grounding layer

* place mention recognition
* toponym disambiguation
* external KB linking
* geometry retrieval
* H3 covering / cell assignment
* spatial relation derivation:

  * intersects
  * within
  * contains
  * adjacentTo
  * near
  * overlaps
* temporal grounding for event facts

### 9.6 Graph storage layer

Use dual representation:

**Property graph**

* better for app ergonomics and traversal
* Neo4j, Memgraph, or graph-on-OLAP stack

**RDF/GeoSPARQL**

* better for semantics, interoperability, reasoning, standards

If you can afford it, keep both:

* property graph for app runtime,
* RDF/GeoSPARQL mirror for standards-based query and exchange.

### 9.7 Embedding and layout layer

Store:

* text embeddings for entities, assertions, documents
* graph embeddings for structure-aware retrieval
* H3 cell vectors / region embeddings
* diffusion map coordinates
* local force layout coordinates
* cluster IDs / community labels

### 9.8 Visualization layer

Three coordinated views:

1. **Map view** — geo-grounded entities and regions
2. **Graph view** — semantic relations, provenance, concordance edges
3. **Proposition view** — assertion-centric evidence browser

This is how you avoid the usual failure mode where maps and graphs are separate toys.

---

## 10. HGO spatial visualization engine: recommended model

Here is the system I would actually build.

## 10.1 Core object model

### Entity node

```text
Entity {
  id
  canonical_name
  types[]
  aliases[]
  descriptions[]
  embeddings[]
  external_ids[]
  confidence
}
```

### Assertion node

```text
Assertion {
  id
  subject_id
  predicate_id
  object_id
  qualifiers
  confidence
  source_ids[]
  extraction_method
  validity_time
  validity_geometry
}
```

### Geometry / region node

```text
GeoObject {
  id
  geometry_wkt
  centroid
  h3_cells[]
  admin_hierarchy[]
  spatial_type
}
```

### Mention node

```text
Mention {
  id
  document_id
  span_start
  span_end
  text
  linked_entity_candidates[]
}
```

### Provenance node

```text
Source {
  id
  uri
  title
  date
  author
  chunk_id
  modality
}
```

## 10.2 Edge families

* `MENTIONS`
* `LINKS_TO`
* `ASSERTS`
* `ABOUT`
* `LOCATED_IN`
* `INTERSECTS`
* `ADJACENT_TO`
* `SAME_AS`
* `POSSIBLE_SAME_AS`
* `DERIVED_FROM`
* `SUPPORTED_BY`
* `CONTRADICTED_BY`

### 10.3 Layout system

Use a hybrid coordinate strategy:

[
x_i = \eta_1 x_i^{\text{geo}} + \eta_2 x_i^{\text{diffusion}} + \eta_3 x_i^{\text{force}}
]

This means:

* geo-grounded nodes keep map coordinates,
* abstract entities get diffusion-map initialization,
* local force layout refines neighborhood readability.

In practice:

* nodes with strong geometry stay anchored to map projection,
* non-spatial concept nodes float in semantic halo layers,
* assertion nodes can be collapsed or expanded on demand.

### 10.4 Multi-resolution rendering

H3 gives you natural level-of-detail:

* low zoom: aggregate by cell
* mid zoom: cluster by community within cell
* high zoom: show entities and assertions
* very high zoom: show provenance and mention spans

That is far more stable than raw D3 force chaos.

---

## 11. Mathematical formulation of the full pipeline

Let documents be (D = {d_1,\dots,d_n}).

### 11.1 Proposition extraction

A parser/extractor defines:

[
E_\theta : d \mapsto {p_1,\dots,p_k}
]

where each proposition candidate (p_i) is a semantic frame.

### 11.2 Entity candidate generation

For each mention (m), candidate retrieval gives:

[
C(m) = {e_1,\dots,e_r}
]

using lexical, embedding, and KB retrieval.

### 11.3 Concordance scoring

Define feature vector:

[
\Phi(m,e) = [s_{\text{name}}, s_{\text{context}}, s_{\text{type}}, s_{\text{structure}}, s_{\text{geo}}, s_{\text{time}}]
]

and scorer:

[
P(e \mid m) = \text{softmax}(w^\top \Phi(m,e))
]

### 11.4 Assertion confidence

For extracted assertion (a):

[
c(a) = \sigma(
\alpha_1 c_{\text{extractor}}

* \alpha_2 c_{\text{linking}}
* \alpha_3 c_{\text{schema-fit}}
* \alpha_4 c_{\text{source-quality}}
* \alpha_5 c_{\text{cross-source-agreement}}
  )
  ]

### 11.5 Graph diffusion

For node labels or confidence propagation:

[
F^{(t+1)} = \alpha D^{-1}AF^{(t)} + (1-\alpha)Y
]

where (A) is adjacency, (D) degree matrix, (Y) seed labels or confidences.

### 11.6 Spatial aggregation

Let (h(g)) be H3 cover of geometry (g).
For cell (c), aggregate local entity mass:

[
M(c) = \sum_{e \in \mathcal{E}} \mathbf{1}[c \in h(g_e)] \cdot w_e
]

This supports heatmaps, density overlays, and multiscale query acceleration.

---

## 12. Data model recommendations

### 12.1 Use assertion-centric storage

Store assertions as first-class nodes or quoted triples, not just edges.

Reason:

* provenance is easier,
* qualifiers are easier,
* contradiction tracking is easier,
* evidence browsing is easier.

### 12.2 Keep raw and canonical layers separate

You want:

* **raw extraction graph**
* **canonical fused graph**
* **review/audit graph**

Never collapse them into one table. That is amateur hour.

### 12.3 Keep both symbolic and vector indices

You need:

* symbolic graph query,
* full-text search,
* vector search,
* spatial index,
* H3 cell index,
* temporal index.

This is why serious systems end up polyglot:

* graph DB
* object store
* vector DB or pgvector
* OLAP / lakehouse
* geospatial engine

---

## 13. Evaluation: how to tell whether the system works

Do not evaluate only triple precision/recall. That is too shallow.

You need at least six metrics families.

### 13.1 Proposition quality

* exact proposition match
* argument role accuracy
* negation/modality handling
* event completeness

### 13.2 Entity concordance quality

* mention-level linking accuracy
* cluster purity
* merge/split error rate
* cross-source alignment accuracy

### 13.3 Schema quality

* relation type stability
* ontology redundancy
* canonical relation collapse rate

### 13.4 Provenance quality

* source traceability completeness
* assertion-to-evidence coverage

### 13.5 Spatial grounding quality

* toponym disambiguation accuracy
* geometry correctness
* H3 assignment correctness
* spatial relation correctness

### 13.6 Visualization quality

* neighborhood preservation
* cluster stability across zoom levels
* user task completion for search/explanation
* explanation path readability

Benchmarks like OpenIE surveys and BenchIE-related work make it clear that evaluation in extraction remains messy and benchmark-sensitive; you should expect to build your own domain evaluation set if this is meant to be useful. ([ACL Anthology][2])

---

## 14. Recommended implementation strategy

## Phase 1: proposition and concordance backbone

Build:

* chunker
* NER + coref
* SRL / AMR subset
* LLM extraction with structured JSON
* candidate entity linker
* canonical entity store
* assertion store with provenance

Ignore fancy visualization at first. If the graph is semantically rotten, the UI is lipstick on a corpse.

## Phase 2: spatial grounding

Add:

* place mention recognition
* toponym linking
* GeoSPARQL-compatible geometry model
* H3 covering
* spatial relation derivation
* map-backed query

## Phase 3: ontology induction and review

Add:

* relation clustering
* schema suggestion
* synonym collapse
* human review console
* conflict detection

## Phase 4: HGO visualization engine

Add:

* diffusion-map global embedding
* force-local refinement
* map-graph coordinated views
* provenance expansion
* time slider
* cluster and cell aggregations

## Phase 5: mathematical proposition pipeline

Add:

* LaTeX parser
* formula entity graph
* theorem/definition/proof object model
* symbol table tracking
* logical dependency graph

---

## 15. Recommended stack

If you want a realistic technical direction:

### Extraction / NLP

* spaCy / Stanza for baseline NLP
* transformer NER / coref
* SRL model
* AMR parser on selected docs
* LLM for structured extraction and adjudication

### Storage

* PostgreSQL + PostGIS for source/provenance/spatial tables
* graph DB for semantic traversal
* object store for raw docs
* vector index for retrieval
* optional RDF triple store if GeoSPARQL/SPARQL matters heavily

### Spatial

* H3
* PostGIS / Sedona
* GeoSPARQL vocabulary model

### Visualization

* deck.gl / MapLibre for map
* D3 / WebGL graph layer
* server-side precomputed diffusion coordinates
* incremental force refinement client-side

### Retrieval

* dense retrieval for entity candidates
* graph neighborhood retrieval
* hybrid lexical + vector + spatial search

---

## 16. Failure modes you should plan for

### 16.1 Hallucinated predicates

LLMs invent relation names and subtly drift schema. This is why post-hoc schema normalization is mandatory. ([ACL Anthology][1])

### 16.2 Over-merging entities

Common with aliases, organizations, place names, and person names.

### 16.3 Under-merging entities

You end up with ten nodes for one real referent.

### 16.4 Place ambiguity

“Springfield” is not a place; it is a debugging problem.

### 16.5 Provenance loss

If a canonical edge cannot tell you where it came from, it should not be trusted.

### 16.6 Spatial false precision

Do not assign point coordinates when the text only supports coarse region-level grounding.

### 16.7 Visualization lies

Pretty force layouts often imply semantics that are not really there. That is why map anchoring, diffusion initialization, and visible provenance matter.

---

## 17. What I would build for your specific goal

Given your stated interest, I would build this exact pipeline:

### Layer A — proposition graph

* SRL/AMR-first semantic extraction
* LLM-assisted open triple capture
* assertion-centric storage
* provenance and evidence bundles

### Layer B — concordance graph

* entity linking to external KBs where possible
* local canonical IDs everywhere else
* `sameAs` / `possibleSameAs` review model
* geospatial conflation for place entities

### Layer C — geo-semantic graph

* GeoSPARQL-compliant geometry model
* H3 multiresolution cell cover
* time-aware assertions
* region and event objects

### Layer D — spatial visualization engine

* map-anchored geo entities
* semantic halo for non-geo concepts
* diffusion-map embedding for overview
* force-local refinement for detail
* assertion provenance drill-down
* conflict and contradiction overlays

### Layer E — mathematical/linguistic proposition engine

* theorem / definition / lemma extraction
* formula symbol graph
* implication and dependency edges
* proof provenance
* optional conversion to typed logical forms

That gives you a system that can ingest text, derive propositions, align entities, ground them in space, and let users explore both **where** and **why** something exists in the graph.

---

## 18. Bottom line

The strongest current pattern is not “LLM writes triples directly into Neo4j.” That’s junk architecture.

The right pattern is:

1. **extract open propositions**,
2. **define or induce schema**,
3. **canonicalize entities and predicates**,
4. **attach provenance, time, and geometry**,
5. **store assertions separately from canonical facts**,
6. **use spatial indexing and standards from day one**,
7. **spatialize the graph with diffusion/spectral structure plus geo anchors**,
8. **keep humans in the merge loop where ambiguity is real**.

That direction is consistent with current LLM-based KG construction work, entity linking/alignment advances, and geospatial KG practice around GeoSPARQL, H3, and large-scale geo knowledge integration. ([ACL Anthology][1])

[1]: https://aclanthology.org/2024.emnlp-main.548.pdf?utm_source=chatgpt.com "Extract, Define, Canonicalize: An LLM-based Framework ..."
[2]: https://aclanthology.org/2024.findings-emnlp.560.pdf?utm_source=chatgpt.com "A Survey on Open Information Extraction from Rule-based ..."
[3]: https://arxiv.org/html/2502.08660v1?utm_source=chatgpt.com "Semantic Role Labeling: A Systematical Survey"
[4]: https://aclanthology.org/2024.emnlp-main.390.pdf?utm_source=chatgpt.com "A Survey of AMR Applications"
[5]: https://arxiv.org/html/2505.13406v1?utm_source=chatgpt.com "The automated mathematical knowledge graph based on ..."
[6]: https://www.w3.org/2024/10/proposed-rdf-star-wg-charter.html?utm_source=chatgpt.com "RDF-star Working Group Charter"
[7]: https://aclanthology.org/2024.findings-acl.839.pdf?utm_source=chatgpt.com "ReLiK: Retrieve and LinK, Fast and Accurate Entity ..."
[8]: https://arxiv.org/abs/2402.06738?utm_source=chatgpt.com "EntGPT: Linking Generative Large Language Models with Knowledge Bases"
[9]: https://arxiv.org/abs/2412.04690?utm_source=chatgpt.com "LLM-Align: Utilizing Large Language Models for Entity Alignment in Knowledge Graphs"
[10]: https://downloads.webis.de/publications/papers/gohsen_2024a.pdf?utm_source=chatgpt.com "Human-Supervised Knowledge Graph Construction from ..."
[11]: https://arxiv.org/abs/2405.07664?utm_source=chatgpt.com "Geospatial Knowledge Graphs"
[12]: https://opengeospatial.github.io/ogc-geosparql/geosparql11/?utm_source=chatgpt.com "GeoSPARQL 1.1"
[13]: https://h3geo.org/docs/?utm_source=chatgpt.com "Introduction | H3"
[14]: https://www.mdpi.com/2220-9964/13/4/106?utm_source=chatgpt.com "A Semantic-Spatial Aware Data Conflation Approach for ..."
[15]: https://agile-giss.copernicus.org/articles/5/37/2024/agile-giss-5-37-2024.pdf?utm_source=chatgpt.com "Constructing Spatio-temporal Disaster Knowledge Graph ..."
[16]: https://arxiv.org/pdf/2501.02313?utm_source=chatgpt.com "DiffGraph: Heterogeneous Graph Diffusion Model"
