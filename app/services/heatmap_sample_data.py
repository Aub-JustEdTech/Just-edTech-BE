from app.schemas.heatmap import CitationItem, DistrictScoreItem, KeywordItem

KEYWORDS: list[KeywordItem] = [
    KeywordItem(id=1, label="Book Ban"),
    KeywordItem(id=2, label="Budget Cuts"),
    KeywordItem(id=3, label="DEI"),
    KeywordItem(id=4, label="School Safety"),
    KeywordItem(id=5, label="Special Education"),
]

# ── Where should we invest resources? ────────────────────────────────────────

INVEST_RESOURCES_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=245, conversation_count=7, source_count=3),
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Lawrence School District",     intensity_score=172, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="New Bedford School District",  intensity_score=145, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Holyoke School District",      intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Fall River School District",   intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Lynn School District",         intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Worcester School District",    intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Fitchburg School District",    intensity_score=51,  conversation_count=2, source_count=1),
]

INVEST_RESOURCES_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Springfield 2024 Education Funding Gap Report",
        date="2024-02-10",
        snippet=(
            "Springfield schools face a $14M shortfall in per-pupil spending relative to the "
            "state average. Facilities maintenance and special education staffing are the most "
            "acute gaps identified by the superintendent's office."
        ),
        source_url="https://example.com/documents/springfield-funding-gap-2024.pdf",
        relevance_score=0.391,
        page_number=4,
    ),
    CitationItem(
        document_id="1",
        document_title="Brockton Infrastructure & Technology Needs Assessment",
        date="2024-03-18",
        snippet=(
            "Three of Brockton's seven elementary schools were built before 1965 and require "
            "significant HVAC and electrical upgrades. The district estimates $22M in deferred "
            "capital investment needed over the next five years."
        ),
        source_url="https://example.com/documents/brockton-needs-assessment-2024.pdf",
        relevance_score=0.354,
        page_number=9,
    ),
    CitationItem(
        document_id="1",
        document_title="Massachusetts Low-Income District Resource Allocation Review",
        date="2024-01-30",
        snippet=(
            "A statewide review found that districts serving the highest proportions of "
            "low-income students receive on average 18% less discretionary funding per pupil "
            "than their wealthier counterparts, exacerbating existing achievement gaps."
        ),
        source_url="https://example.com/documents/ma-resource-allocation-2024.pdf",
        relevance_score=0.318,
        page_number=6,
    ),
]

# ── Which candidates should we support? ──────────────────────────────────────

CANDIDATES_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Newton School District",       intensity_score=245, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Lexington School District",    intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Framingham School District",   intensity_score=172, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Quincy School District",       intensity_score=145, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Waltham School District",      intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Medford School District",      intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Somerville School District",   intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Cambridge School District",    intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Brookline School District",    intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Malden School District",       intensity_score=51,  conversation_count=2, source_count=1),
]

CANDIDATES_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Newton School Committee Election Voter Guide 2024",
        date="2024-09-05",
        snippet=(
            "Three incumbent school committee members face challengers in November. Key issues "
            "include the district's literacy curriculum adoption and proposed redistricting. "
            "The teachers' union has endorsed two of the three challengers."
        ),
        source_url="https://example.com/documents/newton-voter-guide-2024.pdf",
        relevance_score=0.402,
        page_number=2,
    ),
    CitationItem(
        document_id="1",
        document_title="Lexington School Board Candidate Forum Transcript",
        date="2024-09-22",
        snippet=(
            "Candidates were asked about positions on social-emotional learning programs, "
            "gifted education funding, and library collection policies. The forum drew over "
            "300 attendees, the highest turnout for a school board event in a decade."
        ),
        source_url="https://example.com/documents/lexington-forum-transcript-2024.pdf",
        relevance_score=0.367,
        page_number=11,
    ),
    CitationItem(
        document_id="1",
        document_title="Framingham School Committee Race — PAC Spending Analysis",
        date="2024-10-01",
        snippet=(
            "Outside PAC spending in Framingham's school committee race has exceeded $85,000 — "
            "an unusual level for a local school board contest. Education advocacy groups on "
            "both sides cite curriculum and book access policies as the motivating issues."
        ),
        source_url="https://example.com/documents/framingham-pac-analysis-2024.pdf",
        relevance_score=0.331,
        page_number=7,
    ),
]

