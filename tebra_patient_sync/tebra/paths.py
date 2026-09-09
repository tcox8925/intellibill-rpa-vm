"""Shared file paths for the tebra/ package - anchored to this package's
own directory (not the process's current working directory), so
responses/ always lives inside tebra/ no matter where/how a script here
gets invoked from (app.py, -m tebra.load_patient_header, etc).

Kept in its own module (no other imports) so it can be pulled in by
tebra_api.py, load_patient_header.py, and load_patient_coverages.py
without any of them triggering each other's side effects (tebra_api.py
builds a zeep SOAP client at import time).
"""

import os

RESPONSES_DIR = os.path.join(os.path.dirname(__file__), "responses")
RESPONSE_TEXT_PATH = os.path.join(RESPONSES_DIR, "response_patients.txt")
PATIENTS_JSON_PATH = os.path.join(RESPONSES_DIR, "Patients.json")
