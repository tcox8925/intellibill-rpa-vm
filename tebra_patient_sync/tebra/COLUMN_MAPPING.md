# Patients.json → database column mapping

Source: `responses/Patients.json`, shape `{"PatientData": [...]}` (Tebra's
`GetPatients` SOAP response, one dict per patient). Consumed by
[load_patient_header.py](load_patient_header.py) and
[load_patient_coverages.py](load_patient_coverages.py).

## patient_header (load_patient_header.py)

| Patients.json field | patient_header column | Notes |
|---|---|---|
| — | `source` | hardcoded `"tebra"` |
| `ID` | `source_id` | match key, together with `source` |
| — | `pat_id` | hardcoded `""` (matches `pf_patient_load.py` convention - never used for matching) |
| `PracticeName` | `client_id`, `group_id` | resolved via `"EDI_Tebra"."group"`, matched on normalized name (lowercased, whitespace stripped) |
| — | `practice_id` | always `None` (no Practice model/lookup) |
| `LastName` | `sub_lnam` | |
| `FirstName` | `pat_fnam` | |
| `MiddleName` | `middle_name` | |
| `Prefix` | `prefix` | |
| `Suffix` | `suffix` | |
| `Gender` | `pat_gender` | |
| `DOB` | `pat_dob` | |
| `Active` | `active` | `"True"` string → `bool` |
| `PatientFullName` | `patient_full_name` | |
| `Age` | `age` | |
| `SSN` | `ssn` | |
| `MaritalStatus` | `marital_status` | |
| `MedicalRecordNumber` | `medical_record_number` | |
| `EmailAddress` | `pat_email` | |
| `WorkPhone` / `MobilePhone` / `HomePhone` | `pat_contact` | first non-empty of the three |
| `WorkPhone` | `work_phone` | |
| `WorkPhoneExt` | `work_phone_ext` | |
| `MobilePhone` | `mobile_phone` | |
| `MobilePhoneExt` | `mobile_phone_ext` | |
| `HomePhone` | `home_phone` | |
| `HomePhoneExt` | `home_phone_ext` | |
| — | `pat_contact_consent` | always `None` (not present in source data) |
| — | `pat_contact_method` | always `None` (not present in source data) |
| `EmergencyName` | `emergency_name` | |
| `EmergencyPhone` | `emergency_phone` | |
| `EmergencyPhoneExt` | `emergency_phone_ext` | |
| `AddressLine1` | `pat_addr1` | |
| `AddressLine2` | `pat_addr2` | |
| `City` | `pat_city` | |
| `State` | `pat_st` | |
| `ZipCode` | `pat_zip` | |
| `Country` | `country` | |
| `EmployerName` | `employer_name` | |
| `EmploymentStatus` | `employment_status` | |
| `PrimaryCarePhysicianId` | `primary_care_physician_id` | |
| `PrimaryCarePhysicianFullName` | `primary_care_physician_full_name` | |
| `ReferringProviderId` | `referring_provider_id` | |
| `ReferringProviderFullName` | `referring_provider_full_name` | |
| `ReferralSource` | `referral_source` | |
| `CollectionCategoryName` | `collection_category_name` | `.strip()`'d |
| `TotalBalance` | `total_balance` | string → `float` |
| `PatientBalance` | `patient_balance` | string → `float` |
| `InsuranceBalance` | `insurance_balance` | string → `float` |
| `AlertMessage` | `alert_message` | |
| `AlertShowWhenDisplayingPatientDetails` | `alert_show_patient_details` | `"True"` string → `bool` |
| `AlertShowWhenEnteringEncounters` | `alert_show_encounters` | `"True"` string → `bool` |
| `AlertShowWhenPostingPayments` | `alert_show_payments` | `"True"` string → `bool` |
| `AlertShowWhenPreparingPatientStatements` | `alert_show_statements` | `"True"` string → `bool` |
| `AlertShowWhenSchedulingAppointments` | `alert_show_appointments` | `"True"` string → `bool` |
| `AlertShowWhenViewingClaimDetails` | `alert_show_claims` | `"True"` string → `bool` |
| `LastAppointmentDate` | `last_appointment_date` | |
| `LastEncounterDate` | `last_encounter_date` | |
| `LastPaymentDate` | `last_payment_date` | |
| `LastStatementDate` | `last_statement_date` | |
| `DefaultCaseID` | `default_case_id` | |
| `DefaultCaseName` | `default_case_name` | |
| `DefaultCaseDescription` | `default_case_description` | |
| `DefaultServiceLocationId` | `default_service_location_id` | |
| `DefaultServiceLocationName` | `default_service_location_name` | |
| `DefaultRenderingProviderId` | `default_rendering_provider_id` | |
| `DefaultRenderingProviderFullName` | `default_rendering_provider_name` | |
| — | `pcn`, `pcn_original` | never written from application code - owned by the DB triggers `fn_generate_pcn_trigger` / `fn_assign_pcn_original_after_insert` |
| — | `created_at`, `updated_at`, `loaded_at` | set by the loader (insert/update time) |

Not used at all: `PracticeId` (client/group resolved by name, not id).

## patient_coverages (load_patient_coverages.py)

