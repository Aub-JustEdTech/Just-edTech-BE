# Just EdTech — Platform Hand-off Document

**Prepared for:** Client stakeholders
**Prepared by:** Engineering team
**Date:** September 2026
**Audience:** Non-technical — no coding background required

---

## 1. What This Platform Does, in Plain English

Just EdTech automatically watches public school district websites, finds their
board meeting records (agendas, minutes, recordings), reads and understands
what's in them, and turns that into two things a human can use immediately:

1. **A chatbot** that answers plain-English questions like *"Which districts
   discussed book challenges this year?"* and gives you the exact source
   document and quote it came from.
2. **A live map and report generator** that shows, at a glance, how many
   districts are discussing a given topic (e.g. curriculum policy, parental
   rights, budget issues) over a chosen time window — with drill-down to the
   original meeting excerpt.

Everything below this point is already built and running. This document
explains what it does, in what order it happens, and the handful of
deliberate engineering trade-offs made along the way so the system meets
real-world cost, accuracy, and scale constraints.

---

## 2. The Journey a Piece of Content Takes

Think of it as an assembly line with four stations:

```
 Find the pages  →  Read the documents  →  Ask the chatbot  →  See it on the map
 (Discovery)         (Ingestion)             (Chat Q&A)          (Heatmap/Reports)
```

### Station 1 — Discovery: "Where do we look?"
Given a school district's website, the system automatically searches for the
page that lists board meetings (agendas/minutes archives). It checks the
site's sitemap, its navigation menus, and known URL patterns, then ranks the
most likely candidate pages. A person on our team reviews and confirms the
right page(s) once per district — after that, the system remembers it and
never needs to re-discover it.

### Station 2 — Ingestion: "Read and understand it"
Once a page is confirmed, the system periodically checks it for new meeting
files (PDFs, Word docs, audio/video recordings, YouTube links) and:
- Downloads and extracts the readable text (transcribing audio/video with
  timestamps and speaker labels where applicable)
- Summarizes each document and figures out what kind of meeting it's from
  and when
- Breaks it into smaller, searchable pieces ("chunks")
- Tags each piece with the topics it discusses (e.g. curriculum, budget,
  parental rights, transgender policy, book challenges)
- Files it away in a searchable index

Duplicate files and irrelevant content are automatically filtered out, and
nothing outside the configured date range is processed — this keeps costs
predictable and the index clean.

### Station 3 — Chat Q&A
Anyone using the chatbot can ask a natural-language question. The system
searches only the indexed, topic-tagged content, composes an answer, and
always attaches citations — the source document, page/timestamp, and a
quoted snippet — so every answer is independently verifiable, never a
black-box claim.

