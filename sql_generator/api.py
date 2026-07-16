# api.py

from fastapi import FastAPI
from pydantic import BaseModel
from typing import Optional, Dict
import os
from fastapi.responses import FileResponse

from clarification_orchestrator import ClarificationOrchestrator
from sql_planner import build_query
from db_executor import execute_query_by_module

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Metis API")

ASSISTANT_NAME = "Metis"


@app.get("/chat")
def serve_chat():
    return FileResponse(os.path.join(BASE_DIR, "index.html"))


@app.get("/")
def root():
    return {
        "assistant": ASSISTANT_NAME,
        "message": "Hi! I am Metis, your data intelligence assistant. I'm here to help with your data queries."
    }


from typing import Union, List

class QueryRequest(BaseModel):
    question: str
    resolved_filters: Optional[Dict[str, Union[str, List[str]]]] = None


@app.post("/query")
def run_query(request: QueryRequest):

    # Orchestrator no longer needs a manual connection here
    orchestrator = ClarificationOrchestrator()

    result = orchestrator.process(
        question=request.question,
        company_id=1,
        resolved_filters=request.resolved_filters
    )

    if result["status"] == "clarification_required":
        return result

    intent = result["intent"]
    sql, params = build_query(intent)

    print("\n================ SQL DEBUG ================")
    print("Module:", intent.module)
    print("Generated SQL:")
    print(sql)
    print("\nParams:")
    print(params)
    print("===========================================\n")

    query_result = execute_query_by_module(
        module=intent.module,
        sql=sql,
        params=params
    )
    print("RAW DB RESULT:", query_result)

    # KPI: single row with single key "value"
    if (
        isinstance(query_result, list)
        and len(query_result) == 1
        and isinstance(query_result[0], dict)
        and "value" in query_result[0]
        and len(query_result[0]) == 1
    ):
        return {
            "status": "success",
            "module": intent.module,
            "metric": intent.metric,
            "result_type": "kpi",
            "result": {"value": query_result[0]["value"]}
        }

    return {
        "status": "success",
        "module": intent.module,
        "metric": intent.metric,
        "result_type": "table",
        "result": query_result
    }