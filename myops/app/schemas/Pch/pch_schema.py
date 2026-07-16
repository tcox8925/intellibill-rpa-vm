from datetime import date
import uuid
from pydantic import BaseModel
from typing import List, Optional

class PchTopRxExpenseSchema(BaseModel):
    name: Optional[str] = None
    value: Optional[float] = None
    class Config:
        from_attributes = True

class PchMemberRosterSchema(BaseModel):
    amisys_number: Optional[str] = None
    first_name: Optional[str] = None
    middle_name: Optional[str]= None
    last_name: Optional[str]= None
    full_name: Optional[str] = None   
    member_dob: Optional[str] = None
    member_age: Optional[int] = None 
    product: Optional[str] = None
    population_health_category: Optional[str] = None
    primary_risk_category: Optional[str] = None
    member_city: Optional[str] = None
    member_state: Optional[str] = None
    member_zip: Optional[str] = None
    member_status: Optional[str] = None
    report_date: Optional[str] = None
    gender: Optional[str] = None
    member_address_line_1: Optional[str] = None
    member_address_line_2: Optional[str] = None
    member_phone_number: Optional[str] = None
    email_address: Optional[str] = None
    pcp_npi: Optional[str] = None
    risk_score: Optional[float] = None
    report_date: Optional[str] = None
    pch_name: Optional[str] = None
    primary_diagnosis: Optional[str] = None
    provider_name: Optional[str] = None
    coverage_level: Optional[str] = None


    class Config:
        from_attributes = True
            
class PchMeasurementSchema(BaseModel):
    report_date : Optional[str] = None
    bmi  : Optional[str] = None  
    height : Optional[str] = None
    weight : Optional[str] = None
    member_id : Optional[str] = None
    pk_id : Optional[uuid.UUID] = None
    source : Optional[str] = None
    class Config:
        from_attributes = True
    
class PchPreventiveCareSchema(BaseModel):
    pk_id: Optional[uuid.UUID] = None
    member_id: Optional[str] = None
    status: Optional[str] = None
    care_type: Optional[str] = None
    care_name: Optional[str] = None
    diagnosis: Optional[str] = None
    onboarding_date: Optional[str] = None
    source: Optional[str] = None
    class Config:
        from_attributes = True
    
class PchHealthCategorySchema(BaseModel):
    population_health_category: Optional[str] = None
    report_date: Optional[date] = None
    member_status: Optional[str] = None
    count: Optional[int] = None
    member_id: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None
    source: Optional[str] = None
    
    class Config:        
        from_attributes = True
    
class PchDiagnosisSchema(BaseModel):
    diagnosis: Optional[str] = None
    type: Optional[str] = None
    count: Optional[int] = None
    date: Optional[str] = None
    source: Optional[str] = None
    primary_diagnosis_code: Optional[str] = None
    
    class Config:        
        from_attributes = True

class PchDiagnosisCreateSchema(BaseModel):
    member_amisys_nbr: str
    primary_diag_code: str

    status: Optional[str]
    report_date: Optional[str]  
    source: Optional[str]

    class Config:
        from_attributes = True
    
class PchVitalsSchema(BaseModel):
    report_date : Optional[str] = None
    systolic : Optional[str] = None
    diastolic : Optional[str] = None
    member_id : Optional[str] = None
    source : Optional[str] = None
    pk_id : Optional[uuid.UUID] = None
    class Config:        
        from_attributes = True
   
class PchCareGapDetailSchema(BaseModel):
    measure_status: Optional[str] = None
    gap_type:  Optional[str] = None
    measure : Optional[str] = None
    service_strt : Optional[date] = None
    service_end : Optional[date] = None
    source: Optional[str] = None
    mem_id : Optional[str] = None
    
    class Config:        
        from_attributes = True

class PchDiagnosisDetailSchema(BaseModel):
    diagnosis: Optional[str] = None
    type: Optional[str] = None
    count: Optional[int] = None
    date: Optional[str] = None
    source: Optional[str] = None
    records: List[PchDiagnosisSchema]
    
    class Config:
        from_attributes = True
        populate_by_name = True

class PchRiskCategorySchema(BaseModel):
    primary_risk_category: Optional[str] = None
    report_date: Optional[date] = None
    member_status: Optional[str] = None
    count: Optional[int] = None
    member_id: Optional[str] = None
    source: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None
    class Config:        
        from_attributes = True
    
class PchRXClaimHistorySchema(BaseModel):
    status: Optional[str] = None
    member_amisys_nbr: Optional[str] = None
    prescribing_npi: Optional[str] = None
    prescribing_npi_name: Optional[str] = None
    pcp_npi: Optional[str] = None
    pcp_name: Optional[str] = None     
    pcp_inst_name: Optional[str] = None
    drug_type_code: Optional[str] = None
    drug_desc: Optional[str] = None
    drug_type_desc: Optional[str] = None
    refill_nbr: Optional[float] = None
    formulary_ind: Optional[str] = None
    fill_date_dim_ck: Optional[str] = None
    prescription_date_dim_ck: Optional[str] = None
    coverage_level: Optional[str] = None
    product_line: Optional[str] = None
    mcc: Optional[str] = None
    source: Optional[str] = None
    
    class Config:
        from_attributes = True

class PchRXClaimCreateSchema(BaseModel):
    status: Optional[str]
    member_amisys_nbr: str

    prescribing_npi: Optional[str]
    prescribing_npi_name: Optional[str]

    pcp_npi: Optional[str]
    pcp_prac_first_name: Optional[str]
    pcp_prac_middle_name: Optional[str]
    pcp_prac_last_name: Optional[str]
    pcp_inst_name: Optional[str]

    drug_type_code: Optional[str]
    drug_desc: Optional[str]
    drug_type_desc: Optional[str]

    refill_nbr: Optional[str]
    formulary_ind: Optional[str]

    fill_date_dim_ck: Optional[str]
    prescription_date_dim_ck: Optional[str]

    coverage_level: Optional[str]
    product_line: Optional[str]
    mcc: Optional[str]

    source: Optional[str]

    class Config:
        from_attributes = True

class PchRXClaimUpdateSchema(BaseModel):
    status: Optional[str]
    prescribing_npi: Optional[str]
    prescribing_npi_name: Optional[str]

    pcp_npi: Optional[str]
    pcp_prac_first_name: Optional[str]
    pcp_prac_middle_name: Optional[str]
    pcp_prac_last_name: Optional[str]
    pcp_inst_name: Optional[str]

    drug_type_code: Optional[str]
    drug_desc: Optional[str]
    drug_type_desc: Optional[str]

    refill_nbr: Optional[str]
    formulary_ind: Optional[str]

    fill_date_dim_ck: Optional[str]
    prescription_date_dim_ck: Optional[str]

    coverage_level: Optional[str]
    product_line: Optional[str]
    mcc: Optional[str]

    source: Optional[str]
    class Config:
        from_attributes = True
        
class PchPrescriptionSchema(BaseModel):
    pk_id: Optional[uuid.UUID] = None
    member_amisys_nbr: Optional[str] = None
    drug_type_code: Optional[str] = None
    drug_desc: Optional[str] = None
    drug_type_desc: Optional[str] = None
    refill_nbr: Optional[float] = None
    formulary_ind: Optional[str] = None
    fill_date_dim_ck: Optional[str] = None
    prescription_date_dim_ck: Optional[str] = None
    adherence_status: Optional[str] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True
        
class PchPrescriptionCreateSchema(BaseModel):
    member_amisys_nbr: str

    drug_type_code: Optional[str]
    drug_desc: Optional[str]
    drug_type_desc: Optional[str]

    refill_nbr: Optional[str]
    formulary_ind: Optional[str]

    fill_date_dim_ck: Optional[str]
    prescription_date_dim_ck: Optional[str]

    source: Optional[str]
    status: Optional[str] = "ACTIVE"

    class Config:
        from_attributes = True
        