Built once per side (`Primary`/`Secondary`) that has a `{Side}InsurancePolicyCompanyID`.
Field names below use `{Side}` as a placeholder for `Primary` or `Secondary`.

| Patients.json field | patient_coverages column | Notes |
|---|---|---|
| — | `patient_header_id` | resolved via `patient_header` where `lower(trim(source))="tebra"` and `trim(source_id) = trim(ID)` |
| — | `source` | hardcoded `"tebra"` |
| — | `client_id`, `group_id`, `practice_id`, `pat_id`, `pat_sub_lnam`, `pat_fnam`, `pat_dob` | copied from the matched `patient_header` row, not from Patients.json directly |
| — | `pat_source` | copied from `patient_header.source` |
| — | `cov_type` | `"P"` for Primary, `"S"` for Secondary |
| — | `insurance_type` | literal `"Primary"` / `"Secondary"` |
| — | `cov_status` | always `None` (not present in source data) |
| `{Side}InsurancePolicyCompanyID`, `{Side}InsurancePolicyCompanyName` | `cov_car_id`, `cov_car_nam` | resolved through `utils/payer_lookup.find_payer` against `lookup_payers` first; falls back to the raw Patients.json id/name on no match |
| — | `cov_car_type` | from the matched `lookup_payers.payer_type` list (see `carrier_type_from_payer_types`): exactly one type → that type; anything else (zero, two, three, more, or no payer match) → `"Commercial"` |
| `{Side}InsurancePolicyCompanyID` | `company_id` | raw, unresolved |
| `{Side}InsurancePolicyCompanyName` | `company_name` | raw, unresolved |
| `{Side}InsurancePolicyNumber` | `policy_number`, `cov_sub_id` | |
| `{Side}InsurancePolicyGroupNumber` | `group_number` | |
| `{Side}InsurancePolicyCopay` | `copay` | string → `float` |
| `{Side}InsurancePolicyDeductible` | `deductible` | string → `float` |
| `{Side}InsurancePolicyEffectiveStartDate` | `effective_start_date` | defaults to Jan 1 of the current year if missing/unparseable (see `default_effective_start` in `load_patient_coverages.py`) |
| `{Side}InsurancePolicyEffectiveEndDate` | `effective_end_date` | |
| `{Side}InsurancePolicyPatientRelationshipToInsured` | `cov_rel`, `patient_relationship_to_insured` | `"S"` means the patient *is* the insured - see next rows |
| `{Side}InsurancePolicyInsuredFullName` | `insured_full_name` | patient's own `FirstName`/`LastName` if relationship is `"S"`, else this field |
| `{Side}InsurancePolicyInsuredIDNumber` | `insured_id_number` | always this field as-is - `ID` is only used to locate the correct `patient_header` row, never substituted in here |
| `{Side}InsurancePolicyInsuredSocialSecurityNumber` | `insured_ssn` | patient's own `SSN` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredDateOfBirth` | `insured_dob` | patient's own `DOB` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredGender` | `insured_gender` | patient's own `Gender` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredAddressLine1` | `insured_address1` | patient's own `AddressLine1` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredAddressLine2` | `insured_address2` | patient's own `AddressLine2` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredCity` | `insured_city` | patient's own `City` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredState` | `insured_state` | patient's own `State` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredZipCode` | `insured_zip` | patient's own `ZipCode` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredCountry` | `insured_country` | patient's own `Country` if self-insured, else this field |
| `{Side}InsurancePolicyInsuredNotes` | `insured_notes` | |
| `{Side}InsurancePolicyPlanID` | `plan_id` | |
| `{Side}InsurancePolicyPlanName` | `plan_name` | |
| `{Side}InsurancePolicyPlanAddressLine1` | `plan_address1` | |
| `{Side}InsurancePolicyPlanAddressLine2` | `plan_address2` | |
| `{Side}InsurancePolicyPlanCity` | `plan_city` | |
| `{Side}InsurancePolicyPlanState` | `plan_state` | |
| `{Side}InsurancePolicyPlanZipCode` | `plan_zip` | |
| `{Side}InsurancePolicyPlanCountry` | `plan_country` | |
| `{Side}InsurancePolicyPlanPhoneNumber` | `plan_phone` | |
| `{Side}InsurancePolicyPlanPhoneNumberExt` | `plan_phone_ext` | |
| `{Side}InsurancePolicyPlanFaxNumber` | `plan_fax` | |
| `{Side}InsurancePolicyPlanFaxNumberExt` | `plan_fax_ext` | |
| `{Side}InsurancePolicyPlanAdjusterFullName` | `plan_adjuster_name` | |
| `{Side}InsurancePolicyNumber` | `cov_dep_id` | only if self-insured, else `None` |
| — | `cov_dep_name`, `cov_start_date`, `cov_end_date` | always `None` (not present in source data) |
| — | `active` | `True` on insert; set `False` on the row being replaced when `decide_coverage_action` returns `terminate_and_insert` |
| — | `created_at`, `updated_at` | set by the loader |

### Active/inactive decision (not a 1:1 field mapping)

Once `payer_lookup.find_payer` resolves the carrier for both the incoming
and the patient's current active coverage of that type,
`utils/coverage_rules.decide_coverage_action` decides what happens to the
row - see that module's docstring for the three outcomes (update in
place / insert alongside / terminate old + insert new).
