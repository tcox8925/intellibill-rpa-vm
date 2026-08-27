/**
 * LOOKUP TABLES SCHEMA
 * This file contains all lookup/reference table schemas for the EDI_Tebra database
 */

import { pgTable, integer, bigint, uuid, varchar, text, pgSchema, serial, boolean, primaryKey, pgEnum, jsonb } from 'drizzle-orm/pg-core';
import { client, practice, group } from './new_schema.js';

// ============================================================================
// SCHEMA DEFINITION
// ============================================================================
export const ediTebraSchema = pgSchema(process.env.DB_SCHEMA || 'EDI_Tebra');

// ============================================================================
// LOOKUP TABLES
// ============================================================================

/**
 * Lookup Providers table - Provider reference data
 */
export const providers = ediTebraSchema.table('lookup_providers', {
    Serial: serial('serial'),
    ID: uuid('id').primaryKey().defaultRandom().notNull(),
    client_id: integer('client_id').references(() => client.clientId),
    group_id: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id'),
    FullName: varchar('full_name', { length: 500 }),
    Active: varchar('active', { length: 500 }),
    DepartmentName: varchar('department_name', { length: 500 }),
    Suffix: varchar('suffix', { length: 500 }),
    Prefix: varchar('prefix', { length: 500 }),
    EncounterFormName: varchar('encounter_form_name', { length: 500 }),
    FirstName: varchar('first_name', { length: 500 }),
    MiddleName: varchar('middle_name', { length: 500 }),
    LastName: varchar('last_name', { length: 500 }),
    Degree: varchar('degree', { length: 500 }),
    SpecialtyName: varchar('specialty_name', { length: 500 }),
    Type: varchar('type', { length: 500 }),
    BillingType: varchar('billing_type', { length: 500 }),
    providerTaxId: varchar('provider_tax_id', { length: 500 }),
    taxonomy: varchar('taxonomy', { length: 500 }),
    pecos: varchar('pecos', { length: 500 }),
    medicaid: varchar('medicaid', { length: 500 }),
    npn: varchar('npn', { length: 500 }),
    NationalProviderIdentifier: varchar('national_provider_identifier', { length: 500 }),
    ptan: varchar('ptan', { length: 500 }),
    ProviderPerformanceReportActive: varchar('provider_performance_report_active', { length: 500 }),
    ProviderPerformanceReportScope: varchar('provider_performance_report_scope', { length: 500 }),
    ProviderPerformanceReportFequency: varchar('provider_performance_report_fequency', { length: 500 }),
    ProviderPerformanceReportDelay: varchar('provider_performance_report_delay', { length: 500 }),
    ProviderPerformanceReportCCEmailRecipients: text('provider_performance_report_cc_email_recipients'),
    EmailAddress: varchar('email_address', { length: 500 }),
    WorkPhone: varchar('work_phone', { length: 500 }),
    WorkPhoneExt: varchar('work_phone_ext', { length: 500 }),
    MobilePhone: varchar('mobile_phone', { length: 500 }),
    MobilePhoneExt: varchar('mobile_phone_ext', { length: 500 }),
    HomePhone: varchar('home_phone', { length: 500 }),
    HomePhoneExt: varchar('home_phone_ext', { length: 500 }),
    Fax: varchar('fax', { length: 500 }),
    FaxExt: varchar('fax_ext', { length: 500 }),
    Pager: varchar('pager', { length: 500 }),
    PagerExt: varchar('pager_ext', { length: 500 }),
    AddressLine1: varchar('address_line1', { length: 500 }),
    AddressLine2: varchar('address_line2', { length: 500 }),
    City: varchar('city', { length: 500 }),
    State: varchar('state', { length: 500 }),
    ZipCode: varchar('zip_code', { length: 500 }),
    Country: varchar('country', { length: 500 }),
    CreatedDate: varchar('created_date', { length: 500 }),
    LastModifiedDate: varchar('last_modified_date', { length: 500 }),
    Notes: text('notes'),
    SocialSecurityNumber: varchar('social_security_number', { length: 500 }),
});


/**
 * Lookup Procedure Codes table
 */
export const procedureCodes = ediTebraSchema.table('lookup_procedure_codes', {
    serial: serial('serial'),
    id: text('id'),
    active: boolean('active'),
    billableCode: text('billable_code'),
    createdDate: text('created_date'),
    customersSpecific: text('customers_pecific'),
    defaultUnits: text('default_units'),
    drugName: text('drug_name'),
    localName: text('local_name'),
    modifiedDate: text('modified_date'),
    ndc: text('ndc'),
    officialDescription: text('official_description'),
    officialName: text('official_name'),
    clientName: text('client_name'),
    procedureCode: text('procedure_code').primaryKey().notNull(),
    procedureCodeCategoryId: text('procedure_code_category_id'),
    typeOfServiceCode: text('type_of_service_code'),
});

