"""Queue status reporting and manual row reset."""

from typing import Any, Dict

from pf_sync_pkg.matching import select_queue_rows
from pf_sync_pkg.store import load_store, save_store, store_rows
from pf_sync_pkg.utils import now_iso


def queue_status(queue_json: str, show_limit: int = 20) -> Dict[str, Any]:
    store = load_store(queue_json)
    rows = store_rows(store)
    counts: Dict[str, int] = {}
    for record in rows:
        counts[record.status] = counts.get(record.status, 0) + 1
    print("Queue counts:")
    for status, count in sorted(counts.items()):
        print(f"  {status:18s} {count}")
    attention = [record for record in rows if record.status == "needs_attention"]
    review = [record for record in rows if record.status == "review"]
    if attention:
        print("\nNeeds attention:")
        for record in attention[:show_limit]:
            print(
                f"  appointment_id={record.appointment_id or '<none>'} "
                f"row_id={record.row_id} patient={record.patient_name} DOB={record.patient_dob} "
                f"phone={record.patient_phone} message={record.message or record.patient_match_message}"
            )
    if review:
        print("\nReview/poll again:")
        for record in review[:show_limit]:
            print(
                f"  appointment_id={record.appointment_id or '<none>'} patient={record.patient_name} "
                f"date={record.appointment_date} reason={record.status_reason}"
            )
    return {"counts": counts, "needs_attention": len(attention), "review": len(review)}


def reset_rows(
    queue_json: str,
    row_id: str = "",
    appointment_id: str = "",
    patient_id: str = "",
    all_processed: bool = False,
) -> int:
    store = load_store(queue_json)
    rows = store_rows(store)
    selected = select_queue_rows(
        rows, row_id=row_id, appointment_id=appointment_id, patient_id=patient_id
    )
    if all_processed:
        selected = [record for record in rows if record.status == "processed"]
    if not selected:
        raise ValueError("No rows matched the reset selector.")
    for record in selected:
        if record.status != "ignored":
            record.status = "ready"
            record.status_reason = "manually_reset_for_test"
            record.error_message = ""
            record.message = ""
            record.pdf_path = ""
            record.processed_at = ""
            record.updated_at = now_iso()
    save_store(queue_json, store, rows)
    return len(selected)
