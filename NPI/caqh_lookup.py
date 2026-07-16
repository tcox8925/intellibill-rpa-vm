import os
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime
from typing import Optional, Any, Dict, List
import uuid
import xmltodict

# =========================
# CONFIG
# =========================
CAQH_API_URL = "https://proview.caqh.org/credentialingapi/api/v9/entities"
CAQH_ROSTER_URL = "https://proview.caqh.org/RosterAPI/API/Roster?product=PV"
ORG_ID = "1726"  # your org id @ CAQH

USERNAME = os.getenv("CAQH_USERNAME", "")
PASSWORD = os.getenv("CAQH_PASSWORD", "")

# =========================
# HELPERS
# =========================
def ensure_list(x):
    if not x:
        return []
    return x if isinstance(x, list) else [x]

def safe_text(d: Dict[str, Any], *keys, default: str = "") -> str:
    cur = d
    for k in keys:
        if isinstance(cur, dict):
            cur = cur.get(k, default)
        else:
            return default
    if isinstance(cur, dict):
        return " ".join(str(v) for v in cur.values() if v)
    return cur or default

def safe_date_str(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    return str(val)[:10]

def safe_trim(val: Optional[str], max_len: int) -> Optional[str]:
    if val is None:
        return None
    s = str(val)
    return s if len(s) <= max_len else s[:max_len]

def new_txn_id():
    return str(uuid.uuid4())

# =========================
# VALIDATION
# =========================
def validate_caqh_id(caqh_id: str,
                     username: str = USERNAME,
                     password: str = PASSWORD,
                     timeout: int = 30) -> Dict[str, Any]:
    try:
        params = {
            "organizationId": ORG_ID,
            "caqhProviderId": str(caqh_id),
            "attestationDate": datetime.today().strftime("%m/%d/%Y"),
        }
        headers = {"Content-Type": "application/xml", "Accept": "application/xml"}

        r = requests.get(
            CAQH_API_URL,
            headers=headers,
            params=params,
            auth=HTTPBasicAuth(username, password),
            timeout=timeout
        )

        xml_text = (r.text or "").lower()

        #294 — provider exists but not current/complete
        if r.status_code == 294:
            return {
                "valid": True,
                "blocking": True,
                "status": "Provider is not current and complete"
            }

        if r.status_code == 292:
            if "provider is not active on the roster" in xml_text:
                return {
                    "valid": True,
                    "blocking": False,
                    "status": "Provider not active on roster"
                }
            return {"valid": False, "blocking": True, "status": "CAQH validation failed"}

        #200 — good profile
        if r.status_code == 200:
            return {"valid": True, "blocking": False, "status": "OK"}

        #true invalids
        if r.status_code in (400, 401, 403, 404):
            return {"valid": False, "blocking": True, "status": "Invalid CAQH ID"}

        return {"valid": False, "blocking": True, "status": "Unknown CAQH response"}

    except Exception as e:
        return {"valid": False, "blocking": True, "status": str(e)}


# =========================
# NETWORK
# =========================
def fetch_caqh_data(npi: str, caqh_provider_id: str,
                    username: str, password: str,
                    attestation_date: Optional[str] = None,
                    timeout: int = 60) -> str:
    if not attestation_date:
        attestation_date = datetime.today().strftime("%m/%d/%Y")
    params = {
        "organizationId": ORG_ID,
        "caqhProviderId": str(caqh_provider_id),
        "attestationDate": attestation_date
    }
    headers = {"Content-Type": "application/xml", "Accept": "application/xml"}
    r = requests.get(CAQH_API_URL, headers=headers, params=params,
                     auth=HTTPBasicAuth(username, password), timeout=timeout)
    r.raise_for_status()
    return r.text

def parse_caqh_xml(xml_text: str) -> dict:
    return xmltodict.parse(xml_text).get("Provider", {})

# =========================
# EXTRACTORS (schema-aligned with safe trims/dates)
# =========================
def extract_provider_info(p: dict) -> List[dict]:
    race_nodes = ensure_list(p.get("Race_Ethnicity"))
    race_ethnicity_level_1 = race_nodes[0].get("Race_Ethnicity_Level_1") if race_nodes else None
    race_ethnicity_level_2 = race_nodes[1].get("Race_Ethnicity_Level_1") if len(race_nodes) > 1 else None
    graduate_type = safe_text(p, "GraduateType", "GraduateTypeDescription") or None
    provider_type = safe_text(p, "ProviderType", "ProviderTypeAbbreviation") or p.get("ProviderType")
    birth_country = safe_text(p, "BirthCountry", "CountryName") or None
    other_interests = ", ".join([oi.get("OtherInterestDescription") for oi in ensure_list(p.get("OtherInterest")) if oi.get("OtherInterestDescription")]) or None
    return [{
        "txn_id": new_txn_id(),
        "npi": safe_trim(p.get("NPI"), 20) or "UNKNOWN",
        "first_name": safe_trim(p.get("FirstName"), 200),
        "last_name": safe_trim(p.get("LastName"), 200),
        "gender": safe_trim(safe_text(p, "Gender", "GenderDescription"), 50),
        "birth_date": safe_date_str(p.get("BirthDate")),
        "birth_city": safe_trim(p.get("BirthCity"), 200),
        "birth_state": safe_trim(p.get("BirthState"), 50),
        "birth_county": safe_trim(p.get("BirthCounty"), 200),
        "birth_country": safe_trim(birth_country, 200),
        "citizenship_status": safe_trim(p.get("CitizenshipStatus"), 100),
        "email": safe_trim(p.get("EmailAddress"), 200),
        "cell_phone": safe_trim(p.get("CellPhoneNumber"), 50),
        "race_ethnicity_level_1": safe_trim(race_ethnicity_level_1, 100),
        "race_ethnicity_level_2": safe_trim(race_ethnicity_level_2, 100),
        "graduate_type": safe_trim(graduate_type, 200),
        "provider_type": safe_trim(provider_type, 200),
        "other_interests": safe_trim(other_interests, 4000),
        "dea_flag": p.get("DEAFlag"),
        "cds_flag": p.get("CDSFlag"),
        "upin_flag": p.get("UPINFlag"),
        "npi_flag": p.get("NPIFlag"),
        "medicare_flag": p.get("MedicareProviderFlag"),
        "medicaid_flag": p.get("MedicaidProviderFlag"),
        "fellowship_flag": p.get("FellowshipTrainingFlag"),
        "secondary_specialty_flag": p.get("SecondarySpecialtyFlag"),
        "hospital_privilege_flag": p.get("HospitalPrivilegeFlag"),
        "military_service_flag": p.get("ActiveMilitaryFlag"),
        "work_history_gap_flag": p.get("WorkHistoryGapFlag"),
        "hospital_based_flag": p.get("HospitalBasedFlag"),
        "affiliated_flag": p.get("AffiliatedFlag"),
        "delegated_flag": p.get("DelegatedFlag"),
    }]

def extract_specialties(p: dict) -> List[dict]:
    rows = []
    for s in ensure_list(p.get("Specialty")):
        rows.append({
            "txn_id": new_txn_id(),
            "specialty_name": safe_trim(safe_text(s, "Specialty", "SpecialtyName") or s.get("SpecialtyName"), 200),
            "taxonomy_code": safe_trim(s.get("NUCCTaxonomyCode") or s.get("TaxonomyCode"), 50),
            "board_name": safe_trim(s.get("BoardName"), 200),
            "certification_date": safe_date_str(s.get("CertificationDate")),
            "expiration_date": safe_date_str(s.get("ExpirationDate")),
            "board_certified_flag": s.get("BoardCertifiedFlag"),
        })
    return rows

def extract_education(p: dict) -> List[dict]:
    rows = []
    for e in ensure_list(p.get("Education")):
        rows.append({
            "txn_id": new_txn_id(),
            "program_name": safe_trim(e.get("InstitutionName"), 200),
            "type": safe_trim(e.get("EducationTypeName"), 200),
            "specialty": safe_trim(safe_text(e, "Specialty", "SpecialtyName"), 200),
            "grad_year": safe_date_str(e.get("CompletionDate")),  # VARCHAR(10)
            "start_date": safe_date_str(e.get("StartDate")),
            "end_date": safe_date_str(e.get("EndDate")),
            "location_city": safe_trim(e.get("City"), 200),
            "location_state": safe_trim(e.get("State"), 50),
            "country": safe_trim(safe_text(e, "Country", "CountryName"), 200),
            "degree_abbreviation": safe_trim(safe_text(e, "Degree", "DegreeAbbreviation"), 50),
        })
    return rows

def extract_identifiers(p: dict) -> List[dict]:
    rows = []
    for lic in ensure_list(p.get("ProviderLicense")):
        rows.append({
            "txn_id": new_txn_id(),
            "id_type": "LICENSE",
            "id_value": safe_trim(lic.get("LicenseNumber"), 100),
            "state": safe_trim(lic.get("State"), 50),
            "issue_date": safe_date_str(lic.get("IssueDate")),
            "expiration_date": safe_date_str(lic.get("ExpirationDate")),
        })
    for d in ensure_list(p.get("ProviderDEA")):
        rows.append({
            "txn_id": new_txn_id(),
            "id_type": "DEA",
            "id_value": safe_trim(d.get("DEANumber"), 100),
            "state": safe_trim(d.get("State"), 50),
            "issue_date": safe_date_str(d.get("IssueDate")),
            "expiration_date": safe_date_str(d.get("ExpirationDate")),
        })
    for m in ensure_list(p.get("ProviderMedicare")):
        rows.append({
            "txn_id": new_txn_id(),
            "id_type": "MEDICARE",
            "id_value": safe_trim(m.get("MedicareNumber"), 100),
            "state": safe_trim(m.get("State"), 50)
        })
    for m in ensure_list(p.get("ProviderMedicaid")):
        rows.append({
            "txn_id": new_txn_id(),
            "id_type": "MEDICAID",
            "id_value": safe_trim(m.get("MedicaidNumber"), 100),
            "state": safe_trim(m.get("State"), 50)
        })
    if p.get("ECFMGNumber"):
        rows.append({
            "txn_id": new_txn_id(),
            "id_type": "ECFMG",
            "id_value": safe_trim(p.get("ECFMGNumber"), 100),
            "state": None,
            "issue_date": safe_date_str(p.get("ECFMGIssueDate")),
            "expiration_date": None
        })
    return rows

def extract_provider_associates(p: dict) -> List[dict]:
    rows = []
    for a in ensure_list(p.get("Associate")):
        rows.append({
            "txn_id": new_txn_id(),
            "first_name": safe_trim(a.get("FirstName") or a.get("AssociateFirstName"), 200),
            "last_name": safe_trim(a.get("LastName") or a.get("AssociateLastName"), 200),
            "relationship": safe_trim(a.get("Relationship") or safe_text(a, "AssociateType", "AssociateTypeDescription"), 200),
            "npi": safe_trim(a.get("NPI") or "UNKNOWN", 20),
            "email": safe_trim(a.get("EmailAddress"), 200),
            "phone": safe_trim(a.get("PhoneNumber"), 50),
        })
    return rows

def extract_work_history(p: dict) -> List[dict]:
    rows = []
    for wh in ensure_list(p.get("WorkHistory")) + ensure_list(p.get("Employment")) + ensure_list(p.get("ProfessionalExperience")):
        rows.append({
            "txn_id": new_txn_id(),
            "employer_name": safe_trim(wh.get("EmployerName") or wh.get("OrganizationName"), 200),
            "position_title": safe_trim(wh.get("PositionTitle") or wh.get("Title"), 200),
            "start_date": safe_date_str(wh.get("StartDate")),
            "end_date": safe_date_str(wh.get("EndDate")),
        })
    return rows

def extract_references(p: dict) -> List[dict]:
    rows = []
    for ref in ensure_list(p.get("Reference")):
        rows.append({
            "txn_id": new_txn_id(),
            "first_name": safe_trim(ref.get("FirstName") or ref.get("ReferenceFirstName"), 200),
            "last_name": safe_trim(ref.get("LastName") or ref.get("ReferenceLastName"), 200),
            "relationship": safe_trim(ref.get("Relationship") or safe_text(ref, "ReferenceType", "ReferenceTypeDescription"), 200),
            "email": safe_trim(ref.get("EmailAddress"), 200),
            "phone": safe_trim(ref.get("PhoneNumber"), 50),
        })
    return rows

def extract_disclosures(p: dict) -> List[dict]:
    rows = []
    for d in ensure_list(p.get("Disclosure")):
        qsum = safe_trim(safe_text(d, "DisclosureQuestion", "DisclosureSummary"), 500)
        rows.append({
            "txn_id": new_txn_id(),
            "disclosure_id": safe_trim(d.get("@ID"), 50),
            "question_summary": qsum,
            "answer_flag": d.get("DisclosureAnswerFlag"),
            "explanation": safe_trim(d.get("DisclosureExplanation"), 4000),
        })
    return rows

def extract_malpractice_from_disclosures(p: dict) -> List[dict]:
    rows = []
    for d in ensure_list(p.get("Disclosure")):
        disclosure_id = safe_trim(d.get("@ID"), 50)
        qsum = safe_trim(safe_text(d, "DisclosureQuestion", "DisclosureSummary"), 500)
        for m in ensure_list(d.get("Malpractice")):
            rows.append({
                "txn_id": new_txn_id(),
                "disclosure_id": disclosure_id,
                "question_summary": qsum,
                "carrier_name": safe_trim(m.get("InsuranceCarrierName"), 200),
                "policy_number": safe_trim(m.get("PolicyNumber"), 100),
                "occurrence_date": safe_date_str(m.get("OccurrenceDate")),
                "claim_date": safe_date_str(m.get("ClaimDate")),
                "allegation": safe_trim(m.get("AllegationDescription"), 4000),
                "primary_defendant_flag": m.get("PrimaryDefendantFlag"),
                "num_other_codefendant": m.get("NumberOtherCodefendant"),
                "case_involvement": safe_trim(m.get("CaseInvolvement"), 500),
                "patient_injury_description": safe_trim(m.get("PatientInjuryDescription"), 2000),
                "npdb_case_flag": m.get("NPDBCaseFlag"),
                "patient_died_flag": m.get("PatientDiedFlag"),
                "claim_status": safe_trim(safe_text(m, "ClaimStatus", "ClaimStatus"), 100),
                "address1": safe_trim(m.get("Address"), 200),
                "address2": safe_trim(m.get("Address2"), 200),
                "city": safe_trim(m.get("City"), 100),
                "state": safe_trim(m.get("State"), 50),
                "zip": safe_trim(m.get("Zip"), 20),
                "phone": safe_trim(m.get("PhoneNumber"), 50),
            })
    return rows

def extract_insurance(p: dict) -> List[dict]:
    rows = []
    for i in ensure_list(p.get("Insurance")):
        rows.append({
            "txn_id": new_txn_id(),
            "carrier_name": safe_trim(i.get("InsuranceCarrierName"), 200),
            "policy_number": safe_trim(i.get("PolicyNumber"), 100),
            "insurance_type": safe_trim(i.get("InsuranceType"), 100),
            "start_date": safe_date_str(i.get("StartDate")),
            "end_date": safe_date_str(i.get("EndDate")),
            "occurrence": safe_trim(i.get("CoverageAmountOccurrence"), 100),
            "aggregate": safe_trim(i.get("CoverageAmountAggregate"), 100),
            "self_insured": i.get("SelfInsuredFlag"),
        })
    return rows

def extract_certifications(p: dict) -> List[dict]:
    rows = []
    for c in ensure_list(p.get("Certification")):
        rows.append({
            "txn_id": new_txn_id(),
            "certification_name": safe_trim(c.get("CertificationDescription"), 200),
            "provider_certified_flag": c.get("ProviderCertifiedFlag"),
            "staff_certified_flag": c.get("StaffCertifiedFlag") or c.get("CertificationFlag"),
            "expiration_date": safe_date_str(c.get("ExpirationDate"))
        })
    return rows

def extract_affiliations(p: dict) -> List[dict]:
    rows = []
    for h in ensure_list(p.get("Hospital")):
        rows.append({
            "txn_id": new_txn_id(),
            "hospital_name": safe_trim(h.get("HospitalName"), 200),
            "aha_id": safe_trim(h.get("AHAHospitalID"), 50),
            "privileges": safe_trim(h.get("PrivilegeDescription"), 200),
            "staff_category": safe_trim(h.get("StaffCategory"), 100),
            "unrestricted_flag": h.get("UnrestrictedPrivilegesFlag"),
            "start_date": safe_date_str(h.get("StartDate")),
            "end_date": safe_date_str(h.get("EndDate"))
        })
    return rows

# ---------- Practices and children ----------
def _primary_flag_from_addresses(practice: dict) -> int:
    for addr in ensure_list(practice.get("PracticeAddress")):
        desc = safe_text(addr, "AddressType", "AddressTypeDescription")
        if desc and "primary practice" in desc.lower():
            return 1
    desc2 = safe_text(practice, "AddressType", "AddressTypeDescription")
    if desc2 and "primary practice" in desc2.lower():
        return 1
    return 0

def extract_practices(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        rows.append({
            "txn_id": new_txn_id(),
            "practice_uid": safe_trim(practice_uid, 100),
            "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
            "practice_name": safe_trim(pr.get("PracticeName"), 200),
            "address": safe_trim(pr.get("Address"), 200),
            "city": safe_trim(pr.get("City"), 100),
            "state": safe_trim(pr.get("State"), 50),
            "zip": safe_trim(pr.get("Zip"), 20),
            "phone": safe_trim(pr.get("PhoneNumber") or pr.get("PatientAppointmentPhoneNumber"), 50),
            "fax": safe_trim(pr.get("FaxNumber"), 50),
            "after_hours_phone": safe_trim(pr.get("AfterHoursPhoneNumber"), 50),
            "currently_practicing_flag": pr.get("CurrentlyPracticingFlag"),
            "ada_flag": pr.get("ADAApprovedFlag"),
            "interpreter_flag": pr.get("InterpreterAvailableFlag"),
            "practice_type": safe_trim(pr.get("PracticeTypeDescription"), 100),
            "start_date": safe_date_str(pr.get("StartDate")),
            "end_date": safe_date_str(pr.get("EndDate")),
            "list_in_directory_flag": pr.get("ListInDirectoryFlag"),
            "electronic_billing_flag": pr.get("ElectronicBillingFlag"),
            "primary_flag": _primary_flag_from_addresses(pr),
        })
    return rows

def extract_practice_hours(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for h in ensure_list(pr.get("ProviderPracticeHours")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "day": safe_trim(safe_text(h, "DayOfWeek", "DayOfWeekName"), 50),
                "start_time": safe_trim(safe_text(h, "StartHours", "Hours"), 20),
                "end_time": safe_trim(safe_text(h, "EndHours", "Hours"), 20),
                "hours_type": safe_trim(safe_text(h, "HoursType", "HoursTypeDescription"), 50),
            })
    return rows

def extract_practice_languages(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for lang in ensure_list(pr.get("Language")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "language": safe_trim(safe_text(lang, "Language", "LanguageName"), 100),
                "type": safe_trim(lang.get("LanguageType"), 100),
                "employee_type": safe_trim(safe_text(lang, "EmployeeType", "EmployeeTypeDescription"), 100),
            })
    return rows

def extract_practice_services(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for s in ensure_list(pr.get("Service")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "service_name": safe_trim(safe_text(s, "Service", "ServiceName"), 200),
                "provided_flag": s.get("ServiceProvidedFlag"),
                "lab_cert_program": safe_trim(s.get("LaboratoryCertificationProgram"), 100),
            })
    return rows

def extract_practice_patient_acceptance(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for pt in ensure_list(pr.get("Patient")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "patient_type": safe_trim(safe_text(pt, "PatientType", "PatientTypeDescription"), 100),
                "accepts_flag": pt.get("PatientFlag")
            })
    return rows

def extract_practice_accessibility(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for acc in ensure_list(pr.get("Accessibility")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "accessibility": safe_trim(safe_text(acc, "Accessibility", "AccessibilityDescription"), 200),
                "accessibility_flag": acc.get("AccessibilityFlag"),
                "other_accessibility_description": safe_trim(acc.get("OtherAccessibilityDescription"), 200)
            })
    return rows

def extract_practice_limitations(p: dict, npi: str) -> List[dict]:
    rows = []

    def int_from_caqh(v):
        """
        Convert CAQH value to integer safely:
        - True → 1
        - False → 0
        - "7" → 7
        - " 12 " → 12
        - None / invalid → None
        """
        if v is True:
            return 1
        if v is False:
            return 0
        try:
            # convert "17", "01", " 3 " to integer
            return int(str(v).strip())
        except:
            return None

    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"

        for lim in ensure_list(pr.get("Limitation")):

            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),

                # ALWAYS convert age_flag, age_min, age_max to int safely
                "age_flag": int_from_caqh(lim.get("AgeLimitationFlag")),
                "age_min": int_from_caqh(lim.get("AgeLimitationMinimum")),
                "age_max": int_from_caqh(lim.get("AgeLimitationMaximum")),

                "gender_limitation": safe_trim(
                    safe_text(lim, "GenderLimitation", "GenderLimitationDescription"), 50
                )
            })

    return rows



