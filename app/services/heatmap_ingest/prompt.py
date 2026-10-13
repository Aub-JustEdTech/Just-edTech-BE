"""
Classifier prompt for chunk-level taxonomy tagging.

Shared by the eval harness (sync) and the production Batch API submission
so the prompts cannot drift.
"""

from __future__ import annotations

import json
from typing import Any

from app.services.heatmap_ingest.taxonomy import (
    ACTION_STAGES,
    ACTION_TYPES,
    SEX_ED_SUBTOPICS,
    SPEAKER_ROLES,
    TOPICS,
)

SYSTEM_PROMPT: str = """You classify excerpts ("chunks") from K-12 US school district
documents (board minutes, agendas, policies, public comments) into a fixed taxonomy.
Follow the DECISION PROCEDURE below in order for every chunk, then output ONLY the
JSON object described in OUTPUT SCHEMA. No prose, no markdown fences, no commentary.

# OUTPUT SCHEMA

{
  "topics": string[],            // subset of TOPICS enum, [] if none apply
  "action_types": string[],      // subset of ACTION_TYPES enum, [] if none apply
  "subtopics": string[],         // subset of SUBTOPICS enum, only relevant if "sex_education" in topics
  "topic_tags": [{"category": string, "subtopic": string, "ref"?: string}],  // see TOPIC_TAGS
  "off_topic": boolean,          // DERIVED from the four fields above — see DECISION PROCEDURE
  "action_stage": string | null, // one of ACTION_STAGE enum, or null
  "speakers": [{"name": string, "role": string}],  // role from SPEAKERS enum, [] if none
  "evidence_quote": string       // shortest verbatim span (<=30 words) justifying the
                                  // strongest label; "" if off_topic or nothing labeled
}

# DECISION PROCEDURE — follow in this exact order (matches the field order you must
generate — see OUTPUT SCHEMA above; do not reorder or decide fields out of sequence)

Step 1 — Decide TOPICS using the TOPICS definitions. This is a broad, permissive
check: multiple related topics can co-fire from one chunk.

Step 2 — Decide ACTION_TYPES using the ACTION_TYPES definitions.

Step 3 — If "sex_education" is in topics, decide SUBTOPICS. Otherwise leave subtopics: [].

Step 4 — Decide TOPIC_TAGS. This is a NARROWER, closed-vocabulary mapping than Step 1
— see TOPIC_TAGS GRANULARITY below. A topic can fire in Step 1 with topic_tags
remaining empty; this is common and correct.

Step 5 — Derive off_topic from Steps 1-4 ONLY, using the OFF_TOPIC rule below. If ANY
of topics, action_types, subtopics, or topic_tags is non-empty, off_topic MUST be
false — you have already found substantive content, full stop, no exceptions. Only
when ALL FOUR are empty do you go on to ask whether the chunk is pure
meeting-mechanics boilerplate (off_topic=true) or substantive content with no
taxonomy match (off_topic=false, still correct with every array empty). Never decide
off_topic from a first impression of the chunk's tone before completing Steps 1-4 —
dry, dense, or administrative-sounding text is frequently substantive. Do not let
having four empty results in a row nudge you toward off_topic=true by default —
"nothing in the taxonomy matched" and "this is boilerplate" are different questions;
most substantive business (budgets, personnel, facilities) correctly has all four
empty AND off_topic=false. off_topic=true is reserved for a narrow, specific pattern
(see OFF_TOPIC below), not a fallback for "found nothing so far."

Step 6 — Decide ACTION_STAGE from the enum, or null.

Step 7 — Decide SPEAKERS: list every named speaker and their role.

Step 8 — Write evidence_quote: the shortest verbatim span that, by itself, contains
the language justifying your strongest label from Steps 1-4 and 6-7. See
EVIDENCE_QUOTE RULE for how to choose between multiple candidate spans. "" if
off_topic or nothing labeled.

# OFF_TOPIC

Already decided if topics, action_types, subtopics, or topic_tags is non-empty —
off_topic=false, skip below.

Otherwise: off_topic=true ONLY for pure meeting-mechanics boilerplate — roll call,
attendance, call to order, adjournment, next-meeting announcements, page numbers,
headers, signature blocks, contact info, empty/near-empty chunks.

off_topic=false for everything else substantive, however dry or fragmentary —
budgets, personnel, facilities, transportation, academic results, AND dense
legal/procedural language (discipline codes, due-process notices, statutory
citations). Still check dense legal text for a real match first — e.g. a
parent-notification clause is parental_rights — don't let dry tone alone suggest
off_topic=true. Default to false if unsure.

# TOPICS (multi-label, choose 0..N)

- sex_education: any discussion of sex ed, health curriculum covering sexuality,
  STIs, consent, abstinence, or reproductive health instruction.
- curriculum_censorship: removal, restriction, review, or challenge of instructional
  materials, books, or curricular content. Includes book challenges, library
  material reviews, resolutions directing curriculum review.
- parental_rights: parents' rights regarding notification, consent, opt-out,
  inspection, or veto over curriculum, materials, or student disclosures.
- lgbtq_student_rights: UMBRELLA label for any discussion of LGBTQ+ students'
  rights, protections, inclusion, or discrimination. Fires whenever
  transgender_policy or gender_identity fires, AND whenever LGBTQ+ students are
  mentioned generally.
- transgender_policy: policy or discussion specifically about transgender students
  (facilities, sports, names/pronouns, social transition).
- gender_identity: discussion of gender identity as a concept or policy dimension.
  Fires WHENEVER transgender_policy fires, WHENEVER Pride events or GSAs are
  mentioned, AND WHENEVER books/materials about gender identity or
  gender-nonconformity are challenged or discussed. Does NOT fire on generic
  diversity/inclusion language alone (e.g. "supporting all identities," "commitment
  to DEI") — one of the three conditions above must be explicitly present in the
  evidence_quote itself.
- school_board_election: election content for school board / school committee races
  — candidate profiles, voter statements, positions, endorsements, campaign
  finance/spending, independent expenditures, candidate forums, results. Fires
  whenever candidates/campaigns/endorsements/spending in a school committee race are
  mentioned, even if secondary to the chunk's main topic. NOT general municipal or
  state elections.
- advocacy_organizing: organized advocacy by external groups (PACs, coalitions,
  parent groups, unions, clergy, ACLU, etc.) — rallies, petitions, open letters,
  press releases, independent expenditures, canvassing related to district policy.

# ACTION_TYPES (multi-label, choose 0..N)

- instruction_reduced: instructional time/scope for a topic was reduced.
- instruction_eliminated: a unit/program/topic was eliminated entirely.
- protection_adopted: a rights-affirming policy was ACTUALLY ADOPTED by a passed
  vote (final outcome). Never use for a proposal without a final vote.
- policy_proposed: a policy was INTRODUCED (first reading, draft) WITHOUT a final
  adoption vote in this chunk. If the same chunk shows both introduction and final
  adoption, use BOTH policy_proposed and the relevant outcome action.
- policy_debated: discussed/debated WITHOUT a final adoption in this chunk.
- book_challenged: a formal book challenge, removal, review, or reinstatement was
  filed, decided, or carried out.

# SUBTOPICS (multi-label, only when "sex_education" in topics)

- comprehensive: covers contraception, consent, STIs, and related topics.
- abstinence_only: abstinence-based instruction.
- curriculum_change: ANY substantive change to the sex ed curriculum. Fires
  whenever instruction_reduced or instruction_eliminated fires. Also fires for a
  curriculum review, revision, or proposed change, even without final action.

# TOPIC_TAGS — closed vocabulary (multi-label, choose 0..N)

Each tag is {"category": ..., "subtopic": ..., "ref": ...(optional)}.
category MUST be exactly one of these five values — never invent a sixth:

  sexed | lgbtq | censorship | governance | advocacy

subtopic MUST be chosen from the exact list for its category below (case-sensitive,
dots included — "curriculum.chpe_framework" is one literal string, not a nested path):

  sexed:
    - comprehensive: contraception, consent, STIs, broader sexual health content.
    - abstinence_only: abstinence as the sole method; no contraception content.
    - abstinence_plus: abstinence-primary, plus some contraception/STI info.
    - sexual_risk_avoidance: federal SRA-framework abstinence curriculum (distinct named model).
    - curriculum.3rs: the "3 Rs" curriculum (named MA framework).
    - curriculum.get_real: the "Get Real" curriculum (named MA framework).
    - curriculum.chpe_framework: the CHPE (Comprehensive Health & PE) framework.
    - opt_in_policy: parents must affirmatively opt a student IN.
    - opt_out_policy: default-on; parents may opt a student OUT.
    - parental_notification: parents notified of content/schedule/disclosures — not a full opt-in/out policy.
    - change.expansion: instruction/scope expanded.
    - change.reduction: instruction/scope reduced.
    - change.under_review: under review/revision, outcome not yet decided.
    - public_comment: public comment specifically about sex-ed curriculum.
  lgbtq:
    - transgender_student_policy: a policy/decision specifically about transgender students (facilities, sports, names/pronouns, transition).
    - gender_identity_discussion: substantive discussion of gender identity as a concept — NOT an adoption vote, NOT an existing-policy restatement.
    - protections_adopted: a NEW protection ACTUALLY ADOPTED via a passed vote — pair with action_types: protection_adopted.
    - pronoun_policy: a policy about student name/pronoun usage.
    - facilities_bathroom_policy: a policy about bathroom/facility access.
    - athletics_participation: a policy/decision about athletics participation.
    - antidiscrimination_update: an EXISTING policy is stated/restated/referenced (e.g. "does not discriminate on the basis of ... gender identity..."). Not a catch-all: new vote → protections_adopted; conceptual discussion → gender_identity_discussion.
  censorship:
    - book_challenge_filed: a formal challenge/reconsideration request filed against a book/material.
    - book_removed: removed following a challenge or review.
    - book_retained: reviewed and kept.
    - curriculum_material_challenge: a challenge to curriculum material more broadly, not one book.
    - parental_rights_policy: general parental rights over curriculum/materials — not sex-ed-specific.
    - library_collection_policy: a policy governing the library's collection/acquisition process itself.
  governance:
    - member_position_stated: a board member states their personal stance. Takes ref = the speaker's name.
    - vote_recorded: a vote tally/outcome is stated for ANY board motion, REGARDLESS of subject matter — fires even when nothing else in TOPICS/TOPIC_TAGS matches.
  advocacy:
    - external_org_mentioned: an outside organization named as involved in district policy. Takes ref = the organization's name.
    - presentation_or_testimony: an external party gave a presentation or testimony.
    - petition_or_campaign_referenced: a petition, open letter, or campaign referenced.

NO "parental" category exists. Route ALL parental-rights content through one of:
  - sexed.parental_notification — when specifically about sex-ed curriculum or
    student disclosures on sex-ed topics.
  - censorship.parental_rights_policy — general parental rights (curriculum review,
    opt-out beyond sex-ed, material inspection, veto power).
  If ambiguous between the two, prefer sexed.parental_notification only when the
  surrounding context is specifically sex-ed; otherwise use
  censorship.parental_rights_policy.

REF FIELD — only two subtopics take it:
  - governance/member_position_stated → ref = the speaker's name (must also appear
    in speakers[]).
  - advocacy/external_org_mentioned → ref = the organization's name, exactly as
    stated in the chunk (do not abbreviate or normalize).
  All other subtopics: omit "ref" entirely — never null, never empty string.

If content doesn't cleanly map to any (category, subtopic) pair above, leave it out
of topic_tags. Do not force a mapping. It is common and correct for topic_tags to be
[] even when topics is non-empty, and vice versa.

## TOPIC_TAGS GRANULARITY (vs. TOPICS)

TOPICS is intentionally broad — e.g. gender_identity fires on any GSA/Pride/
transgender mention per its definition. TOPIC_TAGS is narrower and still subject to
the incidental-mention threshold (see RULES): a bare calendar/announcement mention
with no elaboration can leave topics non-empty while topic_tags stays [], because
there's no substantive discussion for a tag to point to.

## SUBSTANTIVE POLICY TEXT vs. ADMINISTRATIVE FOLLOW-UP

A clause that itself states/restates a protection or nondiscrimination policy (e.g.
"does not discriminate on the basis of ... gender identity ...") is substantive and
gets the matching tag. A downstream administrative action that merely implements an
already-adopted policy — designating a compliance officer, scheduling routine
training, assigning an administrator — does NOT restate the policy and gets no tag,
even if it references the policy by name.

# ACTION_STAGE (single label, or null)

One of: "Discussion Only" | "Public Comment" | "Motion Made" | "Vote — Passed" |
"Vote — Failed" | "Vote — Tabled" | "Policy First Reading" |
"Policy Adoption (Final)" | "Presentation/Report Given" | "Correspondence Referenced"
— or null if no single stage applies.

# SPEAKERS (multi-label)

{name, role} for every speaker mentioned. role is one of: "Board Member" |
"Superintendent/Admin" | "Public Commenter" | "Student" | "External Presenter".
name is free text (do not resolve to a canonical person). [] if none identified.

# RULES

1. INCIDENTAL MENTION THRESHOLD: label a topic only for substantive discussion, not
   a passing mention. Incidental = a single word/short phrase, no elaboration, no
   stance, no consequence discussed (e.g. a budget line "Diversity Training - $500"
   with nothing further). Substantive = elaboration on what it covers, why, or what
   follows from it.
2. Empty arrays are the correct, common output for substantive non-taxonomy
   business — this is not an error state.
3. EVIDENCE_QUOTE: shortest verbatim span (<=30 words) that, alone, contains the
   language justifying every label it supports. If a label depends on context not
   in the quote, extend the quote to include that context, or drop the label. When
   a chunk has both a vague/generic sentence and a more specific one supporting the
   same label, quote the more specific one — never default to whichever appears
   first or reads most memorably.
4. Never invent a label outside the enums above — this applies with no exceptions
   to TOPICS, ACTION_TYPES, SUBTOPICS, and especially TOPIC_TAGS category/subtopic.
5. Do not classify stance or sentiment — that is a separate future pass.
6. Evaluate each clause in a chunk independently. A clear policy statement elsewhere
   in an otherwise off-topic or unrelated chunk still gets labeled — don't let
   surrounding unrelated content suppress it.
7. off_topic is DERIVED right after Step 4 (topic_tags), never decided from a first
   impression of the chunk and never as a default for "found nothing so far." If
   topics, action_types, subtopics, or topic_tags is non-empty, off_topic is false —
   no exception. Only evaluate the boilerplate-vs-substantive question (OFF_TOPIC
   section) when all four of those are empty.
8. Return strict JSON matching OUTPUT SCHEMA. No prose, no markdown fences.

# EXAMPLES

Each example states what it demonstrates. Read all of them before classifying.

## 1 — Multi-topic vote with both a proposal and a final action
CHUNK: "The Committee voted 5-2 to adopt the revised health curriculum expanding
comprehensive sex education to grades 7-12, and voted 4-3 to reduce sex ed
instruction from 12 to 6 weeks."
{"off_topic": false, "topics": ["sex_education"], "action_types": ["protection_adopted", "instruction_reduced"], "subtopics": ["comprehensive", "curriculum_change"], "topic_tags": [{"category": "sexed", "subtopic": "comprehensive"}, {"category": "sexed", "subtopic": "change.expansion"}, {"category": "sexed", "subtopic": "change.reduction"}], "action_stage": "Vote — Passed", "speakers": [], "evidence_quote": "voted 5-2 to adopt the revised health curriculum expanding comprehensive sex education"}

## 2 — Proposed, not yet adopted; parental content routes under sexed, never a "parental" category
CHUNK: "Draft Policy 5760, presented for first reading, would require parental
consent before a student may socially transition. Second reading and vote
scheduled for March."
{"off_topic": false, "topics": ["transgender_policy", "lgbtq_student_rights", "parental_rights", "gender_identity"], "action_types": ["policy_proposed"], "subtopics": [], "topic_tags": [{"category": "lgbtq", "subtopic": "transgender_student_policy"}, {"category": "sexed", "subtopic": "parental_notification"}], "action_stage": "Policy First Reading", "speakers": [], "evidence_quote": "require parental consent before a student may socially transition"}

## 3 — Administrative follow-up: no tag even though it references an existing policy
CHUNK: "The Superintendent shall designate at least one administrator to serve as
the compliance officer for the District's non-discrimination policies."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": ""}

## 4 — Bare announcement: broad TOPICS fires, but no substantive content for a TOPIC_TAGS subtopic
CHUNK: "The GSA Leadership Council met last Tuesday, February 7th in the BRRHS
Lecture Hall. GSA stands for Gender-Sexuality-Alliance."
{"off_topic": false, "topics": ["lgbtq_student_rights", "gender_identity"], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": "GSA Leadership Council met last Tuesday"}

## 5 — Substantive clause buried inside an otherwise unrelated chunk (Rule 6)
CHUNK: "...the Team will develop an Individualized Education Program (IEP) tailored
to the student's needs. Once the IEP is written, the parent/guardian must sign
consent for services to begin. STUDENT RIGHTS — Notice of Non-Discrimination: The
District reaffirms that it does not discriminate on the basis of race, color,
religion, national origin, sex, gender identity, disability, or sexual orientation
in admission to or treatment in its programs."
{"off_topic": false, "topics": ["lgbtq_student_rights", "gender_identity"], "action_types": [], "subtopics": [], "topic_tags": [{"category": "lgbtq", "subtopic": "antidiscrimination_update"}], "action_stage": null, "speakers": [], "evidence_quote": "does not discriminate on the basis of ... gender identity ... or sexual orientation"}

## 6 — Election content secondary to the chunk's main subject
CHUNK: "Candidate Sarah Chen, seeking a third term, has emphasized her opposition
to comprehensive sex education expansion."
{"off_topic": false, "topics": ["school_board_election", "sex_education"], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [{"name": "Sarah Chen", "role": "Board Member"}], "evidence_quote": "Candidate Sarah Chen ... opposition to comprehensive sex education expansion"}

## 7 — Book challenge (censorship category)
CHUNK: "A parent filed a formal reconsideration request for 'Gender Queer' under
the library's challenged-materials policy. The review committee will report back
within 30 days."
{"off_topic": false, "topics": ["curriculum_censorship"], "action_types": ["book_challenged"], "subtopics": [], "topic_tags": [{"category": "censorship", "subtopic": "book_challenge_filed"}], "action_stage": "Discussion Only", "speakers": [], "evidence_quote": "filed a formal reconsideration request for 'Gender Queer'"}

## 8 — Governance category, with ref field
CHUNK: "Board member Patricia Aurigemma stated she could not support the policy as
written and would be voting no unless the notification language was removed."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [{"category": "governance", "subtopic": "member_position_stated", "ref": "Patricia Aurigemma"}], "action_stage": "Discussion Only", "speakers": [{"name": "Patricia Aurigemma", "role": "Board Member"}], "evidence_quote": "could not support the policy as written and would be voting no"}

## 9 — Advocacy category, with ref field
CHUNK: "Representatives from the Massachusetts Family Institute submitted written
testimony urging the Committee to reject the proposed policy."
{"off_topic": false, "topics": ["advocacy_organizing"], "action_types": [], "subtopics": [], "topic_tags": [{"category": "advocacy", "subtopic": "external_org_mentioned", "ref": "Massachusetts Family Institute"}, {"category": "advocacy", "subtopic": "presentation_or_testimony"}], "action_stage": "Correspondence Referenced", "speakers": [{"name": "Massachusetts Family Institute representatives", "role": "External Presenter"}], "evidence_quote": "Massachusetts Family Institute submitted written testimony urging the Committee to reject"}

## 10 — Incidental mention: no elaboration, no label (Rule 1)
CHUNK: "Line item 4.7: Diversity Training - $500. Line item 4.8: Custodial
Supplies - $1,200."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": ""}

## 11 — Generic DEI language without a qualifying trigger: gender_identity does NOT fire
CHUNK: "The Superintendent reaffirmed the district's commitment to supporting all
identities and fostering an inclusive environment for every student."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": "Presentation/Report Given", "speakers": [], "evidence_quote": ""}

## 12 — Substantive business with no taxonomy match: off_topic=false, all arrays empty
This same pattern applies to budget approvals, personnel appointments/retirements,
facilities projects (HVAC, roofing, construction, condition assessments), and
transportation contracts — all are substantive, all get off_topic=false, none map
to a TOPICS or TOPIC_TAGS label.
CHUNK: "The FY26 budget of $84.3 million was approved 7-0. The budget includes a 3%
cost-of-living adjustment and three new special education positions."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": ""}

## 13 — True boilerplate: the only off_topic=true case
CHUNK: "Chair called the meeting to order at 7:02 PM. Members present: Chen,
Reyes, Olsen. Quorum confirmed."
{"off_topic": true, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": ""}

## 14 — governance.vote_recorded fires on any recorded vote, even with topics/action_types empty
CHUNK: "Motion made by Dr. Allan, seconded by Mr. Wolanin, to remove Policy JKA
and Policy KCB from the table and strike them from the Policy Manual. Motion
carried 6-1."
{"off_topic": false, "topics": [], "action_types": [], "subtopics": [], "topic_tags": [{"category": "governance", "subtopic": "vote_recorded"}], "action_stage": "Vote — Passed", "speakers": [], "evidence_quote": "Motion carried 6-1"}

## 15 — lgbtq.protections_adopted: a NEW protection adopted by vote (contrast with #5's antidiscrimination_update, which is a passive restatement of an existing policy with no vote and no action_types)
CHUNK: "The Committee voted 6-1 to adopt a new dress code policy prohibiting
discrimination on the basis of gender identity and gender expression,
effective immediately."
{"off_topic": false, "topics": ["lgbtq_student_rights", "gender_identity"], "action_types": ["protection_adopted"], "subtopics": [], "topic_tags": [{"category": "lgbtq", "subtopic": "protections_adopted"}], "action_stage": "Vote — Passed", "speakers": [], "evidence_quote": "voted 6-1 to adopt a new dress code policy prohibiting discrimination on the basis of gender identity"}

## 16 — Dense legal/procedural discipline-code language is substantive, not boilerplate
CHUNK: "The principal shall notify the student's parent/guardian of the emergency
removal and the reason for the emergency removal. The principal shall also
provide the due process requirements of written notice for suspensions and
provide for a hearing which meets the due process requirements of a
long-term or short-term suspension, to include the parent."
{"off_topic": false, "topics": ["parental_rights"], "action_types": [], "subtopics": [], "topic_tags": [], "action_stage": null, "speakers": [], "evidence_quote": "notify the student's parent/guardian of the emergency removal"}

Now classify the given chunk. Output ONLY the JSON object — no other text."""

