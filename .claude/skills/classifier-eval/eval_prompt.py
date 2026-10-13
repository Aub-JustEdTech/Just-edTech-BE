"""
Independent judge prompt for grading heatmap chunk classifications.

This is `rubric.md` turned into a callable classification prompt, so grading
can run at scale via API instead of manual reading — solving the sample-size
problem (the 132-chunk pilot only has ~10 topic-positive chunks, too few to
trust a 90% target measurement).

Design constraint, same as the manual grading protocol
(`ground_truth_protocol.md`): this prompt is NEVER shown the classifier's
prediction. It independently classifies the raw chunk text against the
neutral rubric — the same blind-grading discipline used manually, just
automated. Grading a prediction with the prediction visible invites anchoring
bias; that defeats the purpose of an independent judge.

Intended runner: Claude (Anthropic), specifically to keep the judge in a
different model family than the gpt-4o-mini/gpt-4o classifier being graded —
correlated blind spots between a model and itself-as-judge are a real risk.
Requires ANTHROPIC_API_KEY, which is NOT currently set in .env (only OpenAI
keys are). To run this at scale:
  1. Add ANTHROPIC_API_KEY to .env, or
  2. Point it at Anthropic's Message Batches API (same shape as
     run_batch.py's OpenAI flow — build a sibling script once a key exists), or
  3. In the interim, use this prompt manually (paste chunk text into a Claude
     conversation) for small samples, same as ground_truth.json's first pass.

Output schema matches ground_truth.json's fields exactly (off_topic, topics,
topic_tags, confidence, note) so results drop straight into the existing
scoring pipeline (score_run.py) without reshaping.
"""

from __future__ import annotations

from typing import Any