def extract_practice_associates(p: dict, npi: str) -> List[dict]:
    rows = []
    for i, pr in enumerate(ensure_list(p.get("Practice")), start=1):
        practice_uid = f"{(npi or 'UNKNOWN')}-{i}"
        for a in ensure_list(pr.get("Associate")):
            rows.append({
                "txn_id": new_txn_id(),
                "practice_uid": safe_trim(practice_uid, 100),
                "practice_id": safe_trim(pr.get("PracticeLocationId"), 100),
                "first_name": safe_trim(a.get("AssociateFirstName") or a.get("FirstName"), 200),
                "last_name": safe_trim(a.get("AssociateLastName") or a.get("LastName"), 200),
                "middle_initial": safe_trim(a.get("AssociateMiddleInitial"), 20),
                "relationship": safe_trim(safe_text(a, "AssociateType", "AssociateTypeDescription") or a.get("Relationship"), 200),
                "email": safe_trim(a.get("EmailAddress"), 200),
                "phone": safe_trim(a.get("PhoneNumber"), 50),
                "fax": safe_trim(a.get("FaxNumber"), 50),
                "license_number": safe_trim(a.get("LicenseNumber"), 100),
                "license_state": safe_trim(a.get("LicenseState"), 50),
            })
    return rows

# =========================
# MAIN ENTRY
# =========================
def run_caqh_lookup(npi: str,
                    caqh_id: str,
                    txn_id_provider: str,
                    username: str = USERNAME,
                    password: str = PASSWORD) -> Dict[str, List[dict]]:
    """
    Run CAQH lookup. Validates CAQH ID and ensures NPI from CAQH matches input NPI.
    """
    # --- Step 1: Validate CAQH ID ---
    if not validate_caqh_id(caqh_id, username, password):
        print(f"[ERROR] Invalid or unrecognized CAQH ID: {caqh_id}")
        return {"status": "Invalid CAQH ID", "success": False, "caqh_id": caqh_id}

    # --- Step 2: Fetch profile ---
    xml_text = fetch_caqh_data(npi, caqh_id, username, password)

    # --- Step 3: Add to roster if needed ---
    if "provider is not active on the roster" in xml_text:
        roster_body = [{"Organization_ID": int(ORG_ID), "CAQH_Provider_ID": str(caqh_id)}]
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        r = requests.post(CAQH_ROSTER_URL, headers=headers, json=roster_body,
                          auth=HTTPBasicAuth(username, password), timeout=60)
        r.raise_for_status()
        xml_text = fetch_caqh_data(npi, caqh_id, username, password)

    # --- Step 4: Parse and verify NPI match ---
    p = parse_caqh_xml(xml_text)
    caqh_npi = str(p.get("NPI") or "").strip()

    if not caqh_npi:
        print(f"[ERROR] CAQH {caqh_id} returned empty NPI field.")
        return {
            "status": "CAQH record missing NPI",
            "success": False,
            "caqh_id": caqh_id
        }

    if caqh_npi != str(npi).strip():
        print(f"[ERROR] NPI mismatch: input {npi} ≠ CAQH profile {caqh_npi}")
        return {
            "status": "CAQH NPI mismatch",
            "success": False,
            "caqh_id": caqh_id,
            "expected_npi": npi,
            "caqh_npi": caqh_npi
        }

    # --- Step 5: Build tables (same as before) ---
    npi_safe = (npi or "UNKNOWN")[:20]
    tables = {
        "pch_caqh_provider_info": [
            {"txn_id_provider": txn_id_provider, **row}
            for row in extract_provider_info(p)
        ],
        "pch_caqh_specialties": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_specialties(p)
        ],
        "pch_caqh_education": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_education(p)
        ],
        "pch_caqh_identifiers": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_identifiers(p)
        ],
        "pch_caqh_provider_associates": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_provider_associates(p)
        ],
        "pch_caqh_work_history": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_work_history(p)
        ],
        "pch_caqh_references": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_references(p)
        ],
        "pch_caqh_disclosures": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_disclosures(p)
        ],
        "pch_caqh_malpractice_claims": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_malpractice_from_disclosures(p)
        ],
        "pch_caqh_insurance": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_insurance(p)
        ],
        "pch_caqh_certifications": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_certifications(p)
        ],
        "pch_caqh_hospitals": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_affiliations(p)
        ],
        "pch_caqh_practice": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practices(p, npi_safe)
        ],
        "pch_caqh_practice_hours": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_hours(p, npi_safe)
        ],
        "pch_caqh_practice_languages": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_languages(p, npi_safe)
        ],
        "pch_caqh_practice_services": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_services(p, npi_safe)
        ],
        "pch_caqh_practice_patient_acceptance": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_patient_acceptance(p, npi_safe)
        ],
        "pch_caqh_practice_accessibility": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_accessibility(p, npi_safe)
        ],
        "pch_caqh_practice_limitations": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_limitations(p, npi_safe)
        ],
        "pch_caqh_practice_associates": [
            {"txn_id_provider": txn_id_provider, "npi": npi_safe, **row}
            for row in extract_practice_associates(p, npi_safe)
        ],
    }

    tables["status"] = "Success"
    tables["success"] = True
    tables["caqh_id"] = caqh_id
    tables["caqh_npi"] = caqh_npi
    return tables