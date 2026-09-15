"""
Sample data derived from:
  AKF Bear Bulletin — Elementary News 6.12.26
  Abby Kelley Foster Charter Public School, Worcester, MA
  Author: Anne Duke (Principal)

Citations are keyed by lowercase district name so get_district_citations can
filter correctly. Only districts that authored or are directly referenced by
the document have citations — others appear on the map but return an empty list.
"""

from app.schemas.heatmap import CitationItem, DistrictScoreItem, KeywordItem

_DOC_ID = "Elementary-School-Update-June-12-2026.pdf"
_DOC_TITLE = "Elementary-School-Update-June-12-2026.pdf"
_DOC_DATE = "2026-06-12"
_DOC_URL = ""

# Shorthand keys used throughout
_AKFCS = "abby kelley foster charter public (district)"
_WORCESTER = "worcester"

KEYWORDS: list[KeywordItem] = [
    KeywordItem(id=1, label="End of Year"),
    KeywordItem(id=2, label="Medication Policy"),
    KeywordItem(id=3, label="Book Drive"),
    KeywordItem(id=4, label="Family Engagement"),
    KeywordItem(id=5, label="School Transition"),
]


def _akfcs(score: int, convos: int) -> DistrictScoreItem:
    return DistrictScoreItem(
        district_name="Abby Kelley Foster Charter Public (District)",
        intensity_score=score,
        conversation_count=convos,
        source_count=1,
        district_type="charter",
    )


# ── End of Year ───────────────────────────────────────────────────────────────

END_OF_YEAR_SCORES: list[DistrictScoreItem] = [
    _akfcs(245, 8),
    DistrictScoreItem(district_name="Worcester",  intensity_score=162, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Shrewsbury", intensity_score=97,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Millbury",   intensity_score=61,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Auburn",     intensity_score=38,  conversation_count=1, source_count=1),
]

END_OF_YEAR_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Over the next several days, classrooms will be filled with end-of-year celebrations, "
                "activities, and special moments. Some classes will celebrate together in their classrooms, "
                "while others will enjoy a fun movie experience at the high school where students can relax "
                "and enjoy the cool air while spending time with classmates."
            ),
            source_url=_DOC_URL, relevance_score=0.921, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Along with these festivities, we will also have our final Goodies with Grownups of the "
                "year next Wednesday morning on the playground. Students and families are invited to join "
                "us for a special treat, enjoy some time together, and visit with one another before we "
                "head into summer break."
            ),
            source_url=_DOC_URL, relevance_score=0.884, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We have 4 and a half days left of our school year! Let's make it count! "
                "June 19th NO School. June 22nd LAST DAY of CLASSES, Dismissal at 11:45am."
            ),
            source_url=_DOC_URL, relevance_score=0.847, page_number=2,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Over the next several days, classrooms will be filled with end-of-year celebrations, "
                "activities, and special moments. Some classes will celebrate together in their classrooms, "
                "while others will enjoy a fun movie experience at the high school where students can relax "
                "and enjoy the cool air while spending time with classmates."
            ),
            source_url=_DOC_URL, relevance_score=0.901, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We have 4 and a half days left of our school year! Let's make it count! "
                "June 19th NO School. June 22nd LAST DAY of CLASSES, Dismissal at 11:45am."
            ),
            source_url=_DOC_URL, relevance_score=0.832, page_number=2,
        ),
    ],
}


# ── Medication Policy ─────────────────────────────────────────────────────────