### Station 4 — Heatmap & Reports
The map view shows, per district and per topic, how much relevant discussion
happened in a chosen time window — with the option to click into a district
and read the actual quoted excerpts. On top of that, the system can generate
a polished, downloadable **PDF report** answering a fixed set of
stakeholder-defined research questions per client (for example: *"Which
districts have the highest volume of book challenges?"* or *"Summarize
recurring governance challenges over the past 24 months."*) — this runs as a
background job so it doesn't block anyone's work while the report compiles.

---

## 3. Feature Inventory

| Area | What's implemented | Why it matters to you |
|---|---|---|
| **Automated discovery** | Finds board-meeting-archive pages on any district website via sitemap/navigation crawling, ranks candidates, supports human confirmation | No manual URL-hunting; scales to hundreds of districts |
| **Continuous monitoring** | Re-checks confirmed pages for new postings on a schedule (or on-demand "Scrape all") | New meetings show up automatically, without re-doing discovery |
| **Document understanding** | Reads PDFs, Word docs, Excel, PowerPoint, and audio/video (with automatic transcription) | One system handles every format districts actually publish in |
| **Topic tagging** | Every piece of content is automatically classified by topic/subtopic and by "off-topic vs. relevant" | Powers both the chatbot's accuracy and the heatmap's counts |
| **Conversational chatbot** | Multi-turn conversations, remembers context, always cites its sources with a document link and quoted snippet | Trustworthy, verifiable answers — not a black box |
| **District heatmap** | Live counts of relevant discussion per district/topic/time-window, with drill-down to source quotes | Visual, at-a-glance monitoring across an entire state or region |
| **Stakeholder PDF reports** | On-demand, downloadable reports answering a fixed set of pre-agreed research questions per client | Board-ready deliverable, no manual compilation |
| **Multi-organization support ("multi-tenancy")** | Each client's data, chatbot, users, and reports are fully isolated from every other client on the same platform | One platform, safely serves multiple clients with completely separate data |
| **User accounts & permissions** | Secure login, admin invitations, role-based access (regular user vs. tenant admin vs. super admin) | Only authorized people see a given client's data |
| **Usage & cost tracking** | Every chatbot answer's cost is tracked automatically; daily and monthly usage/cost reports per client | Full visibility into running costs, no surprises on the bill |
| **Processing visibility** | Real-time status of every document as it moves through the pipeline (downloading → reading → indexing) | Operations team can see exactly what's happening and troubleshoot fast |

---

## 4. Architecture Decisions Made to Meet Client Requirements

These are the handful of judgment calls the engineering team made, and why —
each one exists because a real requirement or constraint (cost, accuracy,
scale, or client-specific data) forced a choice.

### 4.1 Discovery is split into two steps, with a human checkpoint in between
**Requirement it satisfies:** accuracy at scale, without runaway automation costs.
School websites are wildly inconsistent — there's no universal way to find
"the meetings page." Rather than fully automating this (which risks silently
missing or mis-identifying pages) or fully manual (which doesn't scale), we
built an automated *candidate finder* and put a **one-time human confirmation
step** before a URL is trusted. After that one confirmation, the URL is
monitored forever automatically — no repeated manual work per district.

### 4.2 The system never re-processes something it's already seen
**Requirement it satisfies:** predictable, controlled cost as the number of
districts grows.
Every downloaded file and every transcription is checked against what's
already been processed before any money is spent on it (transcription costs
are real — roughly $0.23 per hour of audio). This "pay once" rule is applied
per client, so re-checking a page for new content never re-charges for old
content, even as we scale to hundreds of districts.

### 4.3 Transcripts are never flattened to plain text
**Requirement it satisfies:** the ability to cite a precise timestamp/speaker
in an audio or video meeting recording.
Timestamps and who-said-what are preserved permanently in the stored data.
If they were ever discarded to save space, the only way to recover them
would be to pay for the recording to be transcribed again. We chose to keep
the richer (slightly larger) format from day one to guarantee the chatbot
can always cite "at 14:32, Board Member X said…" rather than just a vague
paragraph.

### 4.4 Cost-aware transcription, cheapest option first
**Requirement it satisfies:** budget control on potentially thousands of
hours of meeting recordings.
Before ever paying for automated transcription, the system checks (in
order): is this already processed? Does YouTube already provide free
captions? Is the recording within an allowed length? Only after all of
those checks fail does it use a paid transcription service. This ordering
alone avoids the large majority of transcription spend.

### 4.5 Multi-tenancy — one platform, fully separated client data
**Requirement it satisfies:** serving multiple, unrelated clients (e.g. a
Massachusetts-focused policy tracker and a California district-analytics
client) from a single platform without any cross-contamination of data,
chatbots, or reports.
Every record in the system — every document, chatbot, conversation, and
report — is tagged to a specific client ("tenant") and every request is
checked against that tag before any data is returned. This was a foundational
decision made early, because retrofitting isolation after the fact is far
riskier than building it in from the start.

### 4.6 Heavy jobs run in the background, never blocking the user
**Requirement it satisfies:** a responsive product even though some jobs
(scraping a whole district, transcribing a 3-hour recording, generating a
PDF report) can take minutes.
Anything slow is handed off to a background worker the moment it's
requested, and the person who requested it can check progress or just come
back later for the finished result (a report download, a completed scrape).
The screen never "hangs" waiting on a multi-minute job.

### 4.7 Configurable "how aggressively do we rank pages" switch
**Requirement it satisfies:** a cost/accuracy dial that can be turned without
a code change.
Finding the right page on a website can be done cheaply with keyword
matching, or more accurately (at a small AI cost per page) using an AI
reviewer. Both modes are built in and switchable via a single setting — so if
a client needs higher accuracy for a tricky website, or we need to control
cost during a large rollout, that's a configuration change, not new
development.

### 4.8 Off-topic content is automatically excluded from the map and reports
**Requirement it satisfies:** the heatmap and reports must reflect genuine
policy discussion, not noise (e.g. a routine cafeteria menu update should
never count as a "curriculum" hit).
Every piece of content is checked for topical relevance before it's counted
toward the heatmap, while still being kept in the searchable index for the
chatbot. This keeps the visual analytics trustworthy at a glance.

### 4.9 Bulk operations are capped, not unlimited
**Requirement it satisfies:** protecting shared infrastructure so one large
client action can't slow down or break the system for everyone.
Actions like "re-scrape every district for this client" are capped to a safe
batch size per run; anything beyond the cap is automatically picked up on the
next scheduled run rather than overwhelming the system all at once.

---

## 5. Current Scope & Honest Limitations

To set expectations clearly:

- **Report questions are fixed per client**, agreed upon in advance (e.g. the
  7 questions currently configured for the Massachusetts client, 7+ for
  California). Adding or changing a question is a small, quick configuration
  change — not a rebuild — but it isn't yet a self-service feature for
  end-users.
- **New districts still need a one-time confirmation step** before ongoing
  monitoring starts (see §4.1). This is a few minutes of review per district,
  once.
- **Nightly automatic re-scraping is currently run manually/on-demand**
  rather than fully unattended, so the team can watch resource usage while
  the number of districts scales up. Flipping it to a fully automatic
  nightly schedule is a configuration change once we're comfortable with
  volume.
- **Supported file types today:** PDF, Word, Excel, PowerPoint, and
  audio/video (including YouTube). Anything outside these formats is not
  yet processed.

None of the above are technical blockers — they are intentional "walk before
we run" choices while the system proves itself at increasing scale.

---

## 6. Glossary (Plain-English)

| Term | Meaning |
|---|---|
| **Tenant** | A client organization using the platform; each has fully separate data |
| **Ingestion** | The read → understand → index process for a document or recording |
| **Chunk** | A small, searchable piece of a larger document |
| **Citation** | The exact source (document + snippet/timestamp) behind a chatbot answer |
| **Topic tag** | An automatically-assigned label describing what a piece of content is about |
| **Heatmap** | The map/dashboard view of how much relevant discussion is happening, by district and topic |
| **Background job** | A task the system runs behind the scenes (e.g. generating a report) so the user isn't stuck waiting |

---

## 7. Where to Go for More Detail

- **Live API documentation:** available at `/docs` on the running server (for developers/integrators)
- **Data flow diagrams:** `docs/data-flow-pipelines-client.md` (visual, step-by-step diagrams of every pipeline described above)
- **Billing/cost details:** `docs/TOKEN_BILLING_SYSTEM.md`

---

*This document reflects the state of the platform as of September 2026 and
will be updated as new capabilities are added.*
