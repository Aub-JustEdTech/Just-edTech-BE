# Golden Dataset — Sample Answers for Client Validation

**Generated**: 2026-09-02
**Corpus**: Tenant 2, 20,037 classified chunks across 394 MA school districts
**Data caveat**: The local corpus is a historical ingest (mostly 2023-2024 meeting
minutes). The classifier's `topic_tags` subtopics drift from the current
taxonomy (`lgbtq.gender_identity` vs `lgbtq.gender_identity_discussion`), so
queries route through the coarse `topics` field where possible. These are
**sample answers for format validation** — not production truth. The agent's
retrieval path is validated end-to-end; the data will populate once the
corpus is re-classified against the current taxonomy and fresh 2025-2026
agendas are ingested.

---

## Q1. Since Sept 2025, which districts have discussed comprehensive sex education as part of the agenda?

**No districts in the current corpus have discussed comprehensive sex
education on an agenda since September 2025.**

The knowledge base contains 20,037 classified chunks across 394
Massachusetts school districts. Filtering for the `sexed` topic category
(6 chunks total) restricted to `Agenda` document types with a meeting
date of 2025-09-01 or later returns zero matches. The few
sex-education-tagged chunks in the corpus predate the window (e.g. a
Brewster agenda item from October 2025 references a "Curriculum Opt
Out" policy but is tagged `sexed.parental_notification`, not
`comprehensive`).

*This answer will populate once the corpus is re-classified against the
current taxonomy and fresh 2025-2026 agendas are ingested.*

---

## Q2. In the last twelve months, identify any districts with sex education curriculum changes on their agenda.

**No districts in the current corpus show sex education curriculum
changes on an agenda in the last 12 months.**

Two retrieval paths were tried:

- `topic_categories=["sexed"]` + `meeting_doc_types=["Agenda"]` +
  change-related `action_stages` (Motion Made, Vote, Policy First
  Reading, Policy Adoption) + date range 2025-09-02 → 2026-09-02 →
  0 chunks.
- `action_types=["instruction_reduced", "instruction_eliminated"]` +
  `meeting_doc_types=["Agenda"]` + same date range → 0 chunks.

The `action_types` values `instruction_reduced` (1 chunk) and
`instruction_eliminated` (3 chunks) exist in the corpus but not on
agenda documents in the last 12 months.

*This answer will populate once the corpus is re-classified and recent
agendas are ingested.*

---

## Q3. Summarize all curriculum censorship efforts discussed this year.

**No curriculum censorship efforts appear in the corpus for the current
calendar year (2026).**

Filtering `topics=["curriculum_censorship"]` (3 chunks total
corpus-wide) and `topic_categories=["censorship"]` (3 chunks total)
with `meeting_date_from="2026-01-01"` returns zero matches. The
censorship-tagged chunks in the corpus are from 2024-2025:

- **Wachusett** — 2024-01-08 agenda item tagged
  `censorship.curriculum_material_challenge`, discussing social media
  policy and criteria for "removal of unacceptable" public comments.
- **Palmer** — 2025-03-28 DESE Final IMR Report, p.10, tagged
  `curriculum.review` + `lgbtq.gender_identity`.

*This answer will populate with fresh 2026 data.*

---

## Q4. Which districts are experiencing the highest volume of book challenges?

**Based on the current corpus, book-challenge activity is minimal and
concentrated in two districts:**

| District | State | Chunks matching | Source |
|---|---|---|---|
| Wachusett | MA | 1 | 2024-01-08 Agenda, p.24 (`censorship.curriculum_material_challenge`) |
| Palmer | MA | 1 | 2025-03-28 DESE IMR Report, p.10 (`curriculum.review`) |

