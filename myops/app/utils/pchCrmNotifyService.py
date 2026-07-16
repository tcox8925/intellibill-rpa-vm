import httpx
from typing import List, Dict, Tuple
from fastapi import HTTPException

EXTERNAL_URL = "http://57.154.234.15:8000/trigger"

# Single provider notify
async def notify_external_service(npi: str, txn_id: str, company_id: str, module: str = "ALL", caqh_id: str = ""):
    payload = {
        "npi": npi,
        "txn_id": txn_id,
        "module": module,
        "company_id": company_id,
        "carrier_id": "",
        "file_path": "",
        "caqh_id": caqh_id
    }

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(EXTERNAL_URL, json=payload)  # Use json instead of data
            if response.status_code != 200:
                raise HTTPException(status_code=500, detail=f" Failed to notify external service for NPI={npi}, TxnId={txn_id}, Error={ex}")

            response.raise_for_status()
    except Exception as ex:
        # In real app, replace with logging instead of print
        print(f"Failed to notify external service for NPI={npi}, TxnId={txn_id}, Error={ex}")


# Bulk notify
async def notify_external_service_batch(
    parent_txn_id: str,
    inserted_providers: List[Tuple[str, str, str]]  # (txn_id, npi, company_id)
):
    if not inserted_providers:
        return

    payload = [
        {
            "txn_id": txn_id,
            "npi": npi,
            "module": "ALL",
            "company_id": company_id,
            "carrier_id": "",
            "file_path": ""
        }
        for txn_id, npi, company_id in inserted_providers
    ]

    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(EXTERNAL_URL, json=payload)
            response.raise_for_status()
    except Exception as ex:
        print(f"Failed to notify external service for parentTxnId={parent_txn_id}, Error={ex}")

async def notify_external_refresh_service(npi: str, txn_id: str, company_id: str, module: str):
    try:
        payload = {
            "npi": npi,
            "txn_id": txn_id,
            "module": module,
            "company_id": company_id,
            "carrier_id": "",
            "file_path": "",
        }
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(EXTERNAL_URL, json=payload)
            response.raise_for_status()
    except Exception as ex:
        # Replace with logger if you use one
        print(f"Failed to notify external service for NPI {npi}, TxnId {txn_id}: {ex}")

async def notify_external_service_async(npi: str, txn_id: str, company_id: str, module: str = "ALL", caqh_id: str = ""):
    payload = {
        "npi": npi,
        "txn_id": txn_id,
        "module": module,
        "company_id": company_id,
        "carrier_id": "",
        "file_path": "",
        "caqh_id": caqh_id
    }
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(EXTERNAL_URL, json=payload)
            print(f"Response Status: {response.status_code}, Body: {response.text}")
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=500, 
                    detail=f"Failed to notify external service for NPI={npi}, TxnId={txn_id}. Status: {response.status_code}, Response: {response.text}"
                )
            
            response.raise_for_status()
            return response.json()  # Return the response data
            
    except HTTPException:
        raise  # Re-raise HTTPException
    except Exception as ex:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to notify external service for NPI={npi}, TxnId={txn_id}, Error={ex}"
        )