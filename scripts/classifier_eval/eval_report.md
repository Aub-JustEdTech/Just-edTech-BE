# Heatmap Chunk Classifier — Phase 1 Eval Report

- Model: `gpt-4o-mini`
- Labeled chunks: 96
- Successful calls: 96
- Failed calls: 0
- Wall-clock: 19.4s
- **Macro F1 (chunk-level labels with support > 0): 0.865**
- **off_topic accuracy: 0.990**

## Gate decision: **PASS**

- Required macro F1 >= 0.75 → actual 0.865
- Required recall >= 0.60 for action_types.policy_debated → actual 0.600 [PASS]
- Required recall >= 0.60 for action_types.policy_proposed → actual 0.714 [PASS]
- Required recall >= 0.60 for topics.lgbtq_student_rights → actual 0.718 [PASS]

## Per-field micro-averaged metrics

| Field | Precision | Recall | F1 |
|---|---|---|---|
| topics | 0.961 | 0.852 | 0.886 |
| action_types | 0.938 | 0.938 | 0.931 |
| subtopics | 0.967 | 0.986 | 0.972 |

## Per-label one-vs-rest — topics

| Label | Precision | Recall | F1 | Support | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| sex_education | 1.000 | 0.913 | 0.955 | 23 | 21 | 0 | 2 |
| curriculum_censorship | 0.960 | 0.857 | 0.906 | 28 | 24 | 1 | 4 |
| parental_rights | 1.000 | 0.778 | 0.875 | 36 | 28 | 0 | 8 |
| lgbtq_student_rights | 0.966 | 0.718 | 0.824 | 39 | 28 | 1 | 11 |
| transgender_policy | 1.000 | 0.750 | 0.857 | 20 | 15 | 0 | 5 |
| gender_identity | 0.824 | 0.737 | 0.778 | 19 | 14 | 3 | 5 |
| school_board_election | 1.000 | 0.786 | 0.880 | 14 | 11 | 0 | 3 |
| advocacy_organizing | 1.000 | 0.800 | 0.889 | 10 | 8 | 0 | 2 |

## Per-label one-vs-rest — action_types

| Label | Precision | Recall | F1 | Support | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| instruction_reduced | 1.000 | 1.000 | 1.000 | 6 | 6 | 0 | 0 |
| instruction_eliminated | 0.750 | 1.000 | 0.857 | 3 | 3 | 1 | 0 |
| protection_adopted | 0.917 | 1.000 | 0.957 | 11 | 11 | 1 | 0 |
| policy_proposed | 0.625 | 0.714 | 0.667 | 7 | 5 | 3 | 2 |
| policy_debated | 1.000 | 0.600 | 0.750 | 10 | 6 | 0 | 4 |
| book_challenged | 1.000 | 0.800 | 0.889 | 10 | 8 | 0 | 2 |

## Per-label one-vs-rest — subtopics

| Label | Precision | Recall | F1 | Support | TP | FP | FN |
|---|---|---|---|---|---|---|---|
| comprehensive | 1.000 | 0.900 | 0.947 | 10 | 9 | 0 | 1 |
| abstinence_only | 0.800 | 1.000 | 0.889 | 4 | 4 | 1 | 0 |
| curriculum_change | 0.643 | 1.000 | 0.783 | 9 | 9 | 5 | 0 |

## Per-chunk mismatches (for manual review)

- **`sex_ed_comprehensive_1`** — subtopics: expected ['comprehensive'] got ['comprehensive', 'curriculum_change']
- **`book_challenge_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship', 'gender_identity', 'lgbtq_student_rights']
- **`parental_rights_1`** — topics: expected ['lgbtq_student_rights', 'parental_rights', 'transgender_policy'] got ['gender_identity', 'lgbtq_student_rights', 'parental_rights', 'transgender_policy']
- **`parental_rights_notification_1`** — topics: expected ['gender_identity', 'lgbtq_student_rights', 'parental_rights'] got ['gender_identity', 'parental_rights']; action_types: expected ['policy_proposed'] got []
- **`trans_policy_debate_1`** — topics: expected ['gender_identity', 'lgbtq_student_rights', 'transgender_policy'] got ['lgbtq_student_rights', 'transgender_policy']
- **`news_media_1`** — action_types: expected ['book_challenged'] got []
- **`policy_proposed_book_review_1`** — topics: expected ['curriculum_censorship', 'parental_rights'] got ['curriculum_censorship']
- **`curriculum_censorship_resolution_1`** — topics: expected ['curriculum_censorship', 'sex_education'] got ['curriculum_censorship']; action_types: expected ['policy_proposed'] got ['policy_proposed', 'protection_adopted']
- **`parental_rights_opt_out_1`** — topics: expected ['parental_rights', 'sex_education'] got ['parental_rights']
- **`book_challenge_formal_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship']
- **`advocacy_organizing_petition_1`** — topics: expected ['advocacy_organizing', 'sex_education'] got ['curriculum_censorship', 'sex_education']; subtopics: expected ['comprehensive'] got ['abstinence_only', 'comprehensive', 'curriculum_change']
- **`multi_topic_sex_ed_trans_1`** — topics: expected ['lgbtq_student_rights', 'parental_rights', 'sex_education', 'transgender_policy'] got ['lgbtq_student_rights', 'parental_rights', 'sex_education']
- **`multi_topic_advocacy_trans_book_1`** — topics: expected ['advocacy_organizing', 'curriculum_censorship', 'gender_identity', 'lgbtq_student_rights', 'transgender_policy'] got ['advocacy_organizing', 'curriculum_censorship', 'lgbtq_student_rights', 'transgender_policy']; action_types: expected ['policy_debated'] got []
- **`multi_topic_election_advocacy_money_1`** — topics: expected ['advocacy_organizing', 'curriculum_censorship', 'parental_rights', 'school_board_election'] got ['advocacy_organizing', 'parental_rights', 'school_board_election']
- **`multi_topic_proposed_debated_1`** — action_types: expected ['policy_debated', 'policy_proposed'] got ['policy_debated']
- **`multi_topic_book_challenge_debate_1`** — action_types: expected ['book_challenged', 'policy_debated'] got ['policy_debated']
- **`multi_topic_sex_ed_curriculum_change_1`** — action_types: expected ['instruction_reduced'] got ['instruction_reduced', 'policy_proposed']
- **`multi_topic_trans_protection_adopted_1`** — topics: expected ['gender_identity', 'lgbtq_student_rights', 'parental_rights', 'transgender_policy'] got ['gender_identity', 'lgbtq_student_rights', 'transgender_policy']
- **`multi_topic_book_challenge_eliminated_1`** — topics: expected ['curriculum_censorship', 'gender_identity', 'lgbtq_student_rights'] got ['curriculum_censorship', 'lgbtq_student_rights']; action_types: expected ['book_challenged'] got ['book_challenged', 'instruction_eliminated']
- **`multi_topic_election_lgbtq_1`** — topics: expected ['curriculum_censorship', 'gender_identity', 'lgbtq_student_rights', 'parental_rights', 'school_board_election', 'transgender_policy'] got ['curriculum_censorship', 'lgbtq_student_rights', 'parental_rights', 'school_board_election', 'transgender_policy']
- **`multi_topic_advocacy_lgbtq_organizing_1`** — topics: expected ['advocacy_organizing', 'curriculum_censorship', 'lgbtq_student_rights', 'school_board_election', 'transgender_policy'] got ['advocacy_organizing', 'lgbtq_student_rights']
- **`action_proposed_only_1`** — topics: expected ['curriculum_censorship', 'parental_rights'] got ['curriculum_censorship']
- **`action_debated_only_1`** — topics: expected ['lgbtq_student_rights', 'parental_rights', 'transgender_policy'] got ['parental_rights']
- **`action_book_challenged_only_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship']
- **`off_topic_consent_agenda_no_topic_1`** — off_topic: expected False got True
- **`ambiguous_passing_mention_sex_ed_1`** — subtopics: expected [] got ['curriculum_change']
- **`ambiguous_passing_mention_lgbtq_1`** — topics: expected ['transgender_policy'] got ['gender_identity', 'lgbtq_student_rights', 'transgender_policy']
- **`ambiguous_book_review_generic_1`** — topics: expected ['curriculum_censorship', 'parental_rights'] got []
- **`ambiguous_news_unrelated_state_1`** — topics: expected ['parental_rights'] got []
- **`edge_long_motion_text_1`** — topics: expected ['gender_identity', 'lgbtq_student_rights', 'parental_rights', 'sex_education'] got ['gender_identity', 'parental_rights', 'sex_education']; action_types: expected ['instruction_reduced'] got ['instruction_reduced', 'policy_proposed']; subtopics: expected ['abstinence_only', 'comprehensive', 'curriculum_change'] got ['abstinence_only', 'curriculum_change']
- **`edge_mixed_minutes_with_action_1`** — action_types: expected ['policy_debated', 'protection_adopted'] got ['protection_adopted']
- **`edge_ocr_artifact_1`** — subtopics: expected ['comprehensive'] got ['comprehensive', 'curriculum_change']
- **`edge_news_letter_to_editor_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights', 'parental_rights'] got ['curriculum_censorship']
- **`misc_education_news_advocacy_1`** — topics: expected ['advocacy_organizing', 'curriculum_censorship', 'parental_rights', 'school_board_election'] got ['advocacy_organizing', 'parental_rights']
- **`misc_book_reinstated_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship']; action_types: expected ['book_challenged', 'policy_debated'] got ['book_challenged']
- **`misc_pride_event_1`** — topics: expected ['gender_identity', 'lgbtq_student_rights', 'parental_rights'] got ['gender_identity', 'lgbtq_student_rights']
- **`misc_agenda_item_only_1`** — action_types: expected ['policy_debated'] got ['policy_proposed']
- **`misc_intervention_letter_1`** — topics: expected ['advocacy_organizing', 'lgbtq_student_rights', 'parental_rights', 'transgender_policy'] got ['advocacy_organizing', 'lgbtq_student_rights', 'parental_rights']
- **`misc_candidate_forum_1`** — topics: expected ['curriculum_censorship', 'parental_rights', 'school_board_election'] got ['curriculum_censorship', 'school_board_election']
- **`misc_book_challenge_outcome_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship']
- **`misc_advocacy_letter_parents_1`** — topics: expected ['advocacy_organizing', 'sex_education'] got ['sex_education']; subtopics: expected ['comprehensive'] got ['comprehensive', 'curriculum_change']
- **`misc_book_challenge_pending_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights'] got ['curriculum_censorship']
- **`misc_election_candidate_stance_1`** — topics: expected ['curriculum_censorship', 'gender_identity', 'lgbtq_student_rights', 'parental_rights', 'school_board_election', 'sex_education', 'transgender_policy'] got ['curriculum_censorship', 'lgbtq_student_rights', 'parental_rights', 'sex_education']
- **`misc_minutes_with_two_actions_1`** — topics: expected ['lgbtq_student_rights', 'parental_rights', 'sex_education'] got ['parental_rights', 'sex_education']
- **`misc_election_endorsement_1`** — topics: expected ['curriculum_censorship', 'lgbtq_student_rights', 'parental_rights', 'school_board_election', 'sex_education'] got ['curriculum_censorship', 'parental_rights', 'school_board_election', 'sex_education']