MEDICATION_POLICY_SCORES: list[DistrictScoreItem] = [
    _akfcs(231, 7),
    DistrictScoreItem(district_name="Worcester",    intensity_score=148, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="Grafton",      intensity_score=85,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Northborough", intensity_score=52,  conversation_count=2, source_count=1),
]

MEDICATION_POLICY_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Many of our children have daily medications that are given here at school or medications "
                "that are here for emergency purposes. These medications cannot be kept at school during "
                "the summer and must be picked up during dismissal on the last day of school by a parent/"
                "guardian or designated adult. Students cannot transport the medication for safety reasons."
            ),
            source_url=_DOC_URL, relevance_score=0.935, page_number=2,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Any medications left over after the close of school on Monday June 22nd 2026, will be "
                "disposed of. Life Saving medications (Epi pens, Inhalers and Seizure meds) MUST be kept "
                "in the building until the end of the school day on the last day of school. Non-life "
                "saving medications may be picked up sooner."
            ),
            source_url=_DOC_URL, relevance_score=0.898, page_number=2,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "All medications, over the counter or prescription must have an updated doctors order "
                "dated after 6/30/26 (for the upcoming school year). Epi pens, Asthma and Seizure "
                "medications must be accompanied by an updated doctor's order and an action plan dated "
                "after 6/30/26. Medications will not be accepted without all proper documentation."
            ),
            source_url=_DOC_URL, relevance_score=0.861, page_number=3,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Many of our children have daily medications that are given here at school or medications "
                "that are here for emergency purposes. These medications cannot be kept at school during "
                "the summer and must be picked up during dismissal on the last day of school by a parent/"
                "guardian or designated adult. Students cannot transport the medication for safety reasons."
            ),
            source_url=_DOC_URL, relevance_score=0.912, page_number=2,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Any medications left over after the close of school on Monday June 22nd 2026, will be "
                "disposed of. Life Saving medications (Epi pens, Inhalers and Seizure meds) MUST be kept "
                "in the building until the end of the school day on the last day of school."
            ),
            source_url=_DOC_URL, relevance_score=0.874, page_number=2,
        ),
    ],
}


# ── Book Drive ────────────────────────────────────────────────────────────────

BOOK_DRIVE_SCORES: list[DistrictScoreItem] = [
    _akfcs(218, 7),
    DistrictScoreItem(district_name="Worcester", intensity_score=134, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Holden",    intensity_score=79,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Paxton",    intensity_score=47,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Leicester", intensity_score=28,  conversation_count=1, source_count=1),
]

BOOK_DRIVE_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "AKFCS ELEMENTARY SCHOOL BOOK DRIVE — HELP US BUILD OUR CLASSROOM LIBRARIES & SPARK A "
                "LOVE OF READING! What we need: Gently Used Children's Books — Fiction (Chapter books, "
                "Graphic Novels, Picture books, Early Readers) — Non-Fiction (Animals, Science, History, "
                "Biography). Drop off donations at AKFCS Elementary. Donation dates: June 1 – June 23."
            ),
            source_url=_DOC_URL, relevance_score=0.952, page_number=7,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We have already gotten over 300 books. Keep them coming! "
                "For more info, contact Lauren Blumberg at lblumberg@akfcs.org."
            ),
            source_url=_DOC_URL, relevance_score=0.903, page_number=6,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet="YOUR DONATIONS MAKE A DIFFERENCE! THANK YOU FOR SUPPORTING OUR YOUNG READERS!",
            source_url=_DOC_URL, relevance_score=0.856, page_number=7,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "AKFCS ELEMENTARY SCHOOL BOOK DRIVE — HELP US BUILD OUR CLASSROOM LIBRARIES & SPARK A "
                "LOVE OF READING! What we need: Gently Used Children's Books — Fiction (Chapter books, "
                "Graphic Novels, Picture books, Early Readers) — Non-Fiction (Animals, Science, History, "
                "Biography). Drop off donations at AKFCS Elementary. Donation dates: June 1 – June 23."
            ),
            source_url=_DOC_URL, relevance_score=0.928, page_number=7,
        ),
    ],
}


# ── Family Engagement ─────────────────────────────────────────────────────────