/**
 * Lookup Carriers table - Insurance carrier reference data
 */
export const carriers = ediTebraSchema.table('lookup_carriers', {
    payerId: varchar('payer_id', { length: 20 }),
    payerName: varchar('payer_name', { length: 255 }).notNull(),
    payerType: varchar('payer_type', { length: 50 }),
    claimPayerId: varchar('claim_payer_id', { length: 20 }),
    eligibilityPayerId: varchar('eligibility_payer_id', { length: 20 }),
    eraPayerId: varchar('era_payer_id', { length: 20 }),
    addressLine1: varchar('address_line1', { length: 255 }),
    addressLine2: varchar('address_line2', { length: 255 }),
    city: varchar('city', { length: 100 }),
    state: varchar('state', { length: 10 }),
    zipCode: varchar('zip_code', { length: 20 }),
    country: varchar('country', { length: 100 }),
    activeStatus: boolean('active_status').default(true),
});

/**
 * Lookup Payers source table - pre-aggregation payer reference data kept as backup after the table swap
 */
export const lookupPayersSource = ediTebraSchema.table('lookup_payers_source', {
    id: serial('id').primaryKey(),
    sortId: bigint('sort_id', { mode: 'number' }),
    payerName: text('payer_name'),
    payerId: text('payer_id'),
    payerType: text('payer_type'),
    transactionType: text('transaction_type'),
    available: text('available'),
    nonPar: text('non_par'),
    enrollment: text('enrollment'),
    secondary: text('secondary'),
    attachment: text('attachment'),
    wcAuto: text('wc_auto'),
    notes: text('notes'),
    payerAlias: text('payer_alias').array(),
    portal: text('portal'),
    login: text('login'),
    password: text('password'),
    activeStatus: boolean('active_status').default(true),
});

/**
 * Lookup Payers table - Payer reference data grouped to one row per payer_id
 */
export const lookupPayers = ediTebraSchema.table('lookup_payers', {
    id: serial('id').primaryKey(),
    sortId: bigint('sort_id', { mode: 'number' }),
    payerName: text('payer_name'),
    payerId: text('payer_id'),
    payerType: text('payer_type').array(),
    transactionType: text('transaction_type').array(),
    available: text('available'),
    nonPar: text('non_par'),
    enrollment: text('enrollment'),
    secondary: text('secondary'),
    attachment: text('attachment'),
    wcAuto: text('wc_auto'),
    notes: text('notes'),
    payerAlias: text('payer_alias').array(),
    portal: text('portal'),
    login: text('login'),
    password: text('password'),
    integrationDetails: jsonb('integration_details'),
    activeStatus: boolean('active_status').default(true),
    isRenderingProviderTaxonomyRequired: boolean('is_rend_prv_taxonomy_req').default(true),
});

/**
 * Lookup Zip Codes table - ZIP code demographic and geographic data
 */