class PchInpatientCensusSchema(BaseModel):
    pcp_npi: Optional[str] = None
    pcp_npi_name: Optional[str] = None
    facility: Optional[str] = None
    service_type: Optional[str] = None
    diagnosis_code: Optional[str] = None
    diagnosis_code_description: Optional[str] = None
    admit_date: Optional[str] = None
    discharge_date: Optional[str] = None
    discharge_code: Optional[str] = None
    discharge_code_description: Optional[str] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True

class InpatientPayload(BaseModel):
    status: Optional[str]
    pcp_npi: Optional[str]
    pcp_npi_name: Optional[str]
    facility: Optional[str]
    service_type: Optional[str]
    diagnosis_code: Optional[str]
    diagnosis_code_description: Optional[str]
    admit_date: Optional[str]
    source: Optional[str]
    class Config:
        from_attributes = True
class DischargePayload(BaseModel):
    discharge_date: Optional[str]
    discharge_code: Optional[str]
    discharge_code_description: Optional[str]
    class Config:
        from_attributes = True
class PchHospitalizationCreateUpdateSchema(BaseModel):
    amisys_nbr: str
    inpatient: Optional[InpatientPayload]
    discharge: Optional[DischargePayload]
    class Config:
        from_attributes = True
class PchMedicalClaimHistorySchema(BaseModel):
    status: Optional[str] = None
    pcp_npi: Optional[str] = None
    full_name: Optional[str] = None   
    service_start_date_dim_ck: Optional[str] = None
    service_end_date_dim_ck: Optional[str] = None
    claim_paid_date_dim_ck: Optional[str] = None
    place_of_serv_code: Optional[str] = None
    place_of_serv_desc: Optional[str] = None
    coverage_level: Optional[str] = None
    product_line: Optional[str] = None
    mcc: Optional[str] = None
    proc_code: Optional[str] = None
    rev_code: Optional[str] = None
    primary_diag_code: Optional[str] = None
    pay_to_inst_name: Optional[str] = None
    admission_date_dim_ck: Optional[str] = None
    dischg_date_dim_ck: Optional[str] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True

class PchMedicalClaimCreateSchema(BaseModel):
    member_amisys_nbr: str

    status: Optional[str]
    pcp_npi: Optional[str]
    pcp_prac_title_name: Optional[str]
    pcp_prac_first_name: Optional[str]
    pcp_prac_middle_name: Optional[str]
    pcp_prac_last_name: Optional[str]

    service_start_date_dim_ck: Optional[str]
    service_end_date_dim_ck: Optional[str]
    claim_paid_date_dim_ck: Optional[str]

    place_of_serv_code: Optional[str]
    place_of_serv_desc: Optional[str]

    coverage_level: Optional[str]
    product_line: Optional[str]
    mcc: Optional[str]

    proc_code: Optional[str]
    rev_code: Optional[str]
    primary_diag_code: Optional[str]

    pay_to_inst_name: Optional[str]
    admission_date_dim_ck: Optional[str]
    dischg_date_dim_ck: Optional[str]

    source: Optional[str]

    class Config:
        from_attributes = True
class PchMedicalClaimUpdateSchema(BaseModel):
    status: Optional[str]
    pcp_npi: Optional[str]
    pcp_prac_title_name: Optional[str]
    pcp_prac_first_name: Optional[str]
    pcp_prac_middle_name: Optional[str]
    pcp_prac_last_name: Optional[str]

    service_start_date_dim_ck: Optional[str]
    service_end_date_dim_ck: Optional[str]
    claim_paid_date_dim_ck: Optional[str]

    place_of_serv_code: Optional[str]
    place_of_serv_desc: Optional[str]

    coverage_level: Optional[str]
    product_line: Optional[str]
    mcc: Optional[str]

    proc_code: Optional[str]
    rev_code: Optional[str]
    primary_diag_code: Optional[str]

    pay_to_inst_name: Optional[str]
    admission_date_dim_ck: Optional[str]
    dischg_date_dim_ck: Optional[str]

    source: Optional[str]

    class Config:
        from_attributes = True
        
class PchImmunizationSchema(BaseModel):
    pk_id: Optional[uuid.UUID] = None
    member_id: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None
    fields: Optional[str] = None
    category: Optional[str] = None
    procedure: Optional[str] = None
    requirement: Optional[str] = None
    condition: Optional[str] = None
    complete_date: Optional[str] = None
    immunization_id: Optional[uuid.UUID] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True

