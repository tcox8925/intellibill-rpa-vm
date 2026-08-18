"""
WorkSelector — the single description of *what* a run should process.

Every entry point (daily server, backfill CLI, targeted one-off) builds a
WorkSelector and hands it to the pipeline. The pipeline and query builder read
from it; nothing else decides scope. This is what collapses the old
daily/backfill/one-off code paths into one.

Modes
-----
daily     : no date/id/name filters. Notes recheck is unbounded (catch
            late-signed notes) and facesheets/charges are date-agnostic.
            The *appointment scrape* is still today-only (applied in the UI
            by the appointments pass, not here).
backfill  : an explicit [start_date, end_date] window. All passes stay
            inside the window.
target    : a specific appointment, identified by any of appt_id, patient
            name, or a single date (start_date). Skips the appointment
            scrape and operates on existing rows; produces its own ZIP.
"""

from dataclasses import dataclass
from datetime import date
from typing import Optional

from .config import ENTITY, SUB_ENTITY, EHR_NAME


@dataclass
class WorkSelector:
    mode: str  # 'daily' | 'backfill' | 'target'
    practice: Optional[str] = None          # None = all discovered practices
    folder_structure: Optional[str] = None  # Upload folder root from payload
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    appt_id: Optional[str] = None
    patient_name: Optional[str] = None

    # backfill/target default to True (existing behavior: re-pull facesheets
    # regardless of prior process_status inside the window/target -- the
    # caller explicitly asked for this scope, so already-processed rows are
    # redone too). Set False to keep the normal "skip if already Processed"
    # gate even inside an explicit window -- used by /run-tebra-recheck so a
    # wide historical window only pulls rows that are signed AND genuinely
    # not done yet, never re-downloading ones already successfully processed.
    ungated_repull: bool = True

    # Tenant identity — defaults from config, overridable for future tenants.
    entity: str = ENTITY
    sub_entity: str = SUB_ENTITY
    ehr_name: str = EHR_NAME

    def __post_init__(self):
        self.validate()

    def validate(self):
        if self.mode not in ("daily", "backfill", "target"):
            raise ValueError(f"Unknown mode: {self.mode!r}")

        if self.mode == "backfill":
            if not (self.start_date and self.end_date):
                raise ValueError("backfill mode requires start_date and end_date")
            if self.start_date > self.end_date:
                raise ValueError("start_date must be <= end_date")

        if self.mode == "target":
            if not (self.appt_id or self.patient_name or self.start_date):
                raise ValueError(
                    "target mode requires at least one of: appt_id, "
                    "patient_name, start_date"
                )

        if self.mode == "daily":
            if any((self.start_date, self.end_date, self.appt_id, self.patient_name)):
                raise ValueError("daily mode takes no date/id/name filters")

    # ---- Convenience constructors ----

    @classmethod
    def daily(cls, practice=None, folder_structure=None):
        return cls(mode="daily", practice=practice, folder_structure=folder_structure)

    @classmethod
    def backfill(cls, start_date, end_date, practice=None, folder_structure=None,
                 ungated_repull=True):
        return cls(
            mode="backfill",
            start_date=start_date,
            end_date=end_date,
            practice=practice,
            folder_structure=folder_structure,
            ungated_repull=ungated_repull,
        )

    @classmethod
    def target(cls, appt_id=None, patient_name=None, on_date=None, practice=None,
               folder_structure=None):
        return cls(
            mode="target",
            appt_id=appt_id,
            patient_name=patient_name,
            start_date=on_date,
            practice=practice,
            folder_structure=folder_structure,
        )

    @classmethod
    def from_args(cls, start_date=None, end_date=None, appt_id=None,
                  patient_name=None, practice=None, folder_structure=None):
        """
        Infer mode from whatever the caller supplied:
          nothing                          -> daily
          start_date AND end_date          -> backfill
          appt_id / name / single date     -> target
        """
        if start_date and end_date:
            return cls.backfill(
                start_date,
                end_date,
                practice=practice,
                folder_structure=folder_structure,
            )
        if appt_id or patient_name or start_date:
            return cls.target(
                appt_id=appt_id,
                patient_name=patient_name,
                on_date=start_date,
                practice=practice,
                folder_structure=folder_structure,
            )
        return cls.daily(practice=practice, folder_structure=folder_structure)
