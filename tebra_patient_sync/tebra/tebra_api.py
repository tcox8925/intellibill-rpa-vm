import ast
import json
import os

from dotenv import load_dotenv
from requests import Session
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from zeep import Client
from zeep.transports import Transport

from tebra.paths import PATIENTS_JSON_PATH, RESPONSE_TEXT_PATH, RESPONSES_DIR

load_dotenv()

TEBRA_WSDL_URL = os.getenv("TEBRA_WSDL_URL")
TEBRA_CUSTOMER_KEY = os.getenv("TEBRA_CUSTOMER_KEY")
TEBRA_USERNAME = os.getenv("TEBRA_USERNAME")
TEBRA_PASSWORD = os.getenv("TEBRA_PASSWORD")

# Confirmed live 2026-09-17/18: the plain `Client(wsdl=...)` below used to
# have NO timeout on the actual GetPatients SOAP call (zeep's default
# operation_timeout is None), so an occasional slow/black-holed connection to
# Tebra's SOAP endpoint would just hang indefinitely -- no error, no retry --
# until whatever sat between us and Tebra (their server, a load balancer, an
# idle NAT session) eventually killed the connection on its own, however long
# that took (minutes to tens of minutes observed live). That produced the
# exact same ConnectionError/RemoteDisconnected every time, just at an
# unpredictable delay, and also meant /tebra/sync's _sync_lock (app_tebra.py)
# stayed held for that whole hang, making the *next* trigger attempt fail
# with 409 "already_running" even though nothing was actually deadlocked.
# An explicit operation_timeout + a few automatic retries on transient
# connection resets turns that into a fast, predictable failure instead.
_session = Session()
_retries = Retry(total=3, backoff_factor=1, status_forcelist=[502, 503, 504])
_session.mount("https://", HTTPAdapter(max_retries=_retries))
_session.mount("http://", HTTPAdapter(max_retries=_retries))

_transport = Transport(session=_session, timeout=30, operation_timeout=120)
client = Client(wsdl=TEBRA_WSDL_URL, transport=_transport)


def pull_patient_demographics(practice_name: str = "") -> dict:
    get_patients_req = {
        "RequestHeader": {
            "CustomerKey": TEBRA_CUSTOMER_KEY,
            "User": TEBRA_USERNAME,
            "Password": TEBRA_PASSWORD,
        },
        "Filter": {
            "PracticeName": practice_name,
        },
    }

    os.makedirs(RESPONSES_DIR, exist_ok=True)

    try:
        response = client.service.GetPatients(request=get_patients_req)
        with open(RESPONSE_TEXT_PATH, "w") as f:
            f.write(str(response))
        # Extract Patients and write to JSON
        with open(RESPONSE_TEXT_PATH, "r") as f:
            data = ast.literal_eval(f.read())
        patients = data.get("Patients", {})
        with open(PATIENTS_JSON_PATH, "w") as f:
            json.dump(patients, f, indent=2)
        return patients
    except Exception as e:
        # Confirmed live 2026-09-17: this used to swallow the error and
        # return {} -- app_tebra.py's _run_sync() would then see a normal
        # (empty) return, not an exception, and go on to run
        # run_load_patient_header/run_load_patient_coverages against
        # whatever PATIENTS_JSON_PATH already held from a PREVIOUS
        # successful pull, then report the whole sync as success even
        # though this SOAP call genuinely failed. Re-raising lets
        # /tebra/sync's own try/except (see app_tebra.py) correctly mark
        # the execution failed instead of silently syncing stale data.
        print("Error calling GetPatients:", repr(e), type(e))
        raise
