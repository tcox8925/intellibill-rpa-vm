"""Phone/name normalization and identity scoring used for patient matching."""

import difflib
import re

from pf_sync_pkg.utils import clean


def normalize_phone(value: str) -> str:
    digits = re.sub(r"\D+", "", clean(value))
    if len(digits) > 10 and digits.startswith("1"):
        digits = digits[-10:]
    return digits


def normalize_person_name(value: str) -> str:
    text = clean(value).lower()
    # Convert Last, First into First Last for comparison.
    if "," in text:
        left, right = text.split(",", 1)
        text = f"{right} {left}"
    text = re.sub(r"\b(jr|sr|ii|iii|iv|mr|mrs|ms|dr)\b\.?", " ", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return clean(text)


def name_similarity(left: str, right: str) -> float:
    a = normalize_person_name(left)
    b = normalize_person_name(right)
    if not a or not b:
        return 0.0
    direct = difflib.SequenceMatcher(None, a, b).ratio()
    token = difflib.SequenceMatcher(None, " ".join(sorted(a.split())), " ".join(sorted(b.split()))).ratio()
    return max(direct, token)


def name_token_containment(left: str, right: str) -> float:
    """Fraction of the shorter name's tokens present in the longer name.

    Character-ratio similarity punishes the dominant real-world mismatch at this
    practice: the appointment report drops a middle name or a second surname that the
    chart carries ("Marlene Revilla Gomez" vs "Marlene Del Carmen Revilla Gomez",
    "Elizabeth Vazquez Martinez" vs "Elizabeth Vazquez"). Those score 0.75-0.79 on
    difflib and fall under the 0.82 threshold despite an exact DOB match.

    The subset direction is only trusted when the shorter name carries at least two
    distinct tokens. A single-token name cannot establish identity on its own: the real
    registry contains a malformed row "Peyton Peyton", whose token set is just
    {peyton}, and an unguarded shorter-side ratio scored that 1.0 against "Peyton
    Hicks" -- silently attaching the wrong chart. One shared given name is never
    sufficient evidence, even inside a matching DOB bucket.
    """
    a = set(normalize_person_name(left).split())
    b = set(normalize_person_name(right).split())
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared < 2:
        return 0.0
    smaller = min(len(a), len(b))
    if smaller < 2:
        return 0.0
    return shared / smaller


def identity_score(appointment_name: str, registry_name: str) -> float:
    return max(
        name_similarity(appointment_name, registry_name),
        name_token_containment(appointment_name, registry_name),
    )


def parse_guid_from_url(value: str) -> str:
    match = re.search(r"/patients/([0-9a-fA-F-]{20,})", clean(value))
    return match.group(1) if match else ""
