import ast
import json
import os

from dotenv import load_dotenv
from zeep import Client

from tebra.paths import PATIENTS_JSON_PATH, RESPONSE_TEXT_PATH, RESPONSES_DIR

load_dotenv()

TEBRA_WSDL_URL = os.getenv("TEBRA_WSDL_URL")
TEBRA_CUSTOMER_KEY = os.getenv("TEBRA_CUSTOMER_KEY")
TEBRA_USERNAME = os.getenv("TEBRA_USERNAME")
TEBRA_PASSWORD = os.getenv("TEBRA_PASSWORD")

client = Client(wsdl=TEBRA_WSDL_URL)


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
        print("Error calling GetPatients:", repr(e), type(e))
        return {}