# ── Which districts are at risk? ──────────────────────────────────────────────

DISTRICTS_AT_RISK_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Boston School District",       intensity_score=245, conversation_count=7, source_count=3),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Lawrence School District",     intensity_score=172, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=145, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Holyoke School District",      intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Worcester School District",    intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Fall River School District",   intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Lynn School District",         intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="New Bedford School District",  intensity_score=51,  conversation_count=2, source_count=1),
]

DISTRICTS_AT_RISK_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Boston Public Schools Academic Recovery Plan 2024",
        date="2024-03-12",
        snippet=(
            "The state education department identified 14 Boston schools as requiring "
            "comprehensive support under the ESSA framework. Chronic absenteeism rates remain "
            "above 30% in six of those schools, hampering post-pandemic recovery efforts."
        ),
        source_url="https://example.com/documents/boston-recovery-plan-2024.pdf",
        relevance_score=0.415,
        page_number=3,
    ),
    CitationItem(
        document_id="1",
        document_title="Lowell Academic Performance & Equity Gap Report",
        date="2024-02-28",
        snippet=(
            "Third-grade reading proficiency in Lowell stands at 41%, compared to a statewide "
            "average of 57%. The report flags growing disparities between ELL students and "
            "native English speakers as the district's most pressing at-risk indicator."
        ),
        source_url="https://example.com/documents/lowell-equity-gap-2024.pdf",
        relevance_score=0.378,
        page_number=8,
    ),
    CitationItem(
        document_id="1",
        document_title="Springfield Fiscal Distress & Enrollment Decline Audit",
        date="2024-04-05",
        snippet=(
            "Springfield has lost 2,100 students over the past five years, triggering a "
            "downward spiral in state aid. The auditor's report warns the district may face "
            "receivership if enrollment continues to decline at the current rate."
        ),
        source_url="https://example.com/documents/springfield-fiscal-audit-2024.pdf",
        relevance_score=0.342,
        page_number=5,
    ),
]

# ── What issues are driving conflict? ─────────────────────────────────────────

CONFLICT_ISSUES_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Cambridge School District",    intensity_score=245, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Salem School District",        intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Northampton School District",  intensity_score=172, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Amherst School District",      intensity_score=145, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Somerville School District",   intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Boston School District",       intensity_score=97,  conversation_count=3, source_count=2),
    DistrictScoreItem(district_name="Newton School District",       intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Brookline School District",    intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Medford School District",      intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Gloucester School District",   intensity_score=51,  conversation_count=2, source_count=1),
]

CONFLICT_ISSUES_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Cambridge School Committee — Curriculum Controversy Minutes",
        date="2024-04-17",
        snippet=(
            "A proposed revision to the K-8 history curriculum sparked a four-hour public "
            "comment session. Parents on opposing sides cited concerns about both cultural "
            "representation and age-appropriateness. The vote was tabled pending a community review."
        ),
        source_url="https://example.com/documents/cambridge-curriculum-controversy-2024.pdf",
        relevance_score=0.408,
        page_number=5,
    ),
    CitationItem(
        document_id="1",
        document_title="Salem Library Book Challenge — Board Hearing Summary",
        date="2024-05-09",
        snippet=(
            "The Salem school board received 47 formal book challenge requests in the 2023-24 "
            "school year — up from 3 the previous year. The board voted 4-3 to retain all "
            "challenged titles pending completion of a new materials review policy."
        ),
        source_url="https://example.com/documents/salem-book-challenge-2024.pdf",
        relevance_score=0.371,
        page_number=2,
    ),
    CitationItem(
        document_id="1",
        document_title="Northampton Community Forum on School Policy Conflicts",
        date="2024-03-30",
        snippet=(
            "Northampton's public forum on inclusive school policies drew 400 residents. "
            "Tensions centered on gender identity guidelines for student records, with "
            "community members sharply divided over parental notification requirements."
        ),
        source_url="https://example.com/documents/northampton-forum-2024.pdf",
        relevance_score=0.334,
        page_number=9,
    ),
]