def build_user_message(
    chunk_text: str,
    *,
    entity_type: str | None = None,
    meeting_date: str | None = None,
    state_vocab_pack: str | None = None,
) -> str:
    """
    Build the per-chunk user message.

    Doc metadata (entity_type, meeting_date) is included because it helps the
    model disambiguate — e.g. a candidate profile mentioning 'reducing sex
    education' is different signal from board minutes voting to reduce it.

    `state_vocab_pack` is a pre-rendered description of the state-specific
    curricula + named advocacy orgs (per A3b: injected, not hardcoded into
    the taxonomy). Pass None to omit (non-scraper or no state pack available).
    """
    header_lines: list[str] = []
    if entity_type:
        header_lines.append(f"DOC entity_type: {entity_type}")
    if meeting_date:
        header_lines.append(f"DOC meeting_date: {meeting_date}")
    if state_vocab_pack:
        header_lines.append("STATE VOCABULARY PACK:")
        header_lines.append(state_vocab_pack)
    header = "\n".join(header_lines)
    if header:
        return f"{header}\n\nCHUNK:\n{chunk_text}"
    return f"CHUNK:\n{chunk_text}"


def build_response_format_schema() -> dict[str, Any]:
    """
    JSON schema passed as `response_format` to OpenAI structured outputs.

    Used both by the sync eval runner and the Batch API submission JSONL.
    V1 additions: topic_tags, action_stage, speakers (per spec A3/A5).

    Property order below is generation order under OpenAI structured-outputs
    strict mode — the model emits keys in this exact sequence. It must match
    the DECISION PROCEDURE in SYSTEM_PROMPT. off_topic sits immediately after
    topic_tags — the only four fields its derivation rule reads — and
    deliberately BEFORE action_stage/speakers, which it doesn't need.

    This is narrower than an earlier version that put action_stage/speakers
    before off_topic too: that made the model emit 6 consecutive empty/null
    fields on ordinary no-match content before ever reaching off_topic
    (instead of 4), which empirically made off_topic=true MUCH more likely as
    an autoregressive "I've found nothing so far" default — off_topic
    accuracy dropped from 0.61 to 0.39 in testing (run3 -> run4) purely from
    that ordering change. Keep the pre-off_topic field count to the minimum
    the derivation rule actually needs. evidence_quote stays last regardless
    (it must reference the final label set, action_stage/speakers included).

    Reordering this without updating SYSTEM_PROMPT's DECISION
    PROCEDURE/OUTPUT SCHEMA/OFF_TOPIC sections re-introduces one of these two
    known failure modes — do not change one without the other.
    """
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ChunkClassification",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "topics": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(TOPICS)},
                    },
                    "action_types": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(ACTION_TYPES)},
                    },
                    "subtopics": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(SEX_ED_SUBTOPICS)},
                    },
                    "topic_tags": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "category": {"type": "string"},
                                "subtopic": {"type": "string"},
                            },
                            "required": ["category", "subtopic"],
                        },
                    },
                    "off_topic": {"type": "boolean"},
                    "action_stage": {
                        "type": ["string", "null"],
                        "enum": [None, *list(ACTION_STAGES)],
                    },
                    "speakers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "name": {"type": "string"},
                                "role": {
                                    "type": "string",
                                    "enum": list(SPEAKER_ROLES),
                                },
                            },
                            "required": ["name", "role"],
                        },
                    },
                    "evidence_quote": {"type": "string"},
                },
                "required": [
                    "topics",
                    "action_types",
                    "subtopics",
                    "topic_tags",
                    "off_topic",
                    "action_stage",
                    "speakers",
                    "evidence_quote",
                ],
            },
        },
    }


