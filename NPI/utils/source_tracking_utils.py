import datetime
from datetime import timezone
import json
import os


def record_sources_used(txn_id: str, sources_used: dict, txn_id_provider: str, output_path: str = "logs/sources_used.jsonl"):
    """
    Automatically records which sources were actually used based on boolean flags.

    Args:
        txn_id (str): Unique transaction ID.
        sources_used (dict): Dict like {'npi_registry': True, 'cms': False, 'gpt': True}
        txn_id_provider (str): Provider transaction ID.
        output_path (str): Path to log file.

    Output:
        Appends a JSON line to the output file.
    """
    used_sources = [k for k, v in sources_used.items() if v]

    if not used_sources:
        return  # No logging needed if nothing was used

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    record = {
        "txn_id": txn_id,
        "sources": ",".join(sorted(used_sources)),
        "txn_id_provider": txn_id_provider,
        "updated_on": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    }

    with open(output_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")