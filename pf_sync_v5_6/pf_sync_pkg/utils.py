"""Generic string/date helpers shared across the pf_sync_pkg package."""

import re
from datetime import date, datetime
from typing import Any, Iterable, Optional

from pf_sync_pkg.constants import PRACTICE_TZ


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def practice_today() -> date:
    return datetime.now(PRACTICE_TZ).date()


def clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def normalize_header(value: str) -> str:
    """Normalize a source column header to a lowercase space-separated token.

    v5.4: Practice Fusion's CSV export emits camelCase headers (AppointmentTime,
    MobilePhone, AppointmentType, AppointmentStatus, SeenBy) while the on-screen
    report renders spaced titles. Collapsing only non-alphanumerics left the
    camelCase forms as single tokens ("appointmenttime"), so six of thirteen target
    fields silently failed to map. Splitting on the lower->upper boundary makes both
    the exported and the scraped header forms normalize to the same token.
    """
    text = clean(value)
    # AppointmentTime -> Appointment Time ; DOBValue -> DOB Value
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def normalize_status(value: str) -> str:
    return clean(value).lower().replace("_", " ")


def status_matches(value: str, configured_statuses: Iterable[str]) -> bool:
    normalized = normalize_status(value)
    if not normalized:
        return False
    for configured in configured_statuses:
        token = normalize_status(configured)
        if token and (normalized == token or token in normalized):
            return True
    return False


# ---------------------------------------------------------------------------
# Single-definition appointment status gates
#
# Every code path that decides whether an appointment is skipped or considered
# clinically complete must call these two functions. Duplicating the status lists
# is how a gate fix lands in one path and misses its twin.
# ---------------------------------------------------------------------------


def is_ignored_status(value: str, config: "SyncConfig") -> bool:
    return status_matches(value, config.ignored_statuses)


def is_seen_status(value: str, config: "SyncConfig") -> bool:
    return status_matches(value, config.seen_statuses)


def parse_date(value: str) -> Optional[date]:
    text = clean(value)
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass
    formats = (
        "%m/%d/%Y",
        "%m/%d/%y",
        "%Y-%m-%d",
        "%m-%d-%Y",
        "%b %d, %Y",
        "%B %d, %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    # Appointment exports commonly append a time.
    date_prefixes = [
        text.split(" ")[0],
        " ".join(text.split(" ")[:2]),
        " ".join(text.split(" ")[:3]),
    ]
    for prefix in date_prefixes:
        for fmt in formats:
            try:
                return datetime.strptime(prefix, fmt).date()
            except ValueError:
                pass
    return None


def require_date(value: str, label: str) -> date:
    parsed = parse_date(value)
    if parsed is None:
        raise ValueError(f"Could not parse {label}: {value!r}")
    return parsed


def safe_filename(value: str) -> str:
    value = re.sub(r"[<>:\"/\\|?*]+", "_", value)
    value = re.sub(r"\s+", "_", value).strip("._")
    return value or "practice_fusion_file"