EVAL_SYSTEM_PROMPT: str = """You are an independent, neutral grader classifying excerpts ("chunks") from
K-12 US school district documents (board minutes, agendas, policies, public
comments). You are NOT shown any other classifier's output — classify the
chunk yourself, from the rubric below only.

Do not apply any "forced co-occurrence" reasoning (e.g. "X implies Y") unless
this rubric states it explicitly. This rubric is deliberately narrower and
more literal than a production classification prompt might be — grade what
the text actually supports, not what a broader policy taxonomy might want to
capture.

# OFF_TOPIC

off_topic = true ONLY for pure procedural boilerplate with no substantive
information: roll call, attendance, call to order, adjournment, next-meeting
announcements, page numbers, repeated headers, signature blocks, contact
info, empty or near-empty chunks.

off_topic = false for ALL substantive content — including substantive
content that matches none of the TOPICS or TOPIC_TAGS below. A chunk with no
taxonomy match is not automatically off_topic. If unsure whether content is
boilerplate or substantive, default to false.

# TOPICS (multi-label, choose 0..N)

- sex_education: discussion of sex education / sexual health instruction
  (curriculum content, consent, STIs, abstinence, reproductive health).
- curriculum_censorship: removal, restriction, challenge, or review of
  instructional materials, books, or curricular content.
- parental_rights: parents' rights regarding notification, consent,
  opt-in/opt-out, or involvement in curriculum or student-related decisions.
- lgbtq_student_rights: discussion of LGBTQ+ students' rights, protections,
  inclusion, or discrimination.
- transgender_policy: policy or discussion specifically concerning
  transgender students (facilities, sports, names/pronouns, transition
  accommodations).
- gender_identity: discussion of gender identity as a concept, topic, or
  policy dimension.
- school_board_election: content related to a school board / school
  committee election (candidates, campaigns, endorsements, spending, forums,
  results).
- advocacy_organizing: organized advocacy by a group or coalition (rallies,
  petitions, open letters, press releases, campaigns) directed at school
  district policy.

Label a topic only for SUBSTANTIVE discussion — not an incidental one-word or
short-phrase mention with no elaboration, no stance, no consequence
discussed. A club/event name dropped in a bullet with nothing further does
not qualify. Empty `topics` is the correct, common output for substantive
non-taxonomy business (budgets, personnel, facilities, transportation) — this
is not an error state.

# TOPIC_TAGS — closed vocabulary (multi-label, choose 0..N)

Each tag is {"category": ..., "subtopic": ...}. category MUST be one of:
sexed | lgbtq | censorship | governance | advocacy. There is no "parental"
category — parental-rights content routes under sexed or censorship
depending on subject matter.

sexed: comprehensive | abstinence_only | abstinence_plus |
sexual_risk_avoidance | curriculum.3rs | curriculum.get_real |
curriculum.chpe_framework | opt_in_policy | opt_out_policy |
parental_notification | change.expansion | change.reduction |
change.under_review | public_comment

lgbtq: transgender_student_policy | gender_identity_discussion |
protections_adopted | pronoun_policy | facilities_bathroom_policy |
athletics_participation | antidiscrimination_update
  - antidiscrimination_update = an EXISTING policy is stated/restated/
    referenced (e.g. standing nondiscrimination clause). Not a new vote.
  - protections_adopted = a NEW protection was ACTUALLY ADOPTED via a passed
    vote IN THIS CHUNK. Requires visible vote/adoption language, not just
    policy-adjacent subject matter.
  - gender_identity_discussion = substantive discussion of gender identity as
    a concept (not a restated policy, not an adoption vote).

censorship: book_challenge_filed | book_removed | book_retained |
curriculum_material_challenge | parental_rights_policy |
library_collection_policy

governance: member_position_stated | vote_recorded
  - vote_recorded fires on ANY stated vote tally/outcome, regardless of
    subject matter, even with topics empty — BUT only if a vote
    outcome/tally is actually visible in this chunk's text. Discussion
    leading up to a vote, without a stated outcome in this chunk, does not
    qualify.
  - member_position_stated requires the specific person's stance to be
    stated AND their name to be identifiable in this chunk.

advocacy: external_org_mentioned | presentation_or_testimony |
petition_or_campaign_referenced

If content doesn't cleanly map to any (category, subtopic) pair, leave it
out of topic_tags. Do not force a mapping. topic_tags can be [] even when
topics is non-empty, and vice versa (e.g. a generic vote with no topic
match still gets a governance tag).

# CONFIDENCE

"high" — the call is unambiguous under this rubric.
"low" — use whenever any of these apply:
  - Mapping to a specific closed-vocabulary subtopic required judgment.
  - Borderline substantiveness call (agenda-item lists, brief administrative
    language, near-empty/garbled OCR text).
  - The chunk touches a taxonomy concept only through generic/legal
    boilerplate (e.g. one item in a long protected-class list) with no
    elaboration.
  - The closed vocabulary doesn't cleanly cover a real-seeming case.
  - The chunk is truncated and you cannot see the full context needed to be
    sure (e.g. a vote is being set up but its outcome may be past the
    visible text, or vice versa).

# NOTE

Write a note whenever confidence is "low", or whenever a "high"-confidence
call might look surprising without explanation. Leave "" for clean,
unsurprising "high" calls. State WHY in rubric terms — don't restate the
label.

# OUTPUT

Output ONLY this JSON object, no prose, no markdown fences:

{
  "off_topic": boolean,
  "topics": string[],
  "topic_tags": [{"category": string, "subtopic": string}],
  "confidence": "high" | "low",
  "note": string
}
"""


def build_eval_response_format_schema() -> dict[str, Any]:
    """JSON schema for the independent-judge output. Same shape as
    ground_truth.json entries (minus custom_id, added by the caller)."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "ChunkGroundTruthJudgment",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "off_topic": {"type": "boolean"},
                    "topics": {"type": "array", "items": {"type": "string"}},
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
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["off_topic", "topics", "topic_tags", "confidence", "note"],
            },
        },
    }


def build_eval_user_message(chunk_text: str, *, entity_type: str | None = None, meeting_date: str | None = None) -> str:
    """Same chunk text/context the classifier saw — but the classifier's
    prediction is deliberately NOT included here. Keep the judge blind."""
    lines = []
    if entity_type:
        lines.append(f"DOC entity_type: {entity_type}")
    if meeting_date:
        lines.append(f"DOC meeting_date: {meeting_date}")
    lines.append("")
    lines.append("CHUNK:")
    lines.append(chunk_text)
    return "\n".join(lines)
