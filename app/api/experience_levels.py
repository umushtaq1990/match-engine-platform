"""Shared experience-bucket mapping (issue #20/#22) -- a 4-bucket selector in
the UI instead of a raw years-of-experience number, used by both
Candidate.years_experience (candidate-details endpoints) and
Job.min_years_experience (job-details endpoints). Nothing in
app/matching/scoring.py reads either column, so the bucket is purely a
display/UX convenience over an int, not a new data concept.
"""
EXPERIENCE_LEVEL_TO_YEARS = {"entry_level": 1, "mid_level": 4, "senior": 8, "expert": 15}
_EXPERIENCE_LEVEL_THRESHOLDS = (
    (2, "entry_level"), (5, "mid_level"), (10, "senior"),
)


def years_experience_to_level(years: int) -> str:
    for max_years, level in _EXPERIENCE_LEVEL_THRESHOLDS:
        if years <= max_years:
            return level
    return "expert"