# ── Which messages resonate with voters? ──────────────────────────────────────

VOTER_MESSAGES_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Quincy School District",       intensity_score=245, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Framingham School District",   intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Waltham School District",      intensity_score=172, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Plymouth School District",     intensity_score=145, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Weymouth School District",     intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Braintree School District",    intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Needham School District",      intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Natick School District",       intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Lexington School District",    intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Burlington School District",   intensity_score=51,  conversation_count=2, source_count=1),
]

VOTER_MESSAGES_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Quincy Voter Survey — Education Priority Index 2024",
        date="2024-06-14",
        snippet=(
            "Survey respondents in Quincy ranked 'school safety' and 'teacher retention' as "
            "their top two education priorities, outranking curriculum content by a wide margin. "
            "Messages focused on classroom stability tested 22 points higher than policy-focused framing."
        ),
        source_url="https://example.com/documents/quincy-voter-survey-2024.pdf",
        relevance_score=0.397,
        page_number=6,
    ),
    CitationItem(
        document_id="1",
        document_title="Framingham Focus Group Report — School Board Messaging",
        date="2024-07-02",
        snippet=(
            "Focus groups of Framingham swing voters responded most positively to messages "
            "emphasizing 'keeping politics out of the classroom' and 'investing in basics.' "
            "Highly partisan messaging on either side drove unfavorable ratings above 60%."
        ),
        source_url="https://example.com/documents/framingham-focus-group-2024.pdf",
        relevance_score=0.362,
        page_number=4,
    ),
    CitationItem(
        document_id="1",
        document_title="Massachusetts Suburban Voter Education Poll — Fall 2024",
        date="2024-09-18",
        snippet=(
            "Statewide polling of suburban school districts found that 68% of likely voters "
            "prioritize 'academic outcomes over social issues' when evaluating school board "
            "candidates. Reading and math proficiency data is the most trusted performance signal."
        ),
        source_url="https://example.com/documents/ma-suburban-voter-poll-2024.pdf",
        relevance_score=0.329,
        page_number=12,
    ),
]

# ── Book Ban ──────────────────────────────────────────────────────────────────

BOOK_BAN_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Boston School District",       intensity_score=245, conversation_count=8, source_count=3),
    DistrictScoreItem(district_name="Salem School District",        intensity_score=210, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Cambridge School District",    intensity_score=183, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Northampton School District",  intensity_score=154, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Amherst School District",      intensity_score=127, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Brookline School District",    intensity_score=101, conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Somerville School District",   intensity_score=88,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Newton School District",       intensity_score=72,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Lexington School District",    intensity_score=55,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Gloucester School District",   intensity_score=34,  conversation_count=1, source_count=1),
]

BOOK_BAN_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Boston School Board — Library Materials Review Hearing",
        date="2024-02-14",
        snippet=(
            "Parents and educators packed the February hearing to debate removal of 12 titles "
            "from high school libraries. The board voted 5-2 to retain all books pending a "
            "formal review process to be completed by June 2024."
        ),
        source_url="https://example.com/documents/boston-library-review-2024.pdf",
        relevance_score=0.421,
        page_number=2,
    ),
    CitationItem(
        document_id="1",
        document_title="Salem Book Challenge — Formal Complaints Summary 2024",
        date="2024-04-03",
        snippet=(
            "Salem received 47 formal book challenge requests this academic year — a 15x "
            "increase from 2022. Most challenges targeted titles dealing with LGBTQ+ themes "
            "and racially diverse narratives in grades 6 through 10."
        ),
        source_url="https://example.com/documents/salem-challenges-2024.pdf",
        relevance_score=0.387,
        page_number=5,
    ),
    CitationItem(
        document_id="1",
        document_title="Massachusetts Library Freedom Coalition — Statewide Report",
        date="2024-05-21",
        snippet=(
            "The coalition documented 134 distinct book challenge incidents across 22 districts "
            "in 2023-24. Eastern Massachusetts districts accounted for 61% of all reported "
            "incidents, with contested titles spanning fiction, memoir, and graphic novels."
        ),
        source_url="https://example.com/documents/ma-library-freedom-report-2024.pdf",
        relevance_score=0.352,
        page_number=8,
    ),
]

# ── Budget Cuts ───────────────────────────────────────────────────────────────

BUDGET_CUTS_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=245, conversation_count=8, source_count=3),
    DistrictScoreItem(district_name="Worcester School District",    intensity_score=208, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Holyoke School District",      intensity_score=179, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Fall River School District",   intensity_score=152, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Lawrence School District",     intensity_score=131, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=108, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Fitchburg School District",    intensity_score=87,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="New Bedford School District",  intensity_score=69,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Lynn School District",         intensity_score=48,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=27,  conversation_count=1, source_count=1),
]

BUDGET_CUTS_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Springfield FY2025 Budget Reduction Plan",
        date="2024-03-07",
        snippet=(
            "The Springfield school committee approved $8.2M in budget reductions for FY2025, "
            "eliminating 34 teaching positions and cutting all middle school arts programs. "
            "The superintendent cited declining state Chapter 70 aid as the primary driver."
        ),
        source_url="https://example.com/documents/springfield-budget-2025.pdf",
        relevance_score=0.433,
        page_number=3,
    ),
    CitationItem(
        document_id="1",
        document_title="Worcester District Finance Committee — Q1 2024 Review",
        date="2024-04-15",
        snippet=(
            "Worcester's finance committee flagged a projected $5.7M deficit for the upcoming "
            "fiscal year. Proposed cuts include reducing paraprofessional staff by 20% and "
            "consolidating two elementary schools into one campus."
        ),
        source_url="https://example.com/documents/worcester-finance-q1-2024.pdf",
        relevance_score=0.396,
        page_number=7,
    ),
    CitationItem(
        document_id="1",
        document_title="MassEd Budget Crisis Coalition — Legislative Testimony",
        date="2024-05-08",
        snippet=(
            "Representatives from 14 districts testified before the Joint Committee on Education "
            "about the cascading impact of budget cuts on special education services, after-school "
            "programs, and school counselor ratios across underserved communities."
        ),
        source_url="https://example.com/documents/massed-testimony-2024.pdf",
        relevance_score=0.361,
        page_number=11,
    ),
]

# ── DEI ───────────────────────────────────────────────────────────────────────

DEI_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Cambridge School District",    intensity_score=245, conversation_count=8, source_count=3),
    DistrictScoreItem(district_name="Boston School District",       intensity_score=214, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Somerville School District",   intensity_score=187, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Northampton School District",  intensity_score=159, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Brookline School District",    intensity_score=133, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Newton School District",       intensity_score=109, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Amherst School District",      intensity_score=84,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Medford School District",      intensity_score=66,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Malden School District",       intensity_score=47,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Framingham School District",   intensity_score=29,  conversation_count=1, source_count=1),
]

DEI_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Cambridge DEI Strategic Plan 2024–2027",
        date="2024-01-22",
        snippet=(
            "Cambridge launched a three-year DEI strategic plan targeting hiring equity, "
            "inclusive curriculum adoption, and student affinity group support. The plan "
            "allocates $1.2M annually and establishes a community advisory board."
        ),
        source_url="https://example.com/documents/cambridge-dei-plan-2024.pdf",
        relevance_score=0.418,
        page_number=4,
    ),
    CitationItem(
        document_id="1",
        document_title="Boston Equity in Education Task Force — Interim Report",
        date="2024-03-19",
        snippet=(
            "The task force found significant disparities in advanced coursework enrollment "
            "by race and zip code. Black and Latino students are underrepresented in AP and "
            "honors tracks by 28% relative to their share of total enrollment."
        ),
        source_url="https://example.com/documents/boston-equity-task-force-2024.pdf",
        relevance_score=0.381,
        page_number=9,
    ),
    CitationItem(
        document_id="1",
        document_title="Somerville Community Forum — DEI Program Feedback Session",
        date="2024-04-30",
        snippet=(
            "Over 250 Somerville parents and staff attended a public forum on the district's "
            "DEI training program. Feedback was mixed — supporters cited cultural responsiveness "
            "gains while critics questioned curriculum time trade-offs."
        ),
        source_url="https://example.com/documents/somerville-dei-forum-2024.pdf",
        relevance_score=0.344,
        page_number=6,
    ),
]

# ── School Safety ─────────────────────────────────────────────────────────────

SCHOOL_SAFETY_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=245, conversation_count=8, source_count=3),
    DistrictScoreItem(district_name="Lynn School District",         intensity_score=217, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Lawrence School District",     intensity_score=191, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=163, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=138, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="New Bedford School District",  intensity_score=112, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Fall River School District",   intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Haverhill School District",    intensity_score=67,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Fitchburg School District",    intensity_score=44,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Holyoke School District",      intensity_score=22,  conversation_count=1, source_count=1),
]

SCHOOL_SAFETY_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Brockton School Safety Assessment — Spring 2024",
        date="2024-03-25",
        snippet=(
            "Brockton's safety audit identified gaps in visitor screening protocols at 4 of 9 "
            "elementary schools. The report recommends upgrading entry systems and adding a "
            "dedicated safety coordinator role at each building by September 2024."
        ),
        source_url="https://example.com/documents/brockton-safety-audit-2024.pdf",
        relevance_score=0.426,
        page_number=3,
    ),
    CitationItem(
        document_id="1",
        document_title="Lynn Threat Assessment Protocol — Annual Review",
        date="2024-04-11",
        snippet=(
            "Lynn's threat assessment team handled 23 student-related incidents in 2023-24, "
            "a 40% increase from the prior year. The review attributes the rise to post-pandemic "
            "social adjustment challenges and recommends expanded mental health staffing."
        ),
        source_url="https://example.com/documents/lynn-threat-assessment-2024.pdf",
        relevance_score=0.389,
        page_number=7,
    ),
    CitationItem(
        document_id="1",
        document_title="Massachusetts DESE School Climate Survey Results 2024",
        date="2024-05-14",
        snippet=(
            "Statewide climate survey data shows 31% of students in high-need districts report "
            "feeling unsafe at school at least once per month. Districts with dedicated mental "
            "health counselors show a 19% lower rate of reported safety concerns."
        ),
        source_url="https://example.com/documents/dese-climate-survey-2024.pdf",
        relevance_score=0.353,
        page_number=12,
    ),
]

# ── Special Education ─────────────────────────────────────────────────────────

SPECIAL_ED_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Boston School District",       intensity_score=245, conversation_count=8, source_count=3),
    DistrictScoreItem(district_name="Worcester School District",    intensity_score=203, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=176, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=148, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=124, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Newton School District",       intensity_score=99,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Quincy School District",       intensity_score=78,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Framingham School District",   intensity_score=57,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Waltham School District",      intensity_score=38,  conversation_count=1, source_count=1),
    DistrictScoreItem(district_name="Malden School District",       intensity_score=19,  conversation_count=1, source_count=1),
]

SPECIAL_ED_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="Boston Special Education Compliance Review 2024",
        date="2024-02-06",
        snippet=(
            "DESE's compliance review found Boston out of compliance on IEP meeting timelines "
            "in 18% of reviewed cases. The district has 90 days to submit a corrective action "
            "plan addressing evaluation delays and transition planning gaps."
        ),
        source_url="https://example.com/documents/boston-sped-compliance-2024.pdf",
        relevance_score=0.437,
        page_number=2,
    ),
    CitationItem(
        document_id="1",
        document_title="Worcester SPED Parent Advisory Council — Annual Report",
        date="2024-04-22",
        snippet=(
            "The PAC report highlights a 14-month average wait time for initial evaluations in "
            "Worcester. Parents cite inadequate communication and shortage of specialized staff "
            "as the top barriers to timely and appropriate services."
        ),
        source_url="https://example.com/documents/worcester-sped-pac-2024.pdf",
        relevance_score=0.401,
        page_number=6,
    ),
    CitationItem(
        document_id="1",
        document_title="Massachusetts SPED Funding Gap Analysis — FY2024",
        date="2024-05-30",
        snippet=(
            "A UMass study found Massachusetts districts collectively under-fund special "
            "education by $340M annually when accounting for actual service costs vs. state "
            "reimbursement rates. High-need urban districts bear the largest funding shortfall."
        ),
        source_url="https://example.com/documents/ma-sped-funding-gap-2024.pdf",
        relevance_score=0.364,
        page_number=9,
    ),
]