FAMILY_ENGAGEMENT_SCORES: list[DistrictScoreItem] = [
    _akfcs(237, 8),
    DistrictScoreItem(district_name="Worcester",     intensity_score=155, conversation_count=5, source_count=1),
    DistrictScoreItem(district_name="West Boylston", intensity_score=91,  conversation_count=3, source_count=1),
    DistrictScoreItem(district_name="Boylston",      intensity_score=57,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Sterling",      intensity_score=33,  conversation_count=1, source_count=1),
]

FAMILY_ENGAGEMENT_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We'd love to see you at Goodies with Grownups this Wednesday at 7:45 AM! Come start "
                "the morning with a sweet treat, some friendly conversation, and time together as a "
                "school community. Families can park in the car drop-off line and walk over to the "
                "playground for a delicious donut treat. While the kids play, grownups can relax, "
                "connect, and visit with other members of our school community."
            ),
            source_url=_DOC_URL, relevance_score=0.941, page_number=4,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We also want to give a very special shout out to Alicia, Nay, Samantha, Shiqui, and "
                "Flor for their amazing work behind the scenes in re energizing the PTO at the ES. Your "
                "efforts to welcome new families, encourage involvement, and invite parents to join the "
                "PTO help strengthen the partnership between our school, families, and students."
            ),
            source_url=_DOC_URL, relevance_score=0.897, page_number=3,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Join the Abby Kelley PTO for the upcoming school year 2026-2027! Together, we empower "
                "every scholar to achieve their greatest potential. We welcome all parents and caregivers! "
                "Get involved in the way that works best for you throughout the year. Questions? Contact: "
                "Lucy Marcil at LMarcil@akfcs.org, Ext 4108."
            ),
            source_url=_DOC_URL, relevance_score=0.852, page_number=8,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "We'd love to see you at Goodies with Grownups this Wednesday at 7:45 AM! Come start "
                "the morning with a sweet treat, some friendly conversation, and time together as a "
                "school community. Families can park in the car drop-off line and walk over to the "
                "playground for a delicious donut treat."
            ),
            source_url=_DOC_URL, relevance_score=0.918, page_number=4,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Join the Abby Kelley PTO for the upcoming school year 2026-2027! Together, we empower "
                "every scholar to achieve their greatest potential. We welcome all parents and caregivers!"
            ),
            source_url=_DOC_URL, relevance_score=0.831, page_number=8,
        ),
    ],
}


# ── School Transition ─────────────────────────────────────────────────────────

SCHOOL_TRANSITION_SCORES: list[DistrictScoreItem] = [
    _akfcs(209, 6),
    DistrictScoreItem(district_name="Worcester", intensity_score=127, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Millbury",  intensity_score=74,  conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Sutton",    intensity_score=44,  conversation_count=2, source_count=1),
]

SCHOOL_TRANSITION_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Our final day of school, Monday, June 22nd, will be a very special day for all students. "
                "We will be hosting our annual 'Meet and Greet: Find Your Seat' event! Students will visit "
                "their next year's classroom, meet their new teacher, and spend approximately an hour "
                "getting to know their new learning space and teacher for the upcoming school year."
            ),
            source_url=_DOC_URL, relevance_score=0.944, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Because this is such an exciting and important transition opportunity for our students, "
                "it is especially important that students attend school on June 22nd. This experience "
                "helps students feel prepared, welcomed, and excited as they look ahead to the next "
                "chapter in their AKF journey."
            ),
            source_url=_DOC_URL, relevance_score=0.908, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "This will be the final weekly Bear Bulletin of the 2025-2026 school year. Families can "
                "look forward to hearing from us again in late July/early August as we begin sharing "
                "important information and updates for the start of the 2026–2027 school year. Our first "
                "week of school will take place the week of August 24th."
            ),
            source_url=_DOC_URL, relevance_score=0.863, page_number=2,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Our final day of school, Monday, June 22nd, will be a very special day for all students. "
                "We will be hosting our annual 'Meet and Greet: Find Your Seat' event! Students will visit "
                "their next year's classroom, meet their new teacher, and spend approximately an hour "
                "getting to know their new learning space and teacher for the upcoming school year."
            ),
            source_url=_DOC_URL, relevance_score=0.921, page_number=1,
        ),
    ],
}