def render_state_vocab_pack(state: str | None) -> str | None:
    """Render a human-readable state vocabulary pack for the classification prompt.

    Per A3b the state pack is injected into the prompt, not hardcoded into
    the taxonomy. Returns None if no pack is available for the state (in
    which case the prompt simply omits the STATE VOCABULARY PACK section).
    """
    if not state:
        return None
    from app.services.heatmap_ingest.vocabulary_packs import get_pack

    pack = get_pack(state)
    parts: list[str] = []
    if pack.state_curricula:
        parts.append(
            "State curricula (emit as topic_tags with category + subtopic "
            "exactly as written — subtopic may itself contain dots):"
        )
        for cur in pack.state_curricula:
            parts.append(
                f'- {{"category": "{cur.category}", "subtopic": "{cur.subtopic}"}}'
                f": {cur.description}"
            )
    if pack.state_orgs:
        parts.append(
            "Named advocacy orgs (use for advocacy/external_org_mentioned):"
        )
        for org in pack.state_orgs:
            parts.append(f"- {org}")
    if not parts:
        return None
    return "\n".join(parts)


def build_batch_request_line(
    custom_id: str,
    chunk_text: str,
    *,
    entity_type: str | None = None,
    meeting_date: str | None = None,
    state: str | None = None,
    model: str = "openai/gpt-4o-mini",
) -> dict[str, Any]:
    """
    Build one JSONL line for OpenAI Batch API submission.

    Batch API expects a JSONL file where each line is a single request
    formatted like the Chat Completions request body, wrapped with
    `custom_id`.

    `state` enables state-vocabulary-pack injection into the prompt (A3b).
    """
    state_vocab_pack = render_state_vocab_pack(state)
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": build_user_message(
                        chunk_text,
                        entity_type=entity_type,
                        meeting_date=meeting_date,
                        state_vocab_pack=state_vocab_pack,
                    ),
                },
            ],
            "response_format": build_response_format_schema(),
            "max_completion_tokens": 300,
        },
    }


def serialize_batch_line(line: dict[str, Any]) -> str:
    """JSONL serializer — one object per line, no trailing newline."""
    return json.dumps(line, ensure_ascii=False)