# ── Default fallback ──────────────────────────────────────────────────────────

SAMPLE_DISTRICT_SCORES: list[DistrictScoreItem] = [
    DistrictScoreItem(district_name="Boston School District",       intensity_score=245, conversation_count=7, source_count=2),
    DistrictScoreItem(district_name="Cambridge School District",    intensity_score=198, conversation_count=6, source_count=2),
    DistrictScoreItem(district_name="Worcester School District",    intensity_score=172, conversation_count=5, source_count=2),
    DistrictScoreItem(district_name="Springfield School District",  intensity_score=145, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Lowell School District",       intensity_score=118, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Brockton School District",     intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Lynn School District",         intensity_score=89,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Newton School District",       intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Salem School District",        intensity_score=62,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="New Bedford School District",  intensity_score=51,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Somerville School District",   intensity_score=38,  conversation_count=1, source_count=1),
    DistrictScoreItem(district_name="Framingham School District",   intensity_score=31,  conversation_count=1, source_count=1),
    DistrictScoreItem(district_name="Quincy School District",       intensity_score=24,  conversation_count=1, source_count=1),
    DistrictScoreItem(district_name="Malden School District",       intensity_score=17,  conversation_count=1, source_count=1),
    DistrictScoreItem(district_name="Medford School District",      intensity_score=11,  conversation_count=1, source_count=1),
]

SAMPLE_CITATIONS: list[CitationItem] = [
    CitationItem(
        document_id="1",
        document_title="January 2024 School Board Meeting",
        date="2024-01-18",
        snippet=(
            "The board reviewed district performance metrics and discussed upcoming policy "
            "changes affecting library materials and curriculum standards."
        ),
        source_url="https://example.com/documents/board-jan-2024.pdf",
        relevance_score=0.385,
        page_number=3,
    ),
    CitationItem(
        document_id="1",
        document_title="March 2024 Curriculum Review Report",
        date="2024-03-05",
        snippet=(
            "The curriculum committee reviewed district reading lists in response to parent "
            "concerns. Three books were flagged for further review by the superintendent."
        ),
        source_url="https://example.com/documents/curriculum-march-2024.pdf",
        relevance_score=0.341,
        page_number=7,
    ),
    CitationItem(
        document_id="1",
        document_title="April 2024 Special Session Minutes",
        date="2024-04-22",
        snippet=(
            "A special session was convened to address the ongoing debate over library "
            "materials. Community members submitted written testimony on both sides."
        ),
        source_url="https://example.com/documents/special-session-april-2024.pdf",
        relevance_score=0.298,
        page_number=12,
    ),
]

# ── Keyword → data map ────────────────────────────────────────────────────────

KEYWORD_DATA: dict[str, tuple[list[DistrictScoreItem], list[CitationItem]]] = {
    # Pre-defined keyword chips
    "book ban":          (BOOK_BAN_SCORES,       BOOK_BAN_CITATIONS),
    "budget cuts":       (BUDGET_CUTS_SCORES,    BUDGET_CUTS_CITATIONS),
    "dei":               (DEI_SCORES,            DEI_CITATIONS),
    "school safety":     (SCHOOL_SAFETY_SCORES,  SCHOOL_SAFETY_CITATIONS),
    "special education": (SPECIAL_ED_SCORES,     SPECIAL_ED_CITATIONS),
    # Free-text query examples
    "where should we invest resources?":    (INVEST_RESOURCES_SCORES,   INVEST_RESOURCES_CITATIONS),
    "which candidates should we support?":  (CANDIDATES_SCORES,         CANDIDATES_CITATIONS),
    "which districts are at risk?":         (DISTRICTS_AT_RISK_SCORES,  DISTRICTS_AT_RISK_CITATIONS),
    "what issues are driving conflict?":    (CONFLICT_ISSUES_SCORES,    CONFLICT_ISSUES_CITATIONS),
    "which messages resonate with voters?": (VOTER_MESSAGES_SCORES,     VOTER_MESSAGES_CITATIONS),
}
