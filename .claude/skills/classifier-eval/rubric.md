# Neutral Judging Rubric

This rubric is the ONLY reference used to produce ground-truth judgments. It
contains no worked examples, no forced co-occurrence rules, and no
prompt-specific edge-case corrections from either classification prompt
version.

## off_topic

`off_topic = true` **only** for pure procedural boilerplate with no
substantive information: roll call, attendance, call to order, adjournment,
next-meeting announcements, page numbers, repeated headers, signature
blocks, contact info, empty or near-empty chunks.

`off_topic = false` for **all** substantive content — including substantive
content that matches none of the TOPICS or TOPIC_TAGS below. A chunk with no
taxonomy match is not automatically off_topic.

## TOPICS enum (multi-label, 0..N apply)

| Topic | Neutral definition |
|---|---|
| `sex_education` | Discussion of sex education / sexual health instruction (curriculum content, consent, STIs, abstinence, reproductive health). |
| `curriculum_censorship` | Removal, restriction, challenge, or review of instructional materials, books, or curricular content. |
| `parental_rights` | Parents' rights regarding notification, consent, opt-in/opt-out, or involvement in curriculum or student-related decisions. |
| `lgbtq_student_rights` | Discussion of LGBTQ+ students' rights, protections, inclusion, or discrimination. |
| `transgender_policy` | Policy or discussion specifically concerning transgender students (e.g. facilities, sports, names/pronouns, transition-related accommodations). |
| `gender_identity` | Discussion of gender identity as a concept, topic, or policy dimension. |
| `school_board_election` | Content related to a school board / school committee election (candidates, campaigns, endorsements, spending, forums, results). |
| `advocacy_organizing` | Organized advocacy activity by a group or coalition (rallies, petitions, open letters, press releases, campaigns) directed at school district policy. |

## TOPIC_TAGS closed vocabulary

`category` must be one of: `sexed | lgbtq | censorship | governance | advocacy`.
There is no `parental` category — parental-rights content routes under
`sexed` or `censorship` depending on subject matter.

**sexed** — comprehensive, abstinence_only, abstinence_plus,
sexual_risk_avoidance, curriculum.3rs, curriculum.get_real,
curriculum.chpe_framework, opt_in_policy, opt_out_policy,
parental_notification, change.expansion, change.reduction,
change.under_review, public_comment

**lgbtq** — transgender_student_policy, gender_identity_discussion,
protections_adopted, pronoun_policy, facilities_bathroom_policy,
athletics_participation, antidiscrimination_update

**censorship** — book_challenge_filed, book_removed, book_retained,
curriculum_material_challenge, parental_rights_policy,
library_collection_policy

**governance** — member_position_stated, vote_recorded

**advocacy** — external_org_mentioned, presentation_or_testimony,
petition_or_campaign_referenced

## Substantiveness rule

Label a topic or tag only when the chunk contains **substantive discussion**
of it. An incidental one-word or short-phrase mention with no elaboration
does not, by itself, justify a label.