class PchScreeningSchema(BaseModel):
    pk_id: Optional[uuid.UUID] = None
    member_id: Optional[str] = None
    status: Optional[str] = None
    type: Optional[str] = None
    fields: Optional[str] = None
    category: Optional[str] = None
    procedure: Optional[str] = None
    requirement: Optional[str] = None
    condition: Optional[str] = None
    complete_date: Optional[str] = None
    immunization_id: Optional[uuid.UUID] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True

class PchMemberEditSchema(BaseModel):
    first_name: Optional[str] = None
    middle_name: Optional[str] = None
    last_name: Optional[str] = None
    member_dob: Optional[str] = None
    gender: Optional[str] = None
    member_phone_number: Optional[str] = None
    member_address_line_1: Optional[str] = None
    member_address_line_2: Optional[str] = None
    member_city: Optional[str] = None
    member_state: Optional[str] = None
    member_zip: Optional[str] = None
    email_address: Optional[str] = None
    member_status: Optional[str] = None
    product: Optional[str] = None
    population_health_category: Optional[str] = None
    primary_risk_category: Optional[str] = None
    risk_score: Optional[str] = None
    pcp_npi: Optional[str] = None
    pcp_prac_title_name: Optional[str] = None
    pcp_prac_first_name: Optional[str] = None
    pcp_prac_middle_name: Optional[str] = None
    pcp_prac_last_name: Optional[str] = None
    coverage_level: Optional[str] = None


    class Config:
        from_attributes = True

class CoverageResponseSchema(BaseModel):
    pat_id: Optional[str]
    cov_type: Optional[str]
    status: Optional[str]
    carrier_type: Optional[str]
    cov_car_name: Optional[str]
    effective_start_date: Optional[str] 
    effective_end_date: Optional[str] 
    relationship: Optional[str]
    pk_id: Optional[uuid.UUID]
    source: Optional[str]
    
    class Config:
        from_attributes = True

class PatientCoverageSchema(BaseModel):
    pk_id: uuid.UUID
    pat_id: str
    cov_type: Optional[str]
    cov_car_type: Optional[str]
    cov_car_id: Optional[str]
    cov_car_nam: Optional[str]
    cov_rel: Optional[str]
    cov_sub_id: Optional[str]
    insured_full_name: Optional[str]
    cov_dep_id: Optional[str]
    cov_dep_name: Optional[str]
    cov_start_date: Optional[str]
    cov_end_date: Optional[str]
    cov_status: Optional[str]

    class Config:
        from_attributes = True
    
class PatientCoveragesSchema(BaseModel):
    pat_id: Optional[str] = None # optional on edit
    cov_status: Optional[str] = None #status
    cov_type: Optional[str] = None
    cov_car_id: Optional[str] = None # car indicator
    cov_car_nam: Optional[str] = None # car name
    cov_car_type: Optional[str] = None
    cov_sub_id: Optional[str] = None
    cov_dep_id: Optional[str] = None
    cov_start_date: Optional[str] = None
    cov_end_date: Optional[str] = None
    insurance_type: Optional[str] = None
    company_id: Optional[str] = None
    company_name: Optional[str] = None
    policy_number: Optional[str] = None
    group_number: Optional[str] = None
    effective_start_date: Optional[str] = None
    effective_end_date: Optional[str] = None
    patient_relationship_to_insured : Optional[str] = None
    insured_full_name: Optional[str] = None #subscriber name
    insured_id_number: Optional[str] = None #subscriber id
    insured_ssn: Optional[str] = None
    insured_dob: Optional[str] = None
    insured_gender: Optional[str] = None
    insured_address1 : Optional[str] = None
    insured_address2 : Optional[str] = None
    insured_city: Optional[str] = None
    insured_state: Optional[str] = None
    insured_zip: Optional[str] = None
    insured_country: Optional[str] = None
    pk_id: Optional[uuid.UUID] = None
    member_id: Optional[str] = None
    source: str = "EDI"
    pat_source: str = "EDI"