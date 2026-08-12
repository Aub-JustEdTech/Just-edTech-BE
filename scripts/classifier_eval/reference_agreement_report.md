# Chunk Classifier — Run-to-Run Agreement Report

**Caveat:** the reference collection is an earlier automated run of this same LLM classifier (2026-08-04 snapshot), not human-verified ground truth. This measures agreement/drift between two runs, not real-world accuracy. For a true accuracy gate, see `scripts/classifier_eval/eval_report.md` (hand-labeled set).

## Matching coverage

- Reference documents: 454
- Live documents: 480
- Overlapping documents: 454
- Excluded (chunking drifted between runs): 161
- Included (identical chunk_index sets): 293
- Chunks excluded (text drifted despite matching index): 185
- **Chunks compared: 3323**

## Headline numbers

- **Macro F1 (labels with support > 0): 1.000**
- **off_topic agreement rate: 0.989**
  - reference=off_topic, live=on_topic (live surfaced something the earlier run missed): 16
  - reference=on_topic, live=off_topic (live now misses something the earlier run caught): 19

## Per-field micro-averaged agreement

| Field | Precision | Recall | F1 |
|---|---|---|---|
| topics | 1.000 | 1.000 | 1.000 |
| action_types | 1.000 | 1.000 | 1.000 |
| subtopics | 1.000 | 1.000 | 1.000 |

## Per-label one-vs-rest — topics

| Label | Precision | Recall | F1 | Support | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|
| sex_education | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |
| curriculum_censorship | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |
| parental_rights | 1.000 | 1.000 | 1.000 | 4 | 4 | 0 | 0 | 3319 |
| lgbtq_student_rights | 1.000 | 1.000 | 1.000 | 35 | 35 | 0 | 0 | 3288 |
| transgender_policy | 1.000 | 1.000 | 1.000 | 12 | 12 | 0 | 0 | 3311 |
| gender_identity | 1.000 | 1.000 | 1.000 | 30 | 30 | 0 | 0 | 3293 |
| school_board_election | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |
| advocacy_organizing | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |

## Per-label one-vs-rest — action_types

| Label | Precision | Recall | F1 | Support | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|
| instruction_reduced | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |
| instruction_eliminated | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |
| protection_adopted | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |
| policy_proposed | 1.000 | 1.000 | 1.000 | 1 | 1 | 0 | 0 | 3322 |
| policy_debated | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |
| book_challenged | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |

## Per-label one-vs-rest — subtopics

| Label | Precision | Recall | F1 | Support | TP | FP | FN | TN |
|---|---|---|---|---|---|---|---|---|
| comprehensive | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |
| abstinence_only | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |
| curriculum_change | 0.000 | 0.000 | 0.000 | 0 | 0 | 0 | 0 | 3323 |

## off_topic disagreements (all cases)

`ref->live` shows the flip direction. All chunks below had identical, empty topics/action_types/subtopics on both sides -- the *only* disagreement is whether the chunk counts as on-topic at all.

- **False->True** `school-01530000-723978a5...` chunk 42: "knows only that the gift is from the class, not from specific donors. A single class gift per calendar year valued up to $150 or several class gifts in a single year with a total value up to $150 from parents/guardians a..."
- **False->True** `school-01530000-46e8e317...` chunk 3: "8 1G140 -Educator Quality 5,562.72 0.00] $ 5,562.72 G180 - LEP Support 2,551.43 0.00] $ 2,551.43 16186 - Immigrant Student Program 1,650.00 0.00] $ 1,650.00 G237 - Coord Family Engagement 4,516.94 195.19] $ 4,712.13 G240..."
- **True->False** `school-00410000-045ca7cd...` chunk 0: "NAUSET REGIONAL SCHOOLS Oct-23 ENROLLMENT TRENDS HIGH As of Live in Live in Live in School OTHER SCHOOL 1-Oct District Truro Provincetown Choice IN STUDENTS TOTAL 2023 575 52 22 122 771 2022 587 54 23 136 800 2021 618 60..."
- **True->False** `school-08100000-1e9d64d4...` chunk 8: "4/10/24, 10:33 AM BoardDocs® Pro Madison Gilbert discussed the District Conference in January, reported the number of medals and trophies received, and fundraising events. Dr. Magalhaes stated we also received an acknowl..."
- **True->False** `school-00430000-08353d4d...` chunk 4: "Brimfield Elempntary Brimfield Elementary School - Budgeting Historical Details Fiscal 2023 Fiscal 2025 Budget Expenditure Budget 1000 Series Administration School Committee 110/Legal Services - 25|Legal Notices 600 035|..."
- **False->True** `school-00360000-2e4312e1...` chunk 7: "My } Bourne Public Schools response rate from families has been positive. She notes that districts across the state are completing the Special Ed Increment Forms for school choice due by the end of the week. ° Technology..."
- **False->True** `school-00360000-2e4312e1...` chunk 11: ",) Bourne Public Schools e BHS-Jessee Clemenst shares events happening at BHS including: BHS Art Show and Cabaret, Hidden in Plain Sight exhibit, mock car crash, the Think Fast program-a state police funded program on sa..."
- **False->True** `school-00410000-400faca1...` chunk 37: "ship and support for the faculty Empowering Subject Coordinators ° Structured data reflection within departments Adopting new curriculum and adjusting scope/sequence in Science..."
- **False->True** `school-00410000-87fe7ce2...` chunk 68: "tation apportionment: Apportionment for transportation under the current agreement and in this draft amendment is treated as an Operating Cost and apportioned on the same basis as all other Operating Costs. DESE is likel..."
- **True->False** `school-00410000-87fe7ce2...` chunk 50: "C. The Committee may accept for enrollment in the regional district school students from towns other than the member towns on a tuition bases and on such terms as it may determine. Income received by the District with re..."
- **False->True** `school-00410000-87fe7ce2...` chunk 28: "Student Enrollment in publicly-funded charter schools and other public schools of choice shall be calculated using figures published by DESE in its October 1 Foundation Enrollment Report 3 for the three preceding fiscal ..."
- **True->False** `school-00410000-87fe7ce2...` chunk 46: "Following the public hearing on the proposed budget, the Committee may make any such modifications to its proposed budget as it may deem necessary or desirable before voting to adopt a final operating and maintenance bud..."
- **False->True** `school-01530000-f5b91433...` chunk 24: "50,727 548,679.86 102,046.80 0.00 3600 3600 SCHOOL SECURITY sal 101,985.50 - 101,986 86,380.14 15,505.48 99.88 4110 4110 CUSTODIAL SERVICES sal _2,321,000.92 - 2,321,001 1,915,049.91 405,951.01 - 4400 4400 TECH INFRA, MA..."
- **False->True** `school-01530000-f5b91433...` chunk 15: ".00 15,323.30 39,676.70 = 1450 1450 DISTRICTWIDE MIS AND TECH exp 248,328.80 (42,615.76) 205,713.04 206,776.81 57,175.60 (58,239.37) 2110 2110 CURRICULUM DIRECTORS exp 106,460.00 215,091.44 321,551.44 116,487.86 125,465...."
- **False->True** `school-01530000-f5b91433...` chunk 22: "4.08 28,786.86 20,702.88 2130 2130 INSTRUCTIONAL TECH LEAD & sal 89,822.50 : 89,823 63,203.95 9,705.76 16,912.79 2210 2210 SCHOOL LEADERSHIP-BUILDIN sal 4,149,060.34 (55,000.00) 4,094,060 3,398,727.43 664,758.53 30,574.3..."
- **True->False** `school-01530000-f5b91433...` chunk 18: "0 878,002.00 759,222.20 175,077.80 (56,298.00) a 4130 4130 UTILITY SERVICES exp —1,147,500.00 300,000.00 —1,447,500.00 916,096.04 484,355.79 47,048.17 A 4210 4210 MAINTENANCE OF GROUNDS exp 244,338.58 (173,980.65) 70,357..."
- **False->True** `school-01530000-68e93c3a...` chunk 40: "9.60 G305 - Title | Reading 42,269.50] 39,000.00| $ _ 81,269.50 G309 - Title \V Academic Enrichment 0.00 71.98] $ 71.98 G400 - Perkins Voc. Ed Skills 0.00] 8,386.34 8,386.34 G426 - CTE Framework 0.00] 2,000.00] $ 2,000.0..."
- **False->True** `school-01530000-68e93c3a...` chunk 37: "4/8/2026 __|FY26 Exp $ 404,144.18 FY26 Transportation Exp _ $ 106,000.00 Total $ 510,144.18 42 4/15/2026 _|FY26 |Exp $ 588,531.99 FY26 Salary Exp $ 2,616,841.50 FY26 Crossing Guard Exp [$1,977.60 FY26 Transportation Exp ..."
- **False->True** `school-01530000-68e93c3a...` chunk 47: "tions 0.00] 6,280.69 6,280.69 2665 - Surround Care Fees 1,177.94] 35,557.30 36,735.24 2675 - School Rental Fees 0.00| _1,677.54| $ 1,677.54 2676 - Student Activity 3,230.74 0.00 3,230.74 2685 - Driver Training 0.00] 2,80..."
- **True->False** `school-03090000-194c1d42...` chunk 2: "sed concern over undetonated shells (as this had been an issue a prior year). Chief Martinez noted they would do a thorough canvasing of the area after the event. He also noted Atlas Fireworks keep a detailed inventory o..."
- **True->False** `school-08100000-b9a1e257...` chunk 9: "he waiting list when they originally decline to come to the school. Mrs. Shaw replied there could be many reasons such as their situation in life has changed or the school they chose to go to did not work out. Additional..."
- **True->False** `school-08100000-b9a1e257...` chunk 13: "9/18/24, 1:22 PM BoardDocs® Pro Final Resolution: Motion Carries Yes: Estele C. Borges, Edward F. Dutra, Jr., Timothy J. Holick, George L. Randall, Ill, Richard J. Spada, Jr., Joseph M. Zinni, Jr. 8. NEW BUSINESS Informa..."
- **True->False** `school-08100000-f834f303...` chunk 9: "ry Board Meeting Update Dr. Magalhaes reported the Executive Advisory Board Meeting was held on May 2 in the Silver Platter. Jackie Machamer, the Vocational Coordinator, discussed equipment received, co-op, certification..."
- **True->False** `school-00410000-cf199973...` chunk 15: "Section 2: Analyze Your Data and Select Student Groups for Focused Support INauset (0660) Public School District - FY 2024 - Student Opportunity Act (SOA) Plan Submission - Rev 0 four work will not automatically be saved..."
- **True->False** `school-00410000-cf199973...` chunk 36: "ne to three Focus Areas your district will prioritize to improve student learning experiences and outcomes for student groups identified in your data analysis. For each Focus Area, select one or more Evidence-Based Progr..."
- **False->True** `school-00410000-cf199973...` chunk 41: "ties, and a weekly meeting time with administration and/or grade level teachers for activates related to the school's SEL goals. Nauset High school is currently working to negotiate a similar advisory period. _ * Which s..."
- **False->True** `school-00410000-cf199973...` chunk 51: "+i EBP 2.2B High Leverage Practices for Students with Disabilities | © EBP 2.2C Collaborative Teaching Models Qu EBP 2.2D Targeted Academic Support and Acceleration + * Provide a short description of what your district h..."
- **True->False** `school-00410000-27b18f14...` chunk 4: "ent of an emergency in one of the schools. Superintendent Clenchy indicated Parents would be informed about this in a upcoming newsletter. Discussion was held on how the new campus will be very secure with a solid perime..."
- **False->True** `school-06250000-972ae0bd...` chunk 7: "Mr. Dolan explained the plan for the BRRSD’s Path to Excellence as: Building and district administrators proposed personnel, goods, and services that support our Student Success Plan and Our Path to Excellence o DESE Com..."
- **False->True** `school-06250000-972ae0bd...` chunk 5: "tion Budget is based on enrollment, wage adjustment, and inflation -Local Contribution is based on property values, income and municipal revenue growth factor Mr. Dolan provided a slide depicting the state minimum contri..."
- **False->True** `school-00410000-09fb7068...` chunk 45: "NAUSET REGIONAL SCHOOL DISTRICT 5. Variance Reporting, Commentary & Projections School Committee Financial Dashboard Reports Month of: 4/30/2024 6 Appropriation Line Item Analysis (Reports Budget Line Items with a varian..."
- **False->True** `school-00410000-09fb7068...` chunk 49: ". IL RO 8729 Charter School Tuition 2,511,073 (1,999,619) (1,056,076) (544,622), = Variance represents an increase in the number of Nauset students leaving the District to attend Charter Schools. The budgeted amount repr..."
- **True->False** `school-00410000-09fb7068...` chunk 18: "Discussion was held on next steps regarding cell phone usage at the schools. Superintendent Clenchy indicated this could be looked at more as practice than policy, and that more data Nauset Regional School Committee May ..."
- **True->False** `school-00360000-d6945266...` chunk 7: "/-/ Bourne Public Schools e@ Dr. Zhou says they talk about planning for the future at every school committee meeting and talks about the different factors that have impacted the downward enrollment trends including less ..."
- **True->False** `school-03090000-7cde0abf...` chunk 0: "Ware School Committee Regular Business Meeting Place: Remote Wednesday, July 26, 2023 PRESENT: Chris Desjardins, Brian Winslow, Mike Foran, Aaron Sawabi, Michael Lovato, and Joan Sawabi Chris Desjardins called the Regula..."

## False-positive / false-negative examples

Up to 5 examples per label. False positive = live tagged it, reference didn't. False negative = reference tagged it, live didn't. (Empty below means zero disagreements found for that label across all 3,323 compared chunks.)