export const zipCodes = ediTebraSchema.table('lookup_zip_codes', {
    ZipCode: text('zip_code'),
    PrimaryRecord: text('primary_record'),
    Population: text('population'),
    HouseholdsPerZipCode: text('households_per_zipcode'),
    WhitePopulation: text('white_population'),
    BlackPopulation: text('black_population'),
    HispanicPopulation: text('hispanic_population'),
    AsianPopulation: text('asian_population'),
    HawaiianPopulation: text('hawaiian_population'),
    IndianPopulation: text('indian_population'),
    OtherPopulation: text('other_population'),
    MalePopulation: text('male_population'),
    FemalePopulation: text('female_population'),
    PersonsPerHousehold: text('persons_per_household'),
    AverageHouseValue: text('average_house_value'),
    IncomePerHousehold: text('income_per_household'),
    MedianAge: text('median_age'),
    MedianAgeMale: text('median_age_male'),
    MedianAgeFemale: text('median_age_female'),
    Latitude: text('latitude'),
    Longitude: text('longitude'),
    Elevation: text('elevation'),
    State: text('state'),
    StateFullName: text('state_full_name'),
    CityType: text('city_type'),
    CityAliasAbbreviation: text('city_alias_abbreviation'),
    AreaCode: text('area_code'),
    City: text('city'),
    CityAliasName: text('city_alias_name'),
    County: text('county'),
    CountyFIPS: text('county_fips'),
    StateFIPS: text('state_fips'),
    TimeZone: text('time_zone'),
    DayLightSaving: text('day_light_saving'),
    MSA: text('msa'),
    PMSA: text('pmsa'),
    CSA: text('csa'),
    CBSA: text('cbsa'),
    CBSA_Div: text('cbsa_div'),
    CBSA_Type: text('cbsa_type'),
    CBSA_Name: text('cbsa_name'),
    MSA_Name: text('msa_name'),
    PMSA_Name: text('pmsa_name'),
    Region: text('region'),
    Division: text('division'),
    MailingName: text('mailing_name'),
    NumberOfBusinesses: text('number_of_businesses'),
    NumberOfEmployees: text('number_of_employees'),
    BusinessFirstQuarterPayroll: text('business_first_quarter_payroll'),
    BusinessAnnualPayroll: text('business_annual_payroll'),
    BusinessEmploymentFlag: text('business_employment_flag'),
    GrowthRank: text('growth_rank'),
    GrowingCountiesA: text('growing_counties_a'),
    GrowingCountiesB: text('growing_counties_b'),
    GrowthIncreaseNumber: text('growth_increase_number'),
    GrowthIncreasePercentage: text('growth_increase_percentage'),
    CBSAPopulation: text('cbsa_population'),
    CBSADivisionPopulation: text('cbsa_division_population'),
    CongressionalDistrict: text('congressional_district'),
    CongressionalLandArea: text('congressionall_and_area'),
    DeliveryResidential: text('delivery_residential'),
    DeliveryBusiness: text('delivery_business'),
    DeliveryTotal: text('delivery_total'),
    PreferredLastLineKey: text('preferred_last_linekey'),
    ClassificationCode: text('classification_code'),
    MultiCounty: text('multi_county'),
    CSAName: text('csa_name'),
    CBSA_Div_Name: text('cbsa_div_name'),
    CityStateKey: text('city_statekey'),
    PopulationEstimate: text('population_estimate'),
    LandArea: text('land_area'),
    WaterArea: text('water_area'),
    CityAliasCode: text('city_alias_code'),
    CityMixedCase: text('city_mixed_case'),
    CityAliasMixedCase: text('city_alias_mixed_case'),
    BoxCount: text('box_count'),
    SFDU: text('sfdu'),
    MFDU: text('mfdu'),
    StateANSI: text('state_ansi'),
    CountyANSI: text('county_ansi'),
    ZIPIntroDate: text('zip_intro_date'),
    AliasIntroDate: text('alias_intro_date'),
    FacilityCode: text('facility_code'),
    CityDeliveryIndicator: text('city_delivery_indicator'),
    CarrierRouteRateSortation: text('carrier_route_rate_sortation'),
    FinanceNumber: text('finance_number'),
    UniqueZIPName: text('unique_zip_name'),
    SSAStateCountyCode: text('ssa_state_county_code'),
    MedicareCBSACode: text('medicare_cbsa_code'),
    MedicareCBSAName: text('medicare_cbsa_name'),
    MedicareCBSAType: text('medicare_cbsa_type'),
    MarketRatingAreaID: text('market_rating_area_id'),
    CountyMixedCase: text('county_mixed_case'),
});

/**
 * Routes To Enum - Determines where a reason code routes to in the system
 */
export const routesToEnum = pgEnum('routes_to_enum', ['CLOSE', 'PATIENT_RESPONSIBILITY', 'DENIAL_MGMT']);

/**
 * Lookup Group Code table - Group codes for remittance processing
 */
export const groupCodeLookup = ediTebraSchema.table('lookup_group_code', {
    groupCode: varchar('group_code', { length: 5 }).primaryKey().notNull(),
    description: text('description'),
    usageNotes: text('usage_notes'),
    owedBy: varchar('owed_by', { length: 50 }),
});

/**
 * Lookup Reason Code table - Reason codes for claim denials and adjustments
 */
export const reasonCodeLookup = ediTebraSchema.table('lookup_reason_code', {
    reasonCode: varchar('reason_code', { length: 10 }).primaryKey().notNull(),
    description: text('description'),
    groupCode: varchar('group_code', { length: 5 }),
    category: varchar('category', { length: 100 }),
    status: varchar('status', { length: 20 }),
    routesTo: routesToEnum('routes_to'),
    codeStatus: boolean('code_status').default(true),
});

/**
 * Lookup ICD Codes table - ICD diagnosis codes
 */
export const icdCodes = ediTebraSchema.table('lookup_icd_codes', {
    ICDCode: varchar('icd_code', { length: 100 }).primaryKey().notNull(),
    description: text('description'),
});