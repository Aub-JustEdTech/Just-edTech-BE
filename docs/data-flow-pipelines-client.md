# Just EdTech — Data Flow Overview

**Purpose:** High-level overview of how school website content is discovered, ingested into the knowledge base, and retrieved for chat and the district heatmap.

**Legend**
- **Rectangle** = process step
- **Diamond** = decision gate
- **Double-bordered box** = LLM or Embedding API call
- **Cylinder-style step** = data store read/write

---

## End-to-End Handoff

```
School URL
  → Schema crawler (LLM page classify)
  → Candidate archive pages
  → Media link extraction (PDF / audio / video)
  → Ingest (summarize → classify → chunk → embed → batch-tag)
  → S3 + Postgres + Qdrant
  → Chat agent OR Heatmap engine
```

---

## 1. Schema-Based Scraping

Discover meeting-document pages on a school website, then extract media file links. Uses a **schema-driven crawler only** — the LLM classifies each page and ranks which links to follow next. Does **not** include keyword/manual URL discovery.

### Flow diagram

```mermaid
flowchart TD
    START([School website URL]) --> FRONTIER[Build link frontier<br/>Sitemap + navigation links]
    FRONTIER --> BUDGET{Pages left<br/>in budget?}

    BUDGET -->|Yes| FETCH[Fetch next URL<br/>Highest confidence first]
    BUDGET -->|No| HUB{Any document<br/>pages found?}

    FETCH --> DOMAIN{Same domain &<br/>not visited?}
    DOMAIN -->|No| BUDGET
    DOMAIN -->|Yes| FETCHOK{Fetch OK &<br/>content usable?}

    FETCHOK -->|No| BUDGET
    FETCHOK -->|Yes| LLM[[LLM: Classify page<br/>Structured page schema]]

    LLM --> CLASSOK{Classification<br/>succeeded?}
    CLASSOK -->|No| BUDGET
    CLASSOK -->|Yes| HASDATA{Page hosts<br/>target documents?}

    HASDATA -->|No| LINKS{Child link confidence<br/>above threshold?}
    HASDATA -->|Yes| ARCHIVE{Skip archival<br/>pages?}

    ARCHIVE -->|Yes → skip| LINKS
    ARCHIVE -->|No| CANDIDATE[Keep as candidate<br/>URL + type + years]
    CANDIDATE --> LINKS

    LINKS -->|Yes| ENQUEUE[Enqueue child links<br/>Ranked by confidence]
    LINKS -->|No| BUDGET
    ENQUEUE --> BUDGET

    HUB -->|Yes| MEDIA[Extract media links<br/>PDF / audio / video URLs]
    HUB -->|No| FALLBACK[Hub-page fallback<br/>Surface likely hubs]
    FALLBACK --> MEDIA

    MEDIA --> END([Scraped media queue<br/>Ready for ingest])

    classDef llm fill:#2563eb,stroke:#1d4ed8,color:#fff
    classDef decision fill:#f3f4f6,stroke:#374151
    class LLM llm
```

### Decision gates

| Gate | Yes path | No path |
|------|----------|---------|
| Pages left in budget? | Continue crawl | Exit loop → check results |
| Same domain & not visited? | Fetch page | Skip URL |
| Fetch OK & content usable? | Classify with LLM | Skip page |
| Classification succeeded? | Evaluate page content | Skip page |
| Page hosts target documents? | Check archival rule | Enqueue links only |
| Skip archival pages? | Do not add to candidates | Add URL as candidate |
| Child link confidence above threshold? | Add to frontier | Do not enqueue |
| Any document pages found? | Extract media | Use hub-page fallback |

### LLM API calls

| Step | Purpose |
|------|---------|
| **Page classifier** (per visited page) | Is this a document/archive page? Which child links look relevant, and at what confidence? |

### Data stores

| Store | When used |
|-------|-----------|
| In-memory | Crawl state during discovery |
| Postgres (ScrapedMedia) | After media link extraction |

---

## 2. Document Ingest

Turn scraped meeting files into searchable, taxonomy-tagged chunks for chat and the district heatmap.

### Flow diagram

```mermaid
flowchart TD
    START([Scraped media item<br/>File URL + school metadata]) --> YEAR{Year in allowed<br/>range?}

    YEAR -->|No| SKIP([Skip item<br/>Year / duplicate / out of range])
    YEAR -->|Yes| DOWNLOAD[Download & extract text<br/>PDF / DOCX / transcript]

    DOWNLOAD --> DUP{Duplicate<br/>content?}
    DUP -->|Yes| SKIP
    DUP -->|No| STORE[(Store raw file<br/>S3 + Postgres document)]

    STORE --> SUM[[LLM: Summarize doc<br/>Type, date range, summary]]
    SUM --> DOC[[LLM: Classify doc<br/>Entity, meeting type, meeting date]]

    DOC --> MEET{Meeting date<br/>in range?}
    MEET -->|No| SKIP
    MEET -->|Yes| CHUNK[Chunk document<br/>Page-aware chunks]

    CHUNK --> CTX[[LLM: Contextualize chunks<br/>Optional situating sentence]]
    CTX --> EMB[[Embedding API<br/>Vectorize chunks]]

    EMB --> QDRANT[(Index in Qdrant<br/>Vectors + metadata)]
    QDRANT --> BATCH[[LLM: Batch classify chunks<br/>Topics / stage / speakers]]

    BATCH --> OFFTOPIC{Chunk<br/>off-topic?}
    OFFTOPIC -->|Yes → exclude from heatmap| DONE([Ingest complete<br/>Ready for retrieval])
    OFFTOPIC -->|No| AGG[(Update heatmap counts<br/>Postgres aggregates)]
    AGG --> DONE

    classDef llm fill:#2563eb,stroke:#1d4ed8,color:#fff
    class SUM,DOC,CTX,EMB,BATCH llm
```

### Decision gates

| Gate | Yes path | No path |
|------|----------|---------|
| Year in allowed range? | Download file | Skip item |
| Duplicate content? | Skip item | Continue ingest |
| Meeting date in range? | Chunk & index | Skip remaining pipeline |
| Chunk off-topic? | Exclude from heatmap counts | Include in heatmap aggregates |

### LLM API calls

| Step | Purpose |
|------|---------|
| **Document summarizer** | Document type, date range, summary paragraph |
| **Document classifier** | Entity type, doc kind, meeting date, meeting body |
| **Chunk contextualizer** (optional) | 1–2 sentence situating context per chunk |
| **Embedding API** | Vector representations for semantic search |
| **Batch chunk classifier** | Topics, topic tags, action stage, speakers, off-topic flag |
| **Image caption** (optional) | Vision model for embedded PDF images |

### Data stores

| Store | What is stored |
|-------|----------------|
| **S3** | Raw media files, transcripts |
| **Postgres** | Documents, processing jobs, pending classifications, heatmap aggregates |
| **Qdrant** | Chunk vectors + taxonomy/metadata payload |

---

## 3. Retrieval

Two read paths over the same indexed corpus: **conversational Q&A** (Agentic RAG) and **district heatmap** counts with citations.

### Flow diagram

```mermaid
flowchart TD
    START([User request<br/>Chat question or map filters]) --> PATH{Chat Q&A or<br/>Heatmap map?}

    %% Chat path
    PATH -->|Chat| AGENT[[LLM: Agent reasoning<br/>Choose tools or synthesize answer]]
    AGENT --> TOOLS{Needs retrieval<br/>tools?}

    TOOLS -->|Yes| QEMB[[Embedding API<br/>Vectorize user query]]
    QEMB --> SEARCH[(Search knowledge base<br/>Qdrant + document metadata)]
    SEARCH --> BUDGET{Iteration / token<br/>budget left?}

    BUDGET -->|Yes| AGENT
    BUDGET -->|No → force final answer| CITE[Extract citations<br/>Dedupe best sources]

    TOOLS -->|No → answer ready| CITE
    CITE --> CHATEND([Answer + citations])

    %% Heatmap path
    PATH -->|Heatmap| SCHOOLS[(Load active schools<br/>Postgres)]
    SCHOOLS --> COUNT[(Count classified chunks<br/>Qdrant filters by district / topic / timeframe)]

    COUNT --> BREAK{Topic breakdown<br/>requested?}
    BREAK -->|Yes| PERCAT[Per-topic counts<br/>Top category per district]
    BREAK -->|No| DRILL{Citation<br/>drill-down?}
    PERCAT --> DRILL

    DRILL -->|Yes| HYDRATE[(Hydrate citations<br/>Documents + S3 file links)]
    DRILL -->|No| MAPEND([Map data returned])
    HYDRATE --> MAPEND

    classDef llm fill:#2563eb,stroke:#1d4ed8,color:#fff
    class AGENT,QEMB llm
```

### Decision gates — Chat path

| Gate | Yes path | No path |
|------|----------|---------|
| Needs retrieval tools? | Embed query → search Qdrant | Go straight to citations / answer |
| Iteration / token budget left? | Loop back to agent reasoning | Force final answer |

### Decision gates — Heatmap path

| Gate | Yes path | No path |
|------|----------|---------|
| Topic breakdown requested? | Compute per-category counts | Return combined counts |
| Citation drill-down? | Load document details + file links | Return map data only |

### LLM API calls

| Path | Step | Purpose |
|------|------|---------|
| **Chat** | Agent reasoning loop | Decide which tools to call vs. produce final answer (may repeat) |
| **Chat** | Query embedding API | Vectorize the user question for semantic search |
| **Chat** | Answer synthesis | Final response with citations (within agent loop) |
| **Heatmap** | — | **No LLM** — filtered counts and citations from Qdrant + Postgres |

### Data stores

| Store | Role |
|-------|------|
| **Qdrant** | Semantic search (chat) and chunk counts (heatmap) |
| **Postgres** | Document metadata, schools, conversation history |
| **S3** | Presigned URLs for citation file downloads |

---

## Summary: Where LLMs Are Used

| Pipeline | LLM / API touchpoints |
|----------|----------------------|
| **Scraping** | Page classifier (1 call per visited page) |
| **Ingest** | Summarize, classify doc, contextualize chunks, embeddings, batch chunk classify, optional image caption |
| **Retrieval (Chat)** | Agent reasoning, query embeddings, answer synthesis |
| **Retrieval (Heatmap)** | None |

---

*Generated for client review — August 2026*