**Wachusett** — The January 8, 2024 agenda packet (item #1402) includes
a discussion of social-media policy and "criteria for posting & removal
of unacceptable" content, classified as a
`curriculum_material_challenge`. The chunk references who controls the
site and which staff are allowed to regulate public comments.

**Palmer** — The March 28, 2025 DESE Integrated Monitoring Report
(p.10) references curriculum review and gender identity, classified
under `curriculum.review` + `lgbtq.gender_identity`.

*No chunks in the corpus carry the `action_types=["book_challenged"]`
label — the classifier did not flag any board minutes as containing a
formal book challenge filing. The two hits above are the closest
proxies (`censorship.curriculum_material_challenge` and
`curriculum.review`).*

---

## Q5. Analyze any current discussions around parental rights policies. Search agenda items, minutes, and board votes.

**Three districts show parental-rights policy activity in the current
corpus:**

| District | State | Chunks | Most recent meeting |
|---|---|---|---|
| Wachusett | MA | 26 | 2025-01-13 (Agenda #1419) |
| Brewster | MA | 2 | 2025-10-09 (Minutes) |
| Worcester | MA | 1 | — |

**Wachusett Regional School District** — 26 chunks across agendas and
minutes, the highest volume in the corpus. The most recent is the
January 13, 2025 agenda packet (item #1419, p.51), which includes a
draft policy "DP3820 — Relating to Education Observation of Education
Programs for Special Needs Students" tagged
`censorship.parental_rights_policy`. The district's October 7, 2024
minutes (item #1414) contain three related chunks tagged
`lgbtq.antidiscrimination_update` covering Title IX updates,
anti-discrimination language referencing "race, color, religion,
ancestry, national origin, sex, gender identity, sexual orientation,
or disability," and an annual EO announcement from the superintendent.

**Brewster (Nauset Regional)** — 2 chunks from the October 9, 2025
minutes, one tagged `parental_rights.opt_out` and recorded as a **Vote
— Passed**. The motion concerns a Department of Education requirement
regarding civil rights and a "Curriculum Opt Out" policy assuring
parental rights. A second chunk from the same meeting is tagged
`sexed.parental_notification` and references approved minutes from
August 28 and September 11, 2025.

**Worcester** — 1 chunk (details sparse in the current retrieval).

---

## Q6. Identify districts debating transgender student policies in the past 12 months.

**One district in the current corpus shows transgender-student-policy
discussion in the last 12 months:**

| District | State | Chunks | Meeting date | Document |
|---|---|---|---|---|
| Bridgewater-Raynham | MA | 1 | 2025-11-12 | MASC Resolutions for Delegate Summary Report (p.1) |

**Bridgewater-Raynham** — The November 12, 2025 MASC Resolutions for
Delegate Summary Report (p.1) contains a chunk tagged
`lgbtq.transgender_student_policy` at the **Discussion Only** action
stage. The snippet references health and wellness framing: "Health and
wellness requires more than BMI and a combo of genetic, medical,
social and socio-economic factors; School staff are not trained to
diag[nose…]".

*The coarse `transgender_policy` topic has 26 chunks total in the
corpus, but only 1 falls in the last-12-months window. The
`lgbtq_student_rights` topic has 89 chunks total but again only 1 in
the window. This is a corpus-recency issue, not a retrieval issue —
the agent correctly scoped to the date range and found the single
in-window chunk.*

---

## Q7. Summarize all board discussions involving gender identity.

**Eight districts in the corpus have board discussions involving
gender identity, led by Wachusett and Quabbin:**

| District | State | Chunks | Date range of meetings |
|---|---|---|---|
| Wachusett | MA | 24 | 2024-10-07 |
| Quabbin | MA | 16 | 2023-12-14 |
| Ware | MA | 11 | — |
| Palmer | MA | 10 | 2025-03-28 |
| New Salem-Wendell | MA | 3 | — |
| Shutesbury | MA | 3 | — |
| Bridgewater-Raynham | MA | 2 | 2025-11-12 |
| Brewster | MA | 1 | 2025-10-09 |

**Wachusett (24 chunks)** — Concentrated in the October 7, 2024 agenda
packet (item #1414), which bundles Special Education and Bullying
presentations. Three retrieved chunks carry the
`lgbtq.antidiscrimination_update` tag and reference: (1) curriculum
review "to ensure that it promotes respect for the human and civil
rights of all individuals and does not perpetuate discriminatory
[practices]"; (2) non-discrimination language covering "race, color,
religion, ancestry, national origin, sex, gender identity, sexual
orientation, or disability"; and (3) an annual EO/Title IX
announcement from the superintendent.

**Quabbin (16 chunks)** — From the December 14, 2023 minutes,
covering the district's non-discrimination policy update. Retrieved
chunks include: (1) a statement that "The Quabbin Regional School
District's policy of non-discrimination will extend to students,
staff, the general public, and individuals with [protected
characteristics]" tagged `lgbtq.transgender_student_policy` +
`lgbtq.gender_identity_policy`; (2) a cross-reference to MASC
September 2022 policy AC (Non-Discrimination Policy Including
Harassment and Retaliation); and (3) non-discrimination language on
"race, color, sex, sexual orientation, gender identity, religion,
disability, age, genetic info[rmation]" tagged
`lgbtq.student_rights` + `lgbtq.gender_identity`.

**Palmer (10 chunks)** — Includes the March 28, 2025 DESE Integrated
Monitoring Report referencing gender identity in a curriculum-review
context.

**Bridgewater-Raynham (2 chunks)** — The November 12, 2025 MASC
Resolutions report (see Q6).

---

## Notes on data quality for the client

1. **Corpus recency**: The local corpus is a historical ingest (mostly
   2023-2024 meeting minutes, ~20k chunks). Only ~2,433 chunks are from
   the 2025-2026 school year. Q1, Q2, Q3 return empty because the
   specific topic + date-window combinations have no matches — not
   because the agent failed.
2. **Classifier taxonomy drift**: The classifier wrote subtopics like
   `lgbtq.gender_identity`, `lgbtq.student_rights`,
   `lgbtq.non_discrimination_policy` — but the current taxonomy uses
   `gender_identity_discussion`, `transgender_student_policy`, etc.
   The coarse `topics` field (`gender_identity`, `lgbtq_student_rights`,
   `transgender_policy`, `parental_rights`) is more reliable for
   retrieval until the classifier is re-run.
3. **"No matches" is a valid answer**: The agent is designed to say
   "no districts matched" explicitly rather than fabricate. Q1-Q3 are
   honest empties; Q4-Q7 return real evidence.
4. **Retrieval path validated**: Every query exercised the new
   `count_districts_by_topic` + `get_district_citations` tools
   end-to-end against the live Qdrant. The filter primitives
   (`must_match`, `must_match_any`, `nested_match_any`,
   `nested_subtopic_match_any`, `range_match`) all produce correct
   Qdrant conditions.
