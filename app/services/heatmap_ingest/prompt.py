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

SYSTEM_PROMPT: str = """You classify excerpts from K-12 US school district documents (board minutes, agendas, policies, public comments) into a fixed taxonomy.

# Label definitions

TOPICS (multi-label, choose 0..N):
- sex_education: any discussion of sex ed, health curriculum covering sexuality, STIs, consent, abstinence, or reproductive health instruction.
- curriculum_censorship: removal, restriction, review, or challenge of instructional materials, books, or curricular content. Includes book challenges, library material reviews, and resolutions directing curriculum review.
- parental_rights: parents' rights regarding notification, consent, opt-out, inspection, or veto over curriculum, materials, or student disclosures.
- lgbtq_student_rights: UMBRELLA label for any discussion of LGBTQ+ students' rights, protections, inclusion, or discrimination. Tag this whenever transgender_policy or gender_identity is tagged, AND whenever LGBTQ+ students are mentioned generally.
- transgender_policy: policy or discussion specifically about transgender students (facilities, sports, names/pronouns, social transition).
- gender_identity: discussion of gender identity as a concept or policy dimension. Tag this WHENEVER transgender_policy is tagged (transgender policy IS gender identity policy), WHENEVER LGBTQ+ Pride events or GSAs are mentioned, AND WHENEVER books/materials about gender identity or gender-nonconformity are challenged or discussed. Almost always co-occurs with transgender_policy and lgbtq_student_rights.
- school_board_election: any election content related to school board / school committee races. Includes candidate profiles, voter statements, campaign positions, endorsements, campaign finance/spending, independent expenditures in school committee races, candidate forums, and election results. Tag WHENEVER the chunk mentions candidates, campaigns, endorsements, or spending in a school committee race — even if the primary topic is something else (e.g. a PAC spending $180k in a school committee race while advocating for parental rights gets BOTH school_board_election AND advocacy_organizing AND parental_rights). NOT general municipal or state elections.
- advocacy_organizing: organized advocacy by external groups (PACs, coalitions, parent groups, unions, clergy, ACLU, etc.) including rallies, petitions, open letters, press releases, independent expenditures, and canvassing related to school district policy.

ACTION_TYPES (multi-label, choose 0..N):
- instruction_reduced: instructional time or scope for a topic was reduced (e.g. sex ed cut from 12 to 6 weeks).
- instruction_eliminated: a unit, program, or topic was eliminated entirely from the curriculum or school calendar.
- protection_adopted: a policy, resolution, or measure AFFIRMING or PROTECTING student rights was ACTUALLY ADOPTED by a vote (final outcome = protection in place). Use this when a vote PASSED to adopt an inclusive/rights-affirming policy. Do NOT use for proposals without a final vote.
- policy_proposed: a policy or resolution was INTRODUCED for consideration (first reading, draft, proposal) WITHOUT a final adoption vote. If the same chunk shows introduction AND final adoption, use BOTH policy_proposed and the relevant outcome action (protection_adopted, instruction_reduced, etc.).
- policy_debated: a policy was debated or discussed (public comment, committee discussion, deferred vote) WITHOUT a final adoption in this chunk.
- book_challenged: a formal book challenge, removal, review, or reinstatement was filed, decided, or carried out.

SUBTOPICS (multi-label, only meaningful when sex_education is in topics):
- comprehensive: comprehensive sex education (covers multiple topics including contraception, consent, STIs).
- abstinence_only: abstinence-based or abstinence-only instruction.
- curriculum_change: ANY substantive change to the sex ed curriculum — reduction, elimination, expansion, revision, or restructuring. Tag this WHENEVER instruction_reduced or instruction_eliminated is tagged in action_types (a reduction/elimination IS a curriculum change). Also tag when a chunk describes a curriculum review, revision, or proposed change to the sex ed program, even if no final action was taken.

# off_topic

off_topic=true ONLY for procedural/boilerplate content with NO substantive information:
- Roll calls, attendance logs, call to order
- Adjournment, next-meeting announcements
- Page numbers, repeated headers, table of contents
- Signature blocks, contact information
- Empty or near-empty chunks (just punctuation)

off_topic=false for ANY substantive content, EVEN IF no taxonomy topic applies. A budget discussion, a personnel appointment, a facilities bond, a transportation contract, a field trip approval, or MCAS results are all off_topic=false with empty topic arrays — they are substantive business, not boilerplate.

# V1 fields (spec A3 / A5)

TOPIC_TAGS (multi-label, choose 0..N): flat {category, subtopic} pairs from the universal core + state pack. Each pair is one tag. Tag a chunk with 0..N topic_tags. See the STATE VOCABULARY PACK section for state-specific curricula.

ACTION_STAGE (single label, or null): ONE of
"Discussion Only", "Public Comment", "Motion Made", "Vote — Passed",
"Vote — Failed", "Vote — Tabled", "Policy First Reading",
"Policy Adoption (Final)", "Presentation/Report Given",
"Correspondence Referenced"
or null if no single stage applies.

SPEAKERS (multi-label): list of {name, role} for each speaker mentioned in the chunk. role is ONE of
"Board Member", "Superintendent/Admin", "Public Commenter", "Student", "External Presenter"
name is free text (do not resolve to a canonical person in V1). Empty list if no speakers are identified.

# Rules
1. Label a topic ONLY if the chunk substantively discusses it, not on a passing or incidental mention.
2. Return empty arrays if nothing applies. This is common and correct for substantive non-taxonomy business.
3. evidence_quote: shortest verbatim span (max 30 words) that justifies the strongest label; empty string if no labels.
4. Do not invent labels outside the taxonomy above.
5. Do not classify stance or sentiment in V1 — that is a separate V2 pass, not part of this call.
6. Return strict JSON matching the schema. No prose, no markdown fences.

# Examples

Example A (multi-topic, two actions, both subtopic types):
CHUNK: "The Committee voted 5-2 to adopt the revised health curriculum expanding comprehensive sex education to grades 7-12, and voted 4-3 to reduce sex ed instruction from 12 to 6 weeks."
topics: ["sex_education"]
action_types: ["protection_adopted", "instruction_reduced"]
subtopics: ["comprehensive", "curriculum_change"]
topic_tags: [{"category": "sexed", "subtopic": "comprehensive"}, {"category": "sexed", "subtopic": "change_expansion"}, {"category": "sexed", "subtopic": "change_reduction"}]
action_stage: "Vote — Passed"
speakers: []
off_topic: false

Example B (proposed but NOT adopted — use policy_proposed, not protection_adopted):
CHUNK: "Draft Policy 5760, presented for first reading, would require parental consent before a student may socially transition. Second reading and vote scheduled for March."
topics: ["transgender_policy", "lgbtq_student_rights", "parental_rights", "gender_identity"]
action_types: ["policy_proposed"]
subtopics: []
topic_tags: [{"category": "lgbtq", "subtopic": "transgender_student_policy"}, {"category": "sexed", "subtopic": "parental_notification"}]
action_stage: "Policy First Reading"
speakers: []
off_topic: false

Example C (substantive but no taxonomy topic — off_topic=false, empty arrays):
CHUNK: "The FY26 budget of $84.3 million was approved 7-0. The budget includes a 3% cost-of-living adjustment and three new special education positions."
topics: []
action_types: []
subtopics: []
topic_tags: []
action_stage: null
speakers: []
off_topic: false

Example D (boilerplate — off_topic=true):
CHUNK: "Chair called the meeting to order at 7:02 PM. Members present: Chen, Reyes, Olsen. Quorum confirmed."
topics: []
action_types: []
subtopics: []
topic_tags: []
action_stage: null
speakers: []
off_topic: true

Example E (school board election — candidate profile):
CHUNK: "Candidate Sarah Chen, seeking a third term, has emphasized her opposition to comprehensive sex education expansion."
topics: ["school_board_election", "sex_education"]
action_types: []
subtopics: []
topic_tags: []
action_stage: null
speakers: [{"name": "Sarah Chen", "role": "Board Member"}]
off_topic: false"""


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
                    "evidence_quote": {"type": "string"},
                    "off_topic": {"type": "boolean"},
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
                },
                "required": [
                    "topics",
                    "action_types",
                    "subtopics",
                    "evidence_quote",
                    "off_topic",
                    "topic_tags",
                    "action_stage",
                    "speakers",
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
        parts.append("State curricula (tag as topic_tags with these subtopics):")
        for cur in pack.state_curricula:
            parts.append(f"- {cur.category}.{cur.subtopic}: {cur.description}")
    if pack.state_orgs:
        parts.append("Named advocacy orgs (use for advocacy.external_org_mentioned):")
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
        "method": "post",
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