# ── Default fallback ──────────────────────────────────────────────────────────

SAMPLE_DISTRICT_SCORES: list[DistrictScoreItem] = [
    _akfcs(180, 5),
    DistrictScoreItem(district_name="Worcester", intensity_score=112, conversation_count=4, source_count=1),
    DistrictScoreItem(district_name="Shrewsbury", intensity_score=68, conversation_count=2, source_count=1),
    DistrictScoreItem(district_name="Millbury",   intensity_score=41, conversation_count=1, source_count=1),
]

SAMPLE_CITATIONS: dict[str, list[CitationItem]] = {
    _AKFCS: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "The year is winding down, but we still have an action-packed few days ahead! As we head "
                "into our final 4½ days of school, our students will have the opportunity to celebrate "
                "all of their hard work and the many memories created throughout this year."
            ),
            source_url=_DOC_URL, relevance_score=0.761, page_number=1,
        ),
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "Thank you for partnering with us throughout this incredible year. We are so proud of "
                "our students and all they have accomplished, and we look forward to celebrating these "
                "final moments together as an AKF family."
            ),
            source_url=_DOC_URL, relevance_score=0.714, page_number=1,
        ),
    ],
    _WORCESTER: [
        CitationItem(
            document_id=_DOC_ID, document_title=_DOC_TITLE, date=_DOC_DATE,
            snippet=(
                "The year is winding down, but we still have an action-packed few days ahead! As we head "
                "into our final 4½ days of school, our students will have the opportunity to celebrate "
                "all of their hard work and the many memories created throughout this year."
            ),
            source_url=_DOC_URL, relevance_score=0.738, page_number=1,
        ),
    ],
}


# ── Keyword → data map ────────────────────────────────────────────────────────

KEYWORD_DATA: dict[str, tuple[list[DistrictScoreItem], dict[str, list[CitationItem]]]] = {
    "end of year":          (END_OF_YEAR_SCORES,        END_OF_YEAR_CITATIONS),
    "medication policy":    (MEDICATION_POLICY_SCORES,  MEDICATION_POLICY_CITATIONS),
    "book drive":           (BOOK_DRIVE_SCORES,          BOOK_DRIVE_CITATIONS),
    "family engagement":    (FAMILY_ENGAGEMENT_SCORES,  FAMILY_ENGAGEMENT_CITATIONS),
    "school transition":    (SCHOOL_TRANSITION_SCORES,  SCHOOL_TRANSITION_CITATIONS),
    # Free-text variants
    "end of year activities":   (END_OF_YEAR_SCORES,       END_OF_YEAR_CITATIONS),
    "student medication":       (MEDICATION_POLICY_SCORES, MEDICATION_POLICY_CITATIONS),
    "student health":           (MEDICATION_POLICY_SCORES, MEDICATION_POLICY_CITATIONS),
    "classroom libraries":      (BOOK_DRIVE_SCORES,         BOOK_DRIVE_CITATIONS),
    "parent involvement":       (FAMILY_ENGAGEMENT_SCORES, FAMILY_ENGAGEMENT_CITATIONS),
    "pto":                      (FAMILY_ENGAGEMENT_SCORES, FAMILY_ENGAGEMENT_CITATIONS),
    "meet and greet":           (SCHOOL_TRANSITION_SCORES, SCHOOL_TRANSITION_CITATIONS),
    "find your seat":           (SCHOOL_TRANSITION_SCORES, SCHOOL_TRANSITION_CITATIONS),
    "last day of school":       (END_OF_YEAR_SCORES,       END_OF_YEAR_CITATIONS),
    "goodies with grownups":    (FAMILY_ENGAGEMENT_SCORES, FAMILY_ENGAGEMENT_CITATIONS),
}
