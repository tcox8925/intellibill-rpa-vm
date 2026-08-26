/**
 * ALL DATABASE SCHEMAS - CONSOLIDATED FILE
 * This file contains all table schemas for the EDI_Tebra database
 * 
 * Table Categories:
 * - Core Infrastructure: client, group, practice
 * - Providers: physician
 * - Patients: patient_header, patient_guarantor, patient_notes, patient_coverages
 * - Claims: claim_header, claim_details, claim_notes, claim_attachments, claim_audit_history
 * - Remittance: remit_chkhdr, remit_chk_summary, remit_clmhdr, remit_clmsrv
 * - Lookups: group_code_lookup, reason_code_lookup, rarc_codes
 */

import { pgTable, integer, varchar, timestamp, text, pgSchema, serial, boolean, numeric, primaryKey, jsonb, uuid, date, time, real, smallint, pgEnum, index, uniqueIndex, bigint, char } from 'drizzle-orm/pg-core';
import { relations, sql } from 'drizzle-orm';
import { users } from './schema.js';

// ============================================================================
// SCHEMA DEFINITION
// ============================================================================
export const ediTebraSchema = pgSchema(process.env.DB_SCHEMA || 'EDI_Tebra');

// ============================================================================
// ENUMS
// ============================================================================

/**
 * Client Settings ON/OFF Status Enum
 */
export const clientSettingsOnOffEnum = pgEnum('client_settings_on_off_enum', ['ON', 'OFF']);

/**
 * AI Execution Override Status Enum
 */
export const overrideStatusForAiExecEnum = pgEnum('override_status_for_ai_exec_enum', ['none', 'overridden_by_user', 'overridden_by_system']);

/**
 * Risk Classification Enum - ML model denial risk levels
 */
export const riskClassificationEnum = pgEnum('risk_classification_enum', ['LOW', 'MEDIUM', 'HIGH']);

/**
 * Actual Outcome Enum - Claim settlement outcomes
 */
export const actualOutcomeEnum = pgEnum('actual_outcome_enum', ['PAID', 'DENIED', 'PARTIAL', 'PENDING', 'APPEALED', 'PATIENT_RESPONSIBILITY', 'WRITE_OFF']);

/**
 * Master Code Type Enum
 */
export const masterCodeTypeEnum = ediTebraSchema.enum('master_code_type_enum', ['cpt', 'hcpcs', 'icd_9', 'icd_10']);

// ============================================================================
// CORE INFRASTRUCTURE TABLES
// ============================================================================

/**
 * Client table - Main client/organization information
 */
export const client = ediTebraSchema.table('client', {
    clientId: serial('client_id').primaryKey().notNull(),
    clientStatus: varchar('client_status', { length: 500 }),
    status: boolean('status').default(true).notNull(),
    clientTaxid: varchar('client_taxid', { length: 500 }),
    clientName: varchar('client_name', { length: 500 }),
    clientContactLnam: varchar('client_contact_lnam', { length: 500 }),
    clientContactFnam: varchar('client_contact_fnam', { length: 500 }),
    clientContactEmail: varchar('client_contact_email', { length: 500 }),
    clientContactNumber: varchar('client_contact_number', { length: 500 }),
    clientAddr1: varchar('client_addr1', { length: 500 }),
    clientAddr2: varchar('client_addr2', { length: 500 }),
    clientCity: varchar('client_city', { length: 500 }),
    clientState: varchar('client_state', { length: 500 }),
    clientZip: varchar('client_zip', { length: 500 }),
    clientLogo: text('client_logo'),
    denialRiskThreshold: integer('denial_risk_threshold').default(30).notNull(), // 0-100, default 30%
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Client Settings table - Track client module toggles and configurations
 */
export const clientSettings = ediTebraSchema.table('client_settings', {
    id: uuid('id').primaryKey().defaultRandom(),
    clientId: integer('client_id').notNull().references(() => client.clientId, { onDelete: 'cascade' }),
    module: varchar('module', { length: 500 }).notNull(),
    toggle: varchar('toggle', { length: 500 }).notNull(),
    description: text('description'),
    status: boolean('status').notNull().default(false),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});
/**
 * Group Settings table - Track group module toggles and configurations
 */
export const groupSettings = ediTebraSchema.table('group_settings', {
    id: uuid('id').primaryKey().defaultRandom(),
    groupId: integer('group_id').notNull().references(() => group.id, { onDelete: 'cascade' }),
    module: varchar('module', { length: 500 }).notNull(),
    toggle: varchar('toggle', { length: 500 }).notNull(),
    description: text('description'),
    status: boolean('status').notNull().default(false),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});
/**
 * Auto Pilot Logs table - Audit log for client settings changes
 */
export const autoPilotLogs = ediTebraSchema.table('auto_pilot_logs', {
    id: uuid('id').primaryKey().defaultRandom(),
    clientId: integer('client_id').notNull().references(() => client.clientId, { onDelete: 'cascade' }),
    clientSettingsId: uuid('client_settings_id').notNull().references(() => clientSettings.id, { onDelete: 'cascade' }),
    oldValue: clientSettingsOnOffEnum('old_value'),
    newValue: clientSettingsOnOffEnum('new_value'),
    changedBy: uuid('changed_by').notNull(),
    changedAt: timestamp('changed_at').defaultNow().notNull(),
});
/**
 * Auto Pilot Logs table - Audit log for group settings changes
 */

export const groupAutoPilotLogs = ediTebraSchema.table('group_auto_pilot_logs', {
    id: uuid('id').primaryKey().defaultRandom(),        
    groupId: integer('group_id').notNull().references(() => group.id, { onDelete: 'cascade' }),
    groupSettingsId: uuid('group_settings_id').notNull().references(() => groupSettings.id, { onDelete: 'cascade' }),
    oldValue: clientSettingsOnOffEnum('old_value'),
    newValue: clientSettingsOnOffEnum('new_value'),
    changedBy: uuid('changed_by').notNull(),
    changedAt: timestamp('changed_at').defaultNow().notNull(),
});

// ============================================================================
// MEDICAL EXTRACTION TABLES
// ============================================================================

export const medicalExtractionPatients = ediTebraSchema.table('medical_extraction_patients', {
    id: serial('id').primaryKey(),
    clientId: integer('client_id').references(() => client.clientId),
    groupId: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id').references(() => practice.id),
    patientName: varchar('patient_name', { length: 255 }).notNull(),
    patientDob: date('patient_dob').notNull(),
    recordCount: integer('record_count').default(1),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
}, (table) => ({
    patientNameIdx: index('idx_medical_extraction_patients_name').on(table.patientName),
    patientDobIdx: index('idx_medical_extraction_patients_dob').on(table.patientDob),
}));

export const medicalExtractionFiles = ediTebraSchema.table('medical_extraction_files', {
    id: serial('id').primaryKey(),
    patientId: integer('patient_id').references(() => medicalExtractionPatients.id, { onDelete: 'cascade' }),
    fileName: varchar('file_name', { length: 500 }).notNull(),
    evaluationDateTime: timestamp('evaluation_date_time'),
    visitDateTime: timestamp('visit_date_time'),
    azureBlobUrl: text('azure_blob_url'),
    ocrText: text('ocr_text'),
    finalOutput: jsonb('final_output'),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
    rpaAppointmentId: varchar('rpa_appointment_id', { length: 500 }),
    generatedClaimId: uuid('generated_claim_id'),
    retryCount: integer('retry_count').default(0).notNull(),
    claimCreationFailureReason: text('claim_creation_failure_reason'),
    sftpFilePath: text('sftp_file_path'),
    source: varchar('source', { length: 100 }).default('tebra').notNull(),
    sourcePatientId: varchar('source_patient_id', { length: 500 }),
    manifestEntry: jsonb('manifest_entry'),
    // Dedup key is (source, sourcePatientId, manifestEntry->>'appt_date') —
    // no separate column for it; enforced by a partial expression unique
    // index instead — see
    // 00116_add_medical_extraction_files_dedup_constraint.sql.
}, (table) => ({
    patientIdIdx: index('idx_medical_extraction_files_patient_id').on(table.patientId),
    createdAtIdx: index('idx_medical_extraction_files_created_at').on(table.createdAt),
}));

/**
 * Auto Pilot AI Execution Logs table - Audit log for AI-driven claim actions
 */
export const autoPilotAiExecutionLogs = ediTebraSchema.table('auto_pilot_ai_execution_logs', {
    id: uuid('id').primaryKey().defaultRandom(),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId, { onDelete: 'cascade' }),
    actionCategory: varchar('action_category', { length: 500 }).notNull(),
    confidenceScore: numeric('confidence_score', { precision: 5, scale: 2 }).notNull(),
    actionTaken: text('action_taken').notNull(),
    overrideStatus: overrideStatusForAiExecEnum('override_status').notNull().default('none'),
    actedBy: uuid('acted_by'),
    createdAt: timestamp('created_at').defaultNow().notNull(),
});

/**
 * Clearing Houses table - Clearing house credentials and configuration
 */
export const clearingHouses = ediTebraSchema.table('clearing_houses', {
    id: uuid('id').primaryKey().defaultRandom(),
    active: boolean('active').default(true).notNull(),
    entityID: varchar('entity_id', { length: 500 }).notNull(),
    username: varchar('username', { length: 500 }).unique().notNull(),
    clearingHouseName: varchar('clearing_house_name', { length: 500 }).notNull(),
    senderId: varchar('sender_id', { length: 500 }).default('1265825').notNull(),
    receiverId: varchar('receiver_id', { length: 500 }).notNull(),
    server: varchar('server', { length: 500 }).notNull(),
    port: integer('port').notNull(),
    password: varchar('password', { length: 500 }).notNull(),
    createdAt: timestamp('created_at').default(sql`(now() AT TIME ZONE 'America/Chicago'::text)`).notNull(),
    updatedAt: timestamp('updated_at').default(sql`(now() AT TIME ZONE 'America/Chicago'::text)`).notNull(),
});

export const clearingHouseActionTypeEnum = ediTebraSchema.enum('clearing_house_action_type', [
    'create',
    'update',
    'enable',
    'disable',
]);

export const clearingHousesAudit = ediTebraSchema.table('clearing_houses_audit', {
    id: uuid('id').primaryKey().defaultRandom(),
    clearingHouseId: uuid('clearing_house_id').notNull().references(() => clearingHouses.id),
    actionType: clearingHouseActionTypeEnum('action_type').notNull(),
    oldData: jsonb('old_data'),
    newData: jsonb('new_data'),
    changedBy: uuid('changed_by').notNull().references(() => users.id),
    changedAt: timestamp('changed_at').default(sql`(now() AT TIME ZONE 'America/Chicago'::text)`).notNull(),
    remarks: text('remarks'),
}, (table) => ({
    clearingHouseIdIdx: index('idx_clearing_houses_audit_clearing_house_id').on(table.clearingHouseId),
    changedAtIdx: index('idx_clearing_houses_audit_changed_at').on(table.changedAt),
    actionTypeIdx: index('idx_clearing_houses_audit_action_type').on(table.actionType),
}));

export const affiliationTypeEnum = ediTebraSchema.enum('affiliation_type_enum', [
    'client',
    'group',
    'practice',
]);

export const affiliationActionTypeEnum = ediTebraSchema.enum('affiliation_action_type_enum', [
    'create',
    'update',
    'enable',
    'disable',
]);

export const affiliationAuditLogs = ediTebraSchema.table('affiliation_audit_logs', {
    id: uuid('id').primaryKey().defaultRandom(),
    affiliationType: affiliationTypeEnum('affiliation_type').notNull(),
    affiliationId: integer('affiliation_id').notNull(),
    actionType: affiliationActionTypeEnum('action_type').notNull(),
    oldData: jsonb('old_data'),
    newData: jsonb('new_data'),
    changedBy: uuid('changed_by').notNull().references(() => users.id),
    changedAt: timestamp('changed_at').default(sql`(now() AT TIME ZONE 'America/Chicago'::text)`).notNull(),
    remarks: text('remarks'),
}, (table) => ({
    affiliationIdx: index('idx_affiliation_audit_logs_affiliation').on(table.affiliationType, table.affiliationId),
    actionTypeIdx: index('idx_affiliation_audit_logs_action_type').on(table.actionType),
    changedAtIdx: index('idx_affiliation_audit_logs_changed_at').on(table.changedAt),
}));

/**
 * Group table - Medical practice groups
 */
export const group = ediTebraSchema.table('group', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id').references(() => client.clientId),
    entityId: varchar('entity_id', { length: 500 }),
    grpTaxid: varchar('grp_taxid', { length: 500 }),
    grpName: varchar('grp_name', { length: 500 }),
    grpAddr1: varchar('grp_addr1', { length: 500 }),
    grpAddr2: varchar('grp_addr2', { length: 500 }),
    grpCity: varchar('grp_city', { length: 500 }),
    grpSt: varchar('grp_st', { length: 500 }),
    grpZip: varchar('grp_zip', { length: 500 }),
    grpNpi: varchar('grp_npi', { length: 500 }),
    taxonomy: varchar('taxonomy', { length: 20 }),
    pecos: varchar('pecos', { length: 20 }),
    npn: varchar('npn', { length: 20 }),
    medicaid: varchar('medicaid', { length: 20 }),
    grpPtan: varchar('ptan', { length: 50 }),
    grpClia: varchar('clia', { length: 50 }),
    grpContactLnam: varchar('grp_contact_lnam', { length: 500 }),
    grpContactFnam: varchar('grp_contact_fnam', { length: 500 }),
    grpContactEmail: varchar('grp_contact_email', { length: 500 }),
    grpContactNumber: varchar('grp_contact_number', { length: 500 }),
    isManualReviewOn: boolean('is_manual_review_on').default(true).notNull(),
    denialRiskThreshold: integer('denial_risk_threshold').default(30).notNull(), // 0-100, default 30%
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Practice table - Individual medical practices
 */
export const practice = ediTebraSchema.table('practice', {
    id: serial('id').primaryKey().notNull(),
    groupId: integer('group_id').references(() => group.id),
    clientId: integer('client_id').references(() => client.clientId),
    prctName: varchar('prct_name', { length: 500 }),
    prctBillingName: varchar('prct_billing_name', { length: 500 }),
    prctAddr1: varchar('prct_addr1', { length: 500 }),
    prctAddr2: varchar('prct_addr2', { length: 500 }),
    prctCity: varchar('prct_city', { length: 500 }),
    prctSt: varchar('prct_st', { length: 500 }),
    prctZip: varchar('prct_zip', { length: 500 }),
    prctCountry: varchar('prct_country', { length: 500 }),
    prctPhone: varchar('prct_phone', { length: 500 }),
    prctPhoneExt: varchar('prct_phone_ext', { length: 500 }),
    prctFaxPhone: varchar('prct_fax_phone', { length: 500 }),
    prctFaxPhoneExt: varchar('prct_fax_phone_ext', { length: 500 }),
    prctNpi: varchar('prct_npi', { length: 500 }),
    prctCliaNumber: varchar('prct_clia_number', { length: 500 }),
    prctPlaceOfService: varchar('prct_place_of_service', { length: 500 }),
    prctFacilityIdType: varchar('prct_facility_id_type', { length: 500 }),
    prctHcfaBox32FacilityId: varchar('prct_hcfa_box32_facility_id', { length: 500 }),
    clientName: varchar('client_name', { length: 500 }),
    prctContactLnam: varchar('prct_contact_lnam', { length: 500 }),
    prctContactFnam: varchar('prct_contact_fnam', { length: 500 }),
    prctContactEmail: varchar('prct_contact_email', { length: 500 }),
    prctContactNumber: varchar('prct_contact_number', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Service Locations table - Service facility locations mapped to practices
 */
export const serviceLocations = ediTebraSchema.table('service_locations', {
    id: serial('id').primaryKey().notNull(),
    practiceId: integer('practice_id').references(() => practice.id),
    practiceName: varchar('practice_name', { length: 500 }),
    name: varchar('name', { length: 500 }),
    billingName: varchar('billing_name', { length: 500 }),
    addressLine1: varchar('address_line1', { length: 500 }),
    addressLine2: varchar('address_line2', { length: 500 }),
    city: varchar('city', { length: 500 }),
    state: varchar('state', { length: 500 }),
    zipCode: varchar('zip_code', { length: 500 }),
    country: varchar('country', { length: 500 }),
    npi: varchar('npi', { length: 500 }),
    cliaNumber: varchar('clia_number', { length: 500 }),
    facilityIdType: varchar('facility_id_type', { length: 500 }),
    hcfaBox32FacilityId: varchar('hcfa_box32_facility_id', { length: 500 }),
    placeOfService: varchar('place_of_service', { length: 500 }),
    phone: varchar('phone', { length: 500 }),
    phoneExt: varchar('phone_ext', { length: 500 }),
    faxPhone: varchar('fax_phone', { length: 500 }),
    faxPhoneExt: varchar('fax_phone_ext', { length: 500 }),
    createdDate: varchar('created_date', { length: 500 }),
    modifiedDate: varchar('modified_date', { length: 500 }),
    isActive: varchar('is_active', { length: 500 }),
});

// ============================================================================
// PROVIDER TABLES
// ============================================================================

/**
 * Physician table - Healthcare providers
 */
export const physician = ediTebraSchema.table('physician', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id').references(() => client.clientId),
    groupId: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id').references(() => practice.id),
    phyName: varchar('phy_name', { length: 500 }),
    phyAddr1: varchar('phy_addr1', { length: 500 }),
    phyAddr2: varchar('phy_addr2', { length: 500 }),
    phyCity: varchar('phy_city', { length: 500 }),
    phySt: varchar('phy_st', { length: 500 }),
    phyZip: varchar('phy_zip', { length: 500 }),
    phyNpi: varchar('phy_npi', { length: 500 }),
    phyTaxonomy: varchar('phy_taxonomy', { length: 500 }),
    phyTaxonomyDesc: varchar('phy_taxonomy_desc', { length: 500 }),
    phyPtan: varchar('phy_ptan', { length: 500 }),
    phyPecos: varchar('phy_pecos', { length: 500 }),
    phyLic: varchar('phy_lic', { length: 500 }),
    phyOtherid1: varchar('phy_otherid1', { length: 500 }),
    phyOtherid2: varchar('phy_otherid2', { length: 500 }),
    phyType: varchar('phy_type', { length: 500 }),
    phyContactLnam: varchar('phy_contact_lnam', { length: 500 }),
    phyContactFnam: varchar('phy_contact_fnam', { length: 500 }),
    phyContactEmail: varchar('phy_contact_email', { length: 500 }),
    phyContactNumber: varchar('phy_contact_number', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

// ============================================================================
// PATIENT TABLES
// ============================================================================

/**
 * Patient Header table - Main patient demographics (composite primary key)
 */
export const patientHeader = ediTebraSchema.table(
    'patient_header',
    {
        patientHeaderId: uuid('patient_header_id').primaryKey().defaultRandom().notNull(),
        pcn: varchar('pcn', { length: 100 }),
        pcnOriginal: varchar('pcn_original', { length: 500 }),
        source_flag: varchar('source_flag', { length: 100 }),
        source: varchar('source', { length: 100 }).notNull().default('EDI'),
        sourceId: varchar('source_id', { length: 500 }).notNull(),
        patId: varchar('pat_id', { length: 500 }).notNull(),
        clientId: integer('client_id').references(() => client.clientId),
        groupId: integer('group_id').references(() => group.id),
        practiceId: integer('practice_id').references(() => practice.id),
        subLnam: varchar('sub_lnam', { length: 500 }).notNull(),
        patFnam: varchar('pat_fnam', { length: 500 }).notNull(),
        middleName: varchar('middle_name', { length: 500 }),
        prefix: varchar('prefix', { length: 50 }),
        suffix: varchar('suffix', { length: 50 }),
        patGender: varchar('pat_gender', { length: 500 }),
        patDob: varchar('pat_dob', { length: 500 }).notNull(),
        active: boolean('active'),
        patientFullName: varchar('patient_full_name', { length: 500 }),
        age: varchar('age', { length: 10 }),
        ssn: text('ssn'),
        maritalStatus: text('marital_status'),
        medicalRecordNumber: varchar('medical_record_number', { length: 500 }),
        patEmail: varchar('pat_email', { length: 500 }),
        patContact: varchar('pat_contact', { length: 500 }),
        workPhone: varchar('work_phone', { length: 500 }),
        workPhoneExt: varchar('work_phone_ext', { length: 50 }),
        mobilePhone: varchar('mobile_phone', { length: 500 }),
        mobilePhoneExt: varchar('mobile_phone_ext', { length: 50 }),
        homePhone: varchar('home_phone', { length: 500 }),
        homePhoneExt: varchar('home_phone_ext', { length: 50 }),
        patContactConsent: boolean('pat_contact_consent'),
        patContactMethod: varchar('pat_contact_method', { length: 500 }),
        emergencyName: varchar('emergency_name', { length: 500 }),
        emergencyPhone: varchar('emergency_phone', { length: 500 }),
        emergencyPhoneExt: varchar('emergency_phone_ext', { length: 50 }),
        patAddr1: varchar('pat_addr1', { length: 500 }),
        patAddr2: varchar('pat_addr2', { length: 500 }),
        patCity: varchar('pat_city', { length: 500 }),
        patSt: varchar('pat_st', { length: 500 }),
        patZip: varchar('pat_zip', { length: 500 }),
        country: varchar('country', { length: 500 }),
        employerName: varchar('employer_name', { length: 500 }),
        employmentStatus: varchar('employment_status', { length: 100 }),
        primaryCarePhysicianId: text('primary_care_physician_id'),
        primaryCarePhysicianFullName: varchar('primary_care_physician_full_name', { length: 500 }),
        referringProviderId: text('referring_provider_id'),
        referringProviderFullName: varchar('referring_provider_full_name', { length: 500 }),
        referralSource: varchar('referral_source', { length: 500 }),
        collectionCategoryName: varchar('collection_category_name', { length: 500 }),
        totalBalance: numeric('total_balance', { precision: 18, scale: 2 }),
        patientBalance: numeric('patient_balance', { precision: 18, scale: 2 }),
        insuranceBalance: numeric('insurance_balance', { precision: 18, scale: 2 }),
        alertMessage: text('alert_message'),
        alertShowPatientDetails: boolean('alert_show_patient_details'),
        alertShowEncounters: boolean('alert_show_encounters'),
        alertShowPayments: boolean('alert_show_payments'),
        alertShowStatements: boolean('alert_show_statements'),
        alertShowAppointments: boolean('alert_show_appointments'),
        alertShowClaims: boolean('alert_show_claims'),
        lastAppointmentDate: text('last_appointment_date'),
        lastEncounterDate: text('last_encounter_date'),
        lastPaymentDate: text('last_payment_date'),
        lastStatementDate: text('last_statement_date'),
        defaultCaseId: text('default_case_id'),
        defaultCaseName: varchar('default_case_name', { length: 500 }),
        defaultCaseDescription: text('default_case_description'),
        defaultServiceLocationId: text('default_service_location_id'),
        defaultServiceLocationName: varchar('default_service_location_name', { length: 500 }),
        defaultRenderingProviderId: text('default_rendering_provider_id'),
        defaultRenderingProviderName: varchar('default_rendering_provider_name', { length: 500 }),
        isSelfPay: boolean('is_self_pay').default(false).notNull(),
        createdAt: timestamp('created_at').defaultNow().notNull(),
        updatedAt: timestamp('updated_at').defaultNow().notNull(),
    },
);

/**
 * Patient Guarantor table - Patient guarantor/responsible party information
 */
export const patientGuarantor = ediTebraSchema.table('patient_guarantor', {
    id: serial('id').primaryKey().notNull(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
    source: varchar('source', { length: 100 }).notNull().default('EDI'),
    source_flag: varchar('source_flag', { length: 100 }),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    patId: varchar('pat_id', { length: 500 }),
    patSource: varchar('pat_source', { length: 100 }).notNull(),
    patSubLnam: varchar('pat_sub_lnam', { length: 500 }).notNull(),
    patFnam: varchar('pat_fnam', { length: 500 }).notNull(),
    patDob: varchar('pat_dob', { length: 500 }).notNull(),
    gurRel: varchar('gur_rel', { length: 500 }),
    gurLnam: varchar('gur_lnam', { length: 500 }),
    gurFnam: varchar('gur_fnam', { length: 500 }),
    guarantorMiddleName: varchar('guarantor_middle_name', { length: 500 }),
    guarantorPrefix: varchar('guarantor_prefix', { length: 50 }),
    guarantorSuffix: varchar('guarantor_suffix', { length: 50 }),
    guarantorDifferentThanPatient: boolean('guarantor_different_than_patient'),
    gurAddr1: varchar('gur_addr1', { length: 500 }),
    gurAddr2: varchar('gur_addr2', { length: 500 }),
    gurCity: varchar('gur_city', { length: 500 }),
    gurSt: varchar('gur_st', { length: 500 }),
    gurZip: varchar('gur_zip', { length: 500 }),
    gurEmail: varchar('gur_email', { length: 500 }),
    gurContact: varchar('gur_contact', { length: 500 }),
    gurContactConsent: boolean('gur_contact_consent'),
    gurContactMethod: varchar('gur_contact_method', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Patient Notes table - Clinical and administrative notes
 */
export const patientNotes = ediTebraSchema.table('patient_notes', {
    id: serial('id').primaryKey().notNull(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
    source: varchar('source', { length: 100 }).notNull().default('EDI'),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    patId: varchar('pat_id', { length: 500 }),
    patSource: varchar('pat_source', { length: 100 }).notNull(),
    patSubLnam: varchar('pat_sub_lnam', { length: 500 }).notNull(),
    patFnam: varchar('pat_fnam', { length: 500 }).notNull(),
    patDob: varchar('pat_dob', { length: 500 }).notNull(),
    noteType: varchar('note_type', { length: 500 }),
    noteText: text('note_text'),
    noteDatetime: timestamp('note_datetime'),
    noteLogin: varchar('note_login', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Patient Coverages table - Insurance coverage information
 * Supports PMI (Patient Master Index) requirements:
 * - P/S/T coverage types via covType (Primary, Secondary, Tertiary)
 * - Carrier lookup via covCarId (Change Healthcare payer ID standard)
 * - Business rule: No overlapping coverage dates for same coverage type
 * - Relationship-based conditional logic via covRel
 * 
 * Field Mapping for PMI:
 * - Coverage Type: covType (P/S/T)
 * - Carrier Type: covCarType (Medicare/Commercial/Medicaid)
 * - Carrier ID: covCarId (Change Healthcare payer ID)
 * - Carrier Name: covCarNam (Insurance company name)
 * - Subscriber ID: covSubId (null if Self)
 * - Relationship: covRel (Self/Spouse/Child/Other)
 * - Subscriber Name: insuredFullName (Required if relationship != Self)
 * - Dependent ID: covDepId (Patient's member ID)
 * - Dependent Name: patFnam + patSubLnam (Patient name on card)
 * - Start Date: covStartDate (forced to 1st of month, blank = open)
 * - End Date: covEndDate (forced to last day of month, blank = current)
 * - Active Status: covStatus
 */
export const patientCoverages = ediTebraSchema.table('patient_coverages', {
    id: serial('id').primaryKey().notNull(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
    active: boolean('active').default(true).notNull(),
    source: varchar('source', { length: 100 }).notNull().default('EDI'),
    source_flag: varchar('source_flag', { length: 100 }),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    patId: varchar('pat_id', { length: 500 }),
    patSource: varchar('pat_source', { length: 100 }).notNull(),
    patSubLnam: varchar('pat_sub_lnam', { length: 500 }).notNull(),
    patFnam: varchar('pat_fnam', { length: 500 }).notNull(),
    patDob: varchar('pat_dob', { length: 500 }).notNull(),

    // PMI Core Fields (using existing columns)
    covStatus: varchar('cov_status', { length: 500 }), // Active/Inactive
    covType: varchar('cov_type', { length: 500 }), // P, S, T (Primary, Secondary, Tertiary)
    covCarId: varchar('cov_car_id', { length: 500 }), // Change Healthcare payer ID
    covCarNam: varchar('cov_car_nam', { length: 500 }), // Insurance company name
    covCarType: varchar('cov_car_type', { length: 500 }), // Medicare, Commercial, Medicaid
    covRel: varchar('cov_rel', { length: 500 }), // Self, Spouse, Child, Other (837 spec)
    covSubId: varchar('cov_sub_id', { length: 500 }), // Insurance subscriber number (null if self)
    covDepId: varchar('cov_dep_id', { length: 500 }), // Patient's member ID
    covDepName: varchar('cov_dep_name', { length: 500 }),
    covStartDate: varchar('cov_start_date', { length: 500 }), // YYYY-MM-DD, forced to 1st of month
    covEndDate: varchar('cov_end_date', { length: 500 }), // YYYY-MM-DD, forced to last day of month
    insuranceType: varchar('insurance_type', { length: 50 }),
    companyId: text('company_id'),
    companyName: varchar('company_name', { length: 500 }),
    policyNumber: varchar('policy_number', { length: 500 }),
    groupNumber: varchar('group_number', { length: 500 }),
    copay: numeric('copay', { precision: 18, scale: 4 }),
    deductible: numeric('deductible', { precision: 18, scale: 4 }),
    effectiveStartDate: text('effective_start_date'),
    effectiveEndDate: text('effective_end_date'),
    patientRelationshipToInsured: varchar('patient_relationship_to_insured', { length: 10 }),
    insuredFullName: varchar('insured_full_name', { length: 500 }),
    insuredIdNumber: varchar('insured_id_number', { length: 500 }),
    insuredSsn: varchar('insured_ssn', { length: 50 }),
    insuredDob: text('insured_dob'),
    insuredGender: varchar('insured_gender', { length: 10 }),
    insuredAddress1: varchar('insured_address1', { length: 500 }),
    insuredAddress2: varchar('insured_address2', { length: 500 }),
    insuredCity: varchar('insured_city', { length: 500 }),
    insuredState: varchar('insured_state', { length: 500 }),
    insuredZip: varchar('insured_zip', { length: 50 }),
    insuredCountry: varchar('insured_country', { length: 500 }),
    insuredNotes: text('insured_notes'),
    planId: text('plan_id'),
    planName: varchar('plan_name', { length: 500 }),
    planAddress1: varchar('plan_address1', { length: 500 }),
    planAddress2: varchar('plan_address2', { length: 500 }),
    planCity: varchar('plan_city', { length: 500 }),
    planState: varchar('plan_state', { length: 500 }),
    planZip: varchar('plan_zip', { length: 50 }),
    planCountry: varchar('plan_country', { length: 500 }),
    planPhone: varchar('plan_phone', { length: 500 }),
    planPhoneExt: varchar('plan_phone_ext', { length: 50 }),
    planFax: varchar('plan_fax', { length: 500 }),
    planFaxExt: varchar('plan_fax_ext', { length: 50 }),
    planAdjusterName: varchar('plan_adjuster_name', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

export const patientVisits = ediTebraSchema.table('patient_visits', {
    id: uuid('id').primaryKey().defaultRandom().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
    apptDate: date('appt_date', { mode: 'string' }),
    apptTime: time('appt_time'),
    provider: varchar('provider', { length: 500 }),
    source: varchar('source', { length: 500 }).notNull().default('tebra'),
    claimId: uuid('claim_id'),
    visitDiagnosis: jsonb('visit_diagnosis').$type<Array<{ code: string; description?: string | null }>>(),
    otherDiagnosis: jsonb('other_diagnosis').$type<Array<{ code: string; description?: string | null }>>(),
    // Links back to the medical_extraction_files row this visit was
    // produced from, for the pf_facesheet_processing flow.
    medicalExtractionFileId: integer('medical_extraction_file_id').references(() => medicalExtractionFiles.id),
    // Flags rows inserted by seedDemoPatientVisitsDummyData.ts for testing
    // chargeSheetVisitDiagnosisResolver.ts - never set by real ingestion.
    dummyData: boolean('dummy_data').notNull().default(false),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

// ============================================================================
// CLAIMS (837) TABLES
// ============================================================================

/**
 * Claim Header table - Main claim information (263 fields)
 */
export const claimHeader = ediTebraSchema.table('claim_header', {
    id: serial('id').notNull(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
    dcn: varchar('dcn', { length: 100 }),
    clientId: integer('client_id').references(() => client.clientId),
    groupId: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id').references(() => practice.id),
    status: varchar('status', { length: 500 }),
    // Retired - what this used to encode (the Exceptions queue's
    // missing-diagnosis-only sub-classification, formerly status='E' +
    // subQueue='Pending') is now its own status value, 'NF' (No Facesheet -
    // see STATUS_NO_FACESHEET in constants/claimStatuses.ts and
    // constants/exceptionSubQueueRules.ts for the classification rule). No
    // longer written to by any live code path; left in the schema
    // unchanged rather than dropped. See
    // server/migrations/00126_migrate_pending_subqueue_to_nf_status.sql.
    subQueue: varchar('sub_queue', { length: 50 }),
    dateOfService: date('date_of_service'),
    claimStatus: varchar('claim_status', { length: 500 }),
    transactionId: varchar('transaction_id', { length: 500 }),
    claimId: uuid('claim_id').primaryKey().notNull(),
    dataSource: text('data_source'),
    isaIdQual: varchar('isa_id_qual', { length: 500 }),
    isaSenderId: varchar('isa_sender_id', { length: 500 }),
    isaReceiverIdQual: varchar('isa_receiver_id_qual', { length: 500 }),
    isaReceiverId: varchar('isa_receiver_id', { length: 500 }),
    isaDate: varchar('isa_date', { length: 500 }),
    isaTime: varchar('isa_time', { length: 500 }),
    isaVersion: varchar('isa_version', { length: 500 }),
    isaCtrlNum: varchar('isa_ctrl_num', { length: 500 }),
    isaUsageInd: varchar('isa_usage_ind', { length: 500 }),
    gsSenderCode: varchar('gs_sender_code', { length: 500 }),
    gsReceiverCode: varchar('gs_receiver_code', { length: 500 }),
    gsDate: varchar('gs_date', { length: 500 }),
    gsTime: varchar('gs_time', { length: 500 }),
    gsCtrlNum: varchar('gs_ctrl_num', { length: 500 }),
    gsUsageInd: varchar('gs_usage_ind', { length: 500 }),
    gsVersion: varchar('gs_version', { length: 500 }),
    sbmLnam: varchar('sbm_lnam', { length: 500 }),
    sbmFnam: varchar('sbm_fnam', { length: 500 }),
    sbmId: varchar('sbm_id', { length: 500 }),
    recLnam: varchar('rec_lnam', { length: 500 }),
    recFnam: varchar('rec_fnam', { length: 500 }),
    recId: varchar('rec_id', { length: 500 }),
    bpTaxonomy: varchar('bp_taxonomy', { length: 500 }),
    bpTaxonomyCd: varchar('bp_taxonomy_cd', { length: 500 }),
    bpLnam: varchar('bp_lnam', { length: 500 }),
    bpFnam: varchar('bp_fnam', { length: 500 }),
    bpId: varchar('bp_id', { length: 500 }),
    bpAddress1: varchar('bp_address1', { length: 500 }),
    bpAddress2: varchar('bp_address2', { length: 500 }),
    bpCity: varchar('bp_city', { length: 500 }),
    bpSt: varchar('bp_st', { length: 500 }),
    bpZip: varchar('bp_zip', { length: 500 }),
    bpRefQ1: varchar('bp_ref_q1', { length: 500 }),
    bpRefId1: varchar('bp_ref_id1', { length: 500 }),
    bpRefQ2: varchar('bp_ref_q2', { length: 500 }),
    bpRefId2: varchar('bp_ref_id2', { length: 500 }),
    ptName: varchar('pt_name', { length: 500 }),
    ptAddress1: varchar('pt_address1', { length: 500 }),
    ptAddress2: varchar('pt_address2', { length: 500 }),
    ptCity: varchar('pt_city', { length: 500 }),
    ptSt: varchar('pt_st', { length: 500 }),
    ptZip: varchar('pt_zip', { length: 500 }),
    ptpName: varchar('ptp_name', { length: 500 }),
    ptpAddress1: varchar('ptp_address1', { length: 500 }),
    ptpAddress2: varchar('ptp_address2', { length: 500 }),
    ptpCity: varchar('ptp_city', { length: 500 }),
    ptpState: varchar('ptp_state', { length: 500 }),
    ptpZip: varchar('ptp_zip', { length: 500 }),
    ptpSecid: varchar('ptp_secid', { length: 500 }),
    ptpTaxid: varchar('ptp_taxid', { length: 500 }),
    ptpRefQ1: varchar('ptp_ref_q1', { length: 500 }),
    ptpRefId1: varchar('ptp_ref_id1', { length: 500 }),
    ptpRefQ2: varchar('ptp_ref_q2', { length: 500 }),
    ptpRefId2: varchar('ptp_ref_id2', { length: 500 }),
    subRelationship: varchar('sub_relationship', { length: 500 }),
    subInstype: varchar('sub_instype', { length: 500 }),
    subIndicator: varchar('sub_indicator', { length: 500 }),
    policyGroupNumber: varchar('policy_group_number', { length: 500 }),
    subLnam: varchar('sub_lnam', { length: 500 }),
    subFnam: varchar('sub_fnam', { length: 500 }),
    subMnam: varchar('sub_mnam', { length: 500 }),
    subQual: varchar('sub_qual', { length: 500 }),
    subId: varchar('sub_id', { length: 500 }),
    subAddress1: varchar('sub_address1', { length: 500 }),
    subAddress2: varchar('sub_address2', { length: 500 }),
    subCity: varchar('sub_city', { length: 500 }),
    subState: varchar('sub_state', { length: 500 }),
    subZip: varchar('sub_zip', { length: 500 }),
    subDob: varchar('sub_dob', { length: 500 }),
    subGender: varchar('sub_gender', { length: 500 }),
    subRefQ1: varchar('sub_ref_q1', { length: 500 }),
    subRefId1: varchar('sub_ref_id1', { length: 500 }),
    subRefQ2: varchar('sub_ref_q2', { length: 500 }),
    subRefId2: varchar('sub_ref_id2', { length: 500 }),
    pryLnam: varchar('pry_lnam', { length: 500 }),
    pryFnam: varchar('pry_fnam', { length: 500 }),
    pryId: varchar('pry_id', { length: 500 }),
    pryAddress1: varchar('pry_address1', { length: 500 }),
    pryAddress2: varchar('pry_address2', { length: 500 }),
    pryCity: varchar('pry_city', { length: 500 }),
    prySt: varchar('pry_st', { length: 500 }),
    pryZip: varchar('pry_zip', { length: 500 }),
    pryRefq1: varchar('pry_refq1', { length: 500 }),
    pryRefid1: varchar('pry_refid1', { length: 500 }),
    pryRefq2: varchar('pry_refq2', { length: 500 }),
    pryRefid2: varchar('pry_refid2', { length: 500 }),
    pryRefq3: varchar('pry_refq3', { length: 500 }),
    pryRefid3: varchar('pry_refid3', { length: 500 }),
    pryRefq4: varchar('pry_refq4', { length: 500 }),
    pryRefid4: varchar('pry_refid4', { length: 500 }),
    pryRefq5: varchar('pry_refq5', { length: 500 }),
    pryRefid5: varchar('pry_refid5', { length: 500 }),
    patRelcd: varchar('pat_relcd', { length: 500 }),
    patLnam: varchar('pat_lnam', { length: 500 }),
    patFnam: varchar('pat_fnam', { length: 500 }),
    patAddr1: varchar('pat_addr1', { length: 500 }),
    patAddr2: varchar('pat_addr2', { length: 500 }),
    patCity: varchar('pat_city', { length: 500 }),
    patSt: varchar('pat_st', { length: 500 }),
    patZip: varchar('pat_zip', { length: 500 }),
    patDob: varchar('pat_dob', { length: 500 }),
    patGender: varchar('pat_gender', { length: 500 }),
    clmPatctrlnum: varchar('clm_patctrlnum', { length: 500 }),
    clmTotalamt: numeric('clm_totalamt', { precision: 18, scale: 2 }),
    clmAssignment: varchar('clm_assignment', { length: 500 }),
    clmResponseCd: varchar('clm_response_cd', { length: 500 }),
    clmConsent: varchar('clm_consent', { length: 500 }),
    pwkTypecd: varchar('pwk_typecd', { length: 500 }),
    pwkTrnscd: varchar('pwk_trnscd', { length: 500 }),
    pwkIdcd: varchar('pwk_idcd', { length: 500 }),
    patPaidamt: numeric('pat_paidamt', { precision: 18, scale: 2 }),
    refAuthid: varchar('ref_authid', { length: 500 }),
    refRefernum: varchar('ref_refernum', { length: 500 }),
    refPriorauth: varchar('ref_priorauth', { length: 500 }),
    refClmCtrlnum: varchar('ref_clm_ctrlnum', { length: 500 }),
    refClianum: varchar('ref_clianum', { length: 500 }),
    refRepricenum: varchar('ref_repricenum', { length: 500 }),
    refAdjrepricenum: varchar('ref_adjrepricenum', { length: 500 }),
    refClmnum: varchar('ref_clmnum', { length: 500 }),
    refMrnum: varchar('ref_mrnum', { length: 500 }),
    refCareplannum: varchar('ref_careplannum', { length: 500 }),
    refK3filenum: varchar('ref_k3filenum', { length: 500 }),
    nteClmnote: text('nte_clmnote'),
    diagQcd: varchar('diag_qcd', { length: 500 }),
    diag1: varchar('diag1', { length: 500 }),
    diag2: varchar('diag2', { length: 500 }),
    diag3: varchar('diag3', { length: 500 }),
    diag4: varchar('diag4', { length: 500 }),
    diag5: varchar('diag5', { length: 500 }),
    diag6: varchar('diag6', { length: 500 }),
    diag7: varchar('diag7', { length: 500 }),
    diag8: varchar('diag8', { length: 500 }),
    diag9: varchar('diag9', { length: 500 }),
    diag10: varchar('diag10', { length: 500 }),
    diag11: varchar('diag11', { length: 500 }),
    diag12: varchar('diag12', { length: 500 }),
    rprvEntcd: varchar('rprv_entcd', { length: 500 }),
    rprvLnam: varchar('rprv_lnam', { length: 500 }),
    rprvFnam: varchar('rprv_fnam', { length: 500 }),
    rprvId: varchar('rprv_id', { length: 500 }),
    rprvRef1: varchar('rprv_ref1', { length: 500 }),
    rendprvEntcd: varchar('rendprv_entcd', { length: 500 }),
    rendprvLnam: varchar('rendprv_lnam', { length: 500 }),
    rendprvFnam: varchar('rendprv_fnam', { length: 500 }),
    rendprvCd: varchar('rendprv_cd', { length: 500 }),
    rendprvSpecd: varchar('rendprv_specd', { length: 500 }),
    rendprvRef1q: varchar('rendprv_ref1q', { length: 500 }),
    rendprvRef1id: varchar('rendprv_ref1id', { length: 500 }),
    srvfacLnam: varchar('srvfac_lnam', { length: 500 }),
    srvfacFnam: varchar('srvfac_fnam', { length: 500 }),
    srvfacIdq: varchar('srvfac_idq', { length: 500 }),
    srvfacCd: varchar('srvfac_cd', { length: 500 }),
    srvfacAddress1: varchar('srvfac_address1', { length: 500 }),
    srvfacAddress2: varchar('srvfac_address2', { length: 500 }),
    srvfacCity: varchar('srvfac_city', { length: 500 }),
    srvfacSt: varchar('srvfac_st', { length: 500 }),
    srvfacZip: varchar('srvfac_zip', { length: 500 }),
    srvfacRef1q: varchar('srvfac_ref1q', { length: 500 }),
    srvfacRef1id: varchar('srvfac_ref1id', { length: 500 }),
    supprvFnam: varchar('supprv_fnam', { length: 500 }),
    suprvLnam: varchar('suprv_lnam', { length: 500 }),
    supprvId: varchar('supprv_id', { length: 500 }),
    supprvRefq1: varchar('supprv_refq1', { length: 500 }),
    supprvRefid1: varchar('supprv_refid1', { length: 500 }),
    supprvRefq2: varchar('supprv_refq2', { length: 500 }),
    supprvRefid2: varchar('supprv_refid2', { length: 500 }),
    supprvRefq3: varchar('supprv_refq3', { length: 500 }),
    supprvRefid3: varchar('supprv_refid3', { length: 500 }),
    supprvRefq4: varchar('supprv_refq4', { length: 500 }),
    supprvRefid4: varchar('supprv_refid4', { length: 500 }),
    encounterId: varchar('encounter_id', { length: 500 }),
    encounterStatus: varchar('encounter_status', { length: 500 }),
    appointmentId: varchar('appointment_id', { length: 500 }),
    rpaAppointmentId: varchar('rpa_appointment_id', { length: 500 }),
    caseName: varchar('case_name', { length: 500 }),
    casePayerScenario: varchar('case_payer_scenario', { length: 500 }),
    hospitalizationStartDate: varchar('hospitalization_start_date', { length: 500 }),
    hospitalizationEndDate: varchar('hospitalization_end_date', { length: 500 }),
    currentIllnessDate: varchar('current_illness_date', { length: 500 }),
    currentIllnessDateQualifier: varchar('current_illness_date_qualifier', { length: 500 }),
    otherDate: varchar('other_date', { length: 500 }),
    otherDateQualifier: varchar('other_date_qualifier', { length: 500 }),
    dateUnableWorkFrom: varchar('date_unable_work_from', { length: 500 }),
    dateUnableWorkTo: varchar('date_unable_work_to', { length: 500 }),
    doNotSendClaimElectronically: boolean('do_not_send_claim_electronically'),
    doNotSendElectronicallyToSecondary: boolean('do_not_send_electronically_to_secondary'),
    localUseBox10d: varchar('local_use_box10d', { length: 500 }),
    localUseBox19: varchar('local_use_box19', { length: 500 }),
    patientFirstBillDate: varchar('patient_first_bill_date', { length: 500 }),
    patientLastBillDate: varchar('patient_last_bill_date', { length: 500 }),
    postingDate: varchar('posting_date', { length: 500 }),
    practiceName: varchar('practice_name', { length: 500 }),
    patMiddleName: varchar('pat_middle_name', { length: 500 }),
    primaryInsFirstBillDate: varchar('primary_ins_first_bill_date', { length: 500 }),
    primaryInsLastBillDate: varchar('primary_ins_last_bill_date', { length: 500 }),
    primaryInsAdjudicationDate: varchar('primary_ins_adjudication_date', { length: 500 }),
    primaryInsBatchId: varchar('primary_ins_batch_id', { length: 500 }),
    primaryInsPaymentId: varchar('primary_ins_payment_id', { length: 500 }),
    primaryInsPaymentMethodDesc: varchar('primary_ins_payment_method_desc', { length: 500 }),
    primaryInsPaymentPostingDate: varchar('primary_ins_payment_posting_date', { length: 500 }),
    primaryInsPaymentRef: varchar('primary_ins_payment_ref', { length: 500 }),
    primaryInsPaymentCategoryDesc: varchar('primary_ins_payment_category_desc', { length: 500 }),
    secondaryInsFirstBillDate: varchar('secondary_ins_first_bill_date', { length: 500 }),
    secondaryInsLastBillDate: varchar('secondary_ins_last_bill_date', { length: 500 }),
    secondaryInsAdjudicationDate: varchar('secondary_ins_adjudication_date', { length: 500 }),
    secondaryInsBatchId: varchar('secondary_ins_batch_id', { length: 500 }),
    secondaryInsPaymentId: varchar('secondary_ins_payment_id', { length: 500 }),
    secondaryInsPaymentMethodDesc: varchar('secondary_ins_payment_method_desc', { length: 500 }),
    secondaryInsPaymentPostingDate: varchar('secondary_ins_payment_posting_date', { length: 500 }),
    secondaryInsPaymentRef: varchar('secondary_ins_payment_ref', { length: 500 }),
    secondaryInsPaymentCategoryDesc: varchar('secondary_ins_payment_category_desc', { length: 500 }),
    tertiaryInsAdjudicationDate: varchar('tertiary_ins_adjudication_date', { length: 500 }),
    tertiaryInsBatchId: varchar('tertiary_ins_batch_id', { length: 500 }),
    tertiaryInsPaymentId: varchar('tertiary_ins_payment_id', { length: 500 }),
    tertiaryInsPaymentMethodDesc: varchar('tertiary_ins_payment_method_desc', { length: 500 }),
    tertiaryInsPaymentPostingDate: varchar('tertiary_ins_payment_posting_date', { length: 500 }),
    tertiaryInsPaymentRef: varchar('tertiary_ins_payment_ref', { length: 500 }),
    tertiaryInsPaymentCategoryDesc: varchar('tertiary_ins_payment_category_desc', { length: 500 }),
    typeOfService: varchar('type_of_service', { length: 500 }),
    conditionRelatedToEmployment: varchar('condition_related_to_employment', { length: 1 }),
    conditionRelatedToAutoAccident: varchar('condition_related_to_auto_accident', { length: 1 }),
    conditionRelatedToOtherAccident: varchar('condition_related_to_other_accident', { length: 1 }),
    accidentState: varchar('accident_state', { length: 2 }),
    resubmissionCode: varchar('resubmission_code', { length: 2 }),
    clmFrequencyCode: varchar('clm_frequency_code', { length: 2 }).default('1'),
    schedprvId: varchar('schedprv_id', { length: 500 }),
    schedprvName: varchar('schedprv_name', { length: 500 }),
    primaryInsCompanyName: varchar('primary_ins_company_name', { length: 500 }),
    primaryInsPlanName: varchar('primary_ins_plan_name', { length: 500 }),
    primaryInsAddressLine1: varchar('primary_ins_address_line1', { length: 500 }),
    primaryInsAddressLine2: varchar('primary_ins_address_line2', { length: 500 }),
    primaryInsCity: varchar('primary_ins_city', { length: 500 }),
    primaryInsState: varchar('primary_ins_state', { length: 500 }),
    primaryInsZipCode: varchar('primary_ins_zip_code', { length: 500 }),
    primaryInsCountry: varchar('primary_ins_country', { length: 500 }),
    secondaryInsCompanyName: varchar('secondary_ins_company_name', { length: 500 }),
    secondaryInsPlanName: varchar('secondary_ins_plan_name', { length: 500 }),
    secondaryInsAddressLine1: varchar('secondary_ins_address_line1', { length: 500 }),
    secondaryInsAddressLine2: varchar('secondary_ins_address_line2', { length: 500 }),
    secondaryInsCity: varchar('secondary_ins_city', { length: 500 }),
    secondaryInsState: varchar('secondary_ins_state', { length: 500 }),
    secondaryInsZipCode: varchar('secondary_ins_zip_code', { length: 500 }),
    secondaryInsCountry: varchar('secondary_ins_country', { length: 500 }),
    tertiaryInsCompanyName: varchar('tertiary_ins_company_name', { length: 500 }),
    tertiaryInsCompanyPlanName: varchar('tertiary_ins_company_plan_name', { length: 500 }),
    tertiaryInsAddressLine1: varchar('tertiary_ins_address_line1', { length: 500 }),
    tertiaryInsAddressLine2: varchar('tertiary_ins_address_line2', { length: 500 }),
    tertiaryInsCity: varchar('tertiary_ins_city', { length: 500 }),
    tertiaryInsState: varchar('tertiary_ins_state', { length: 500 }),
    tertiaryInsZipCode: varchar('tertiary_ins_zip_code', { length: 500 }),
    tertiaryInsCountry: varchar('tertiary_ins_country', { length: 500 }),
    serviceLocationBillingName: varchar('service_location_billing_name', { length: 500 }),
    serviceLocationCliaNumber: varchar('service_location_clia_number', { length: 500 }),
    serviceLocationFacilityId: varchar('service_location_facility_id', { length: 500 }),
    serviceLocationFacilityIdType: varchar('service_location_facility_id_type', { length: 500 }),
    serviceLocationFax: varchar('service_location_fax', { length: 500 }),
    serviceLocationFaxExt: varchar('service_location_fax_ext', { length: 500 }),
    serviceLocationNpi: varchar('service_location_npi', { length: 500 }),
    serviceLocationPhone: varchar('service_location_phone', { length: 500 }),
    serviceLocationPhoneExt: varchar('service_location_phone_ext', { length: 500 }),
    serviceLocationPlaceOfServiceName: varchar('service_location_place_of_service_name', { length: 500 }),
    patientName: varchar('patient_name', { length: 500 }),
    refCode: varchar('ref_code', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
    finishStatus: boolean('finish_status').default(false),
    claimType: varchar('claim_type', { length: 50 }),
    source: varchar('source', { length: 100 }).default('EDI').notNull(),
    extractedDate: timestamp('extracted_date'),
    writeOffAt: text("write_off_at"),
    writeOffAmount: varchar('write_off_amount', { length: 50 }),
    processedDate: timestamp("processed_date", { withTimezone: true, mode: 'string' }),
    queue: jsonb('queue').default({}),
    patient_source_id: varchar('patient_source_id', { length: 255 }),
    submittedCount: varchar('submitted_count', { length: 500 }).default('O'),
    resubmissionFlag: boolean('resubmission_flag').default(false),
    posted: boolean('posted').default(false),
    mappedTo: boolean('mapped_to').default(true),
    denialRiskScore: numeric('denial_risk_score', {precision: 5,scale: 4,}),
    riskLevel: riskClassificationEnum('risk_level'),
    extractedBatchNumber: varchar('extracted_batch_number', { length: 500 }),
    createdBy: uuid('created_by').references(() => users.id),
});

/**
 * Claim Details table - Service line items
 */
export const claimDetails = ediTebraSchema.table('claim_details', {
    id: serial('id').primaryKey().notNull(),
    transactionId: varchar('transaction_id', { length: 500 }),
    svcLineNum: integer('svc_line_num'),
    dosFrom: varchar('dos_from', { length: 500 }),
    dosTo: varchar('dos_to', { length: 500 }),
    locCd: varchar('loc_cd', { length: 500 }),
    emgInd: varchar('emg_ind', { length: 1 }),
    revCode: varchar('rev_code', { length: 10 }), // Revenue code for institutional claims
    outsideLabIndicator: varchar('outside_lab_indicator', { length: 1 }),
    outsideLabCharges: numeric('outside_lab_charges', { precision: 18, scale: 2 }),
    svcMeasurementcd: varchar('svc_measurementcd', { length: 500 }),
    units: numeric('units', { precision: 15, scale: 3 }),
    procCd: varchar('proc_cd', { length: 500 }),
    mod1: varchar('mod_1', { length: 500 }),
    mod2: varchar('mod_2', { length: 500 }),
    mod3: varchar('mod_3', { length: 500 }),
    mod4: varchar('mod_4', { length: 500 }),
    diag1: varchar('diag_1', { length: 500 }),
    diag2: varchar('diag_2', { length: 500 }),
    diag3: varchar('diag_3', { length: 500 }),
    diag4: varchar('diag_4', { length: 500 }),
    srvrendPrvNpi: varchar('srvrend_prv_npi', { length: 500 }),
    chargeAmount: numeric('charge_amount', { precision: 18, scale: 2 }),
    claimHeaderId: uuid('claim_header_id').references(() => claimHeader.claimId),
    claimSource: varchar('claim_source', { length: 100 }),
    claimClientId: integer('claim_client_id'),
    claimPracticeId: integer('claim_practice_id'),
    claimPatCtrlNum: varchar('claim_pat_ctrl_num', { length: 500 }),
    claimPatDob: varchar('claim_pat_dob', { length: 500 }),
    encounterProcedureId: varchar('encounter_procedure_id', { length: 500 }),
    procedureName: varchar('procedure_name', { length: 500 }),
    procedureCodeCategory: varchar('procedure_code_category', { length: 500 }),
    minutes: varchar('minutes', { length: 50 }),
    unitCharge: numeric('unit_charge', { precision: 18, scale: 4 }),
    adjustedCharges: numeric('adjusted_charges', { precision: 18, scale: 4 }),
    allowedAmount: numeric('allowed_amount', { precision: 18, scale: 4 }),
    totalCharges: numeric('total_charges', { precision: 18, scale: 4 }),
    billedTo: varchar('billed_to', { length: 500 }),
    status: varchar('status', { length: 500 }),
    refCode: varchar('ref_code', { length: 500 }),
    lineNote: text('line_note'),
    receipts: numeric('receipts', { precision: 18, scale: 4 }),
    totalBalance: numeric('total_balance', { precision: 18, scale: 4 }),
    patientBalance: numeric('patient_balance', { precision: 18, scale: 4 }),
    insuranceBalance: numeric('insurance_balance', { precision: 18, scale: 4 }),
    otherAdjustment: numeric('other_adjustment', { precision: 18, scale: 4 }),
    expectedAmount: numeric('expected_amount', { precision: 18, scale: 4 }),
    copayAmount: numeric('copay_amount', { precision: 18, scale: 4 }),
    copayCategory: varchar('copay_category', { length: 500 }),
    copayMethod: varchar('copay_method', { length: 500 }),
    copayReference: varchar('copay_reference', { length: 500 }),
    batchNumber: varchar('batch_number', { length: 500 }),
    patientBatchId: varchar('patient_batch_id', { length: 500 }),
    patientPaymentId: varchar('patient_payment_id', { length: 500 }),
    patientPaymentAmount: numeric('patient_payment_amount', { precision: 18, scale: 4 }),
    patientPaymentMethodDesc: varchar('patient_payment_method_desc', { length: 500 }),
    patientPaymentPostingDate: varchar('patient_payment_posting_date', { length: 500 }),
    patientPaymentRef: varchar('patient_payment_ref', { length: 500 }),
    patientPaymentCategoryDesc: varchar('patient_payment_category_desc', { length: 500 }),
    otherPaymentId: varchar('other_payment_id', { length: 500 }),
    otherPaymentAmount: numeric('other_payment_amount', { precision: 18, scale: 4 }),
    otherPaymentMethodDesc: varchar('other_payment_method_desc', { length: 500 }),
    otherPaymentPostingDate: varchar('other_payment_posting_date', { length: 500 }),
    otherPaymentRef: varchar('other_payment_ref', { length: 500 }),
    otherPaymentCategoryDesc: varchar('other_payment_category_desc', { length: 500 }),
    primaryInsAllowed: numeric('primary_ins_allowed', { precision: 18, scale: 4 }),
    primaryInsCoinsurance: numeric('primary_ins_coinsurance', { precision: 18, scale: 4 }),
    primaryInsContractAdjustment: numeric('primary_ins_contract_adjustment', { precision: 18, scale: 4 }),
    primaryInsContractAdjustmentReason: varchar('primary_ins_contract_adjustment_reason', { length: 500 }),
    primaryInsCopay: numeric('primary_ins_copay', { precision: 18, scale: 4 }),
    primaryInsDeductible: numeric('primary_ins_deductible', { precision: 18, scale: 4 }),
    primaryInsPayment: numeric('primary_ins_payment', { precision: 18, scale: 4 }),
    primaryInsSecondaryAdjustment: numeric('primary_ins_secondary_adjustment', { precision: 18, scale: 4 }),
    primaryInsSecondaryAdjustmentReason: varchar('primary_ins_secondary_adjustment_reason', { length: 500 }),
    secondaryInsAllowed: numeric('secondary_ins_allowed', { precision: 18, scale: 4 }),
    secondaryInsCoinsurance: numeric('secondary_ins_coinsurance', { precision: 18, scale: 4 }),
    secondaryInsContractAdjustment: numeric('secondary_ins_contract_adjustment', { precision: 18, scale: 4 }),
    secondaryInsContractAdjustmentReason: varchar('secondary_ins_contract_adjustment_reason', { length: 500 }),
    secondaryInsCopay: numeric('secondary_ins_copay', { precision: 18, scale: 4 }),
    secondaryInsDeductible: numeric('secondary_ins_deductible', { precision: 18, scale: 4 }),
    secondaryInsPayment: numeric('secondary_ins_payment', { precision: 18, scale: 4 }),
    secondaryInsSecondaryAdjustment: numeric('secondary_ins_secondary_adjustment', { precision: 18, scale: 4 }),
    secondaryInsSecondaryAdjustmentReason: varchar('secondary_ins_secondary_adjustment_reason', { length: 500 }),
    tertiaryInsAllowed: numeric('tertiary_ins_allowed', { precision: 18, scale: 4 }),
    tertiaryInsCoinsurance: numeric('tertiary_ins_coinsurance', { precision: 18, scale: 4 }),
    tertiaryInsContractAdjustment: numeric('tertiary_ins_contract_adjustment', { precision: 18, scale: 4 }),
    tertiaryInsContractAdjustmentReason: varchar('tertiary_ins_contract_adjustment_reason', { length: 500 }),
    tertiaryInsCopay: numeric('tertiary_ins_copay', { precision: 18, scale: 4 }),
    tertiaryInsDeductible: numeric('tertiary_ins_deductible', { precision: 18, scale: 4 }),
    tertiaryInsPayment: numeric('tertiary_ins_payment', { precision: 18, scale: 4 }),
    tertiaryInsSecondaryAdjustment: numeric('tertiary_ins_secondary_adjustment', { precision: 18, scale: 4 }),
    tertiaryInsSecondaryAdjustmentReason: varchar('tertiary_ins_secondary_adjustment_reason', { length: 500 }),
    source: varchar('source', { length: 100 }).default('EDI').notNull(),
    writeOffAmount: varchar('write_off_amount', { length: 50 }),
    userAdjustments: jsonb('user_adjustments').default({}),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Claim Notes table - Notes attached to claims
 */
export const claimNotes = ediTebraSchema.table('claim_notes', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    transactionId: varchar('transaction_id', { length: 500 }),
    claimId: uuid('claim_id').references(() => claimHeader.claimId),
    claimSource: varchar('claim_source', { length: 100 }),
    claimClientId: integer('claim_client_id'),
    claimPracticeId: integer('claim_practice_id'),
    claimPatCtrlNum: varchar('claim_pat_ctrl_num', { length: 500 }),
    claimPatDob: varchar('claim_pat_dob', { length: 500 }),
    claimDetailId: integer('claim_detail_id').references(() => claimDetails.id),
    clmNoteType: varchar('clm_note_type', { length: 500 }),
    actionType: varchar('action_type', { length: 50 }),
    clmNoteText: text('clm_note_text'),
    clmRouteReason: varchar('clm_route_reason', { length: 500 }).default(''),
    clmDatetime: timestamp('clm_datetime').defaultNow(),
    clmLogin: uuid('clm_login'),
    noteCategory: varchar('note_category', { length: 100 }),
    source: varchar('source', { length: 100 }).default('EDI').notNull(),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Claim Adjustment Detail table - Track individual adjustments per claim and service line
 */
export const claimAdjustmentDetail = ediTebraSchema.table('claim_adjustment_detail', {
    id: serial('id').primaryKey().notNull(),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId),
    serviceLineId: integer('service_line_id').references(() => claimDetails.id),
    groupCode: varchar('group_code', { length: 5 }).notNull().references(() => groupCodeLookup.groupCode),
    reasonCode: varchar('reason_code', { length: 10 }).notNull().references(() => reasonCodeLookup.reasonCode),
    amount: numeric('amount', { precision: 18, scale: 2 }).notNull(),
});

/**
 * Claim Attachments table - Documents attached to claims
 */
export const claimAttachments = ediTebraSchema.table('attachments', {
    id: serial('id').primaryKey().notNull(),
    typeId: uuid('type_id'),
    documentDcn: varchar('document_dcn', { length: 100 }),
    associatedClaimDcn: varchar('associated_claim_dcn', { length: 100 }),
    claimCreationResponse: jsonb('claim_creation_response'),
    parentAttachmentId: integer('parent_attachment_id'),
    parentAttachmentName: text('parent_attachment_name'),
    originalFileName: text('original_file_name'),
    retrieval: text('retrieval'),
    uploadSource: text('upload_source'),
    rotationDegrees: integer('rotation_degrees'),
    pageCount: integer('page_count'),
    extractedFilesCount: integer('extracted_files_count'),
    clientId: integer('client_id').references(() => client.clientId, { onDelete: 'cascade' }),
    groupId: integer('group_id').references(() => group.id, { onDelete: 'cascade' }),
    practiceId: integer('practice_id').references(() => practice.id, { onDelete: 'cascade' }),
    processed: boolean('processed').default(false).notNull(),
    sha: varchar('sha', { length: 64 }),
    clmAttPath: text('clm_att_path'),
    clmAttFilename: text('clm_att_filename'),
    clmAttDatetime: timestamp('clm_att_datetime'),
    clmLogin: varchar('clm_login', { length: 500 }),
    status: varchar('status', { length: 5 }).default('G').notNull(),
    attachmentType: varchar('attachment_type', { length: 50 }),
    rawExtractedData: jsonb('raw_extracted_data'),
    processedExtractedData: jsonb('processed_extracted_data'),
    extractionMetadata: jsonb('extraction_metadata'),
    user_id: uuid('user_id'),
    assignedToId: uuid('assigned_to_id').references(() => users.id),
    createdAt: timestamp('created_at').notNull(),
    updatedAt: timestamp('updated_at').notNull(),
});

/**
 * Claim Audit History table - Audit trail for claims
 */
export const claimAuditHistory = ediTebraSchema.table('claim_audit_history', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    transactionId: varchar('transaction_id', { length: 500 }),
    claimId: uuid('claim_id').references(() => claimHeader.claimId),
    claimSource: varchar('claim_source', { length: 100 }),
    claimClientId: integer('claim_client_id'),
    claimPracticeId: integer('claim_practice_id'),
    claimPatCtrlNum: varchar('claim_pat_ctrl_num', { length: 500 }),
    claimPatDob: varchar('claim_pat_dob', { length: 500 }),
    clmAudMod: varchar('clm_aud_mod', { length: 500 }),
    clmAudQueue: varchar('clm_aud_queue', { length: 500 }),
    clmAudCurOwner: varchar('clm_aud_cur_owner', { length: 500 }),
    clmAudPrvOwner: varchar('clm_aud_prv_owner', { length: 500 }),
    clmAudAction: varchar('clm_aud_action', { length: 500 }),
    clmAudDatetime: timestamp('clm_aud_datetime').defaultNow(),
    clmAudLogin: varchar('clm_aud_login', { length: 500 }),
    source: varchar('source', { length: 100 }).default('EDI').notNull(),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Claim Assignments table - Track which claims are assigned to which users
 */
export const claimAssignments = ediTebraSchema.table('claim_assignments', {
    id: serial('id').primaryKey().notNull(),
    claimId: uuid('claim_id').notNull().unique(),
    userId: uuid('user_id'),
});

/**
 * Claim Process History table - Track clearing house submission and response history
 */
export const claimProcessHistory = ediTebraSchema.table('claim_process_history', {
    id: serial('id').primaryKey().notNull(),
    claimId: uuid('claim_id'),

    // Process Event Information
    processType: varchar('process_type', { length: 100 }).notNull(), // 'SUBMISSION', 'ACKNOWLEDGMENT', 'REJECTION', 'ACCEPTANCE', 'PAYMENT', 'DENIAL', 'EXTRACTION'
    processStatus: varchar('process_status', { length: 100 }).notNull(), // 'PENDING', 'SUCCESS', 'FAILED', 'PARTIAL'
    processDate: timestamp('process_date').defaultNow().notNull(),

    // Clearing House Information
    clearingHouseId: varchar('clearing_house_id', { length: 100 }),
    clearingHouseName: varchar('clearing_house_name', { length: 500 }),
    clearingHouseResponseCode: varchar('clearing_house_response_code', { length: 100 }),
    clearingHouseResponseMessage: text('clearing_house_response_message'),
    clearingHouseTrackingId: varchar('clearing_house_tracking_id', { length: 500 }),
    clearingHouseControlNumber: varchar('clearing_house_control_number', { length: 100 }),

    // Batch/File Information
    batchId: integer('batch_id'),
    batchNumber: varchar('batch_number', { length: 100 }),
    fileName: varchar('file_name', { length: 500 }),
    extractFileName: varchar('extract_file_name', { length: 500 }),
    extractDate: timestamp('extract_date'),

    // EDI Transaction Information
    ediTransactionSetId: varchar('edi_transaction_set_id', { length: 100 }),
    ediInterchangeControlNumber: varchar('edi_interchange_control_number', { length: 100 }),
    ediGroupControlNumber: varchar('edi_group_control_number', { length: 100 }),

    // Acceptance/Rejection Details
    acceptanceStatus: varchar('acceptance_status', { length: 100 }), // 'ACCEPTED', 'REJECTED', 'ACCEPTED_WITH_ERRORS'
    rejectionReasonCode: varchar('rejection_reason_code', { length: 100 }),
    rejectionReasonDescription: text('rejection_reason_description'),
    errorCount: integer('error_count').default(0),
    warningCount: integer('warning_count').default(0),

    // Payment Information
    paymentStatus: varchar('payment_status', { length: 100 }), // 'PENDING', 'PAID', 'PARTIALLY_PAID', 'DENIED', 'ADJUSTED'
    paymentAmount: numeric('payment_amount', { precision: 18, scale: 2 }),
    paidDate: timestamp('paid_date'),
    checkNumber: varchar('check_number', { length: 100 }),
    paymentMethod: varchar('payment_method', { length: 100 }),

    // Payer Information
    payerId: varchar('payer_id', { length: 100 }),
    payerName: varchar('payer_name', { length: 500 }),

    // Additional Details
    claimAmount: numeric('claim_amount', { precision: 18, scale: 2 }),
    allowedAmount: numeric('allowed_amount', { precision: 18, scale: 2 }),
    adjustmentAmount: numeric('adjustment_amount', { precision: 18, scale: 2 }),

    // Error/Warning Details (JSON array)
    errorDetails: jsonb('error_details'),
    warningDetails: jsonb('warning_details'),

    // Additional Metadata
    responsePayload: jsonb('response_payload'), // Full clearing house response
    notes: text('notes'),
    processedBy: varchar('processed_by', { length: 500 }),

    // Timestamps
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Claim Validation table - Store validation results for claims
 */
export const claimValidation = ediTebraSchema.table('claim_validation_errors', {
    id: serial('id').primaryKey().notNull(),
    claimId: uuid('claim_id'),
    source: text('source'),
    clientId: integer('client_id'),
    practiceId: integer('practice_id'),
    clmPatctrlnum: text('clm_patctrlnum'),
    patDob: varchar('pat_dob', { length: 500 }),
    validationResult: text('validation_result'),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

/**
 * Claim jobs table - Store jobs for claims
 */

export const ClaimImportJobStatus = {
    Ready: "Ready",
    Processing: "Processing",
    Completed: "Completed",
    Failed: "Failed",
} as const;

export const claimImportJobs = ediTebraSchema.table(
    "claim_import_jobs",
    {
        id: serial("id").primaryKey(),

        jobType: varchar("job_type", {
            length: 100,
        }).notNull(),

        jobStatus: varchar("job_status", {
            length: 20,
        }).notNull(),

        fileName: text("file_name").notNull(),

        filePath: text("file_path").notNull(),

        clientId: integer("client_id"),

        groupId: integer("group_id"),

        practiceId: integer("practice_id"),

        totalRecords: integer("total_records"),

        processedRecords: integer("processed_records"),

        result: jsonb("result"),

        errorDetails: text("error_details"),

        createdBy: uuid("created_by"),

        startedAt: timestamp("started_at"),

        completedAt: timestamp("completed_at"),

        createdAt: timestamp("created_at"),

        updatedAt: timestamp("updated_at"),
    },
);

/**
 * Inbound Files - Tracks files received from the clearing house response flow
 */
export const inboundFiles = ediTebraSchema.table('inbound_files', {
    id: uuid('id').primaryKey().defaultRandom(),
    fileName: text('file_name').notNull(),
    filePath: text('file_path').notNull(),
    processed: boolean('processed').default(false),
    clearingHouseId: uuid('clearing_house_id').references(() => clearingHouses.id),
    uploadedAt: timestamp('uploaded_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

// ============================================================================
// APPOINTMENTS & SCHEDULING TABLES
// ============================================================================

/**
 * Appointment Reasons table
 */
export const appointmentReasons = ediTebraSchema.table('AppointmentReasons', {
    serial: serial('Serial').primaryKey().notNull(),
    appointmentReasonId: text('AppointmentReasonId').notNull().unique(),
    appointmentReasonGuid: text('AppointmentReasonGuid'),
    name: text('Name'),
    clientId: integer('client_id').references(() => client.clientId),
    defaultDurationMinutes: text('DefaultDurationMinutes'),
    defaultColorCode: text('DefaultColorCode'),
    practiceResourceIds: text('PracticeResourceIds'),
    providerIds: text('ProviderIds'),
});

/**
 * Appointment Reason Procedure Codes - junction table
 */
export const appointmentReasonProcedureCodes = ediTebraSchema.table('AppointmentReasonProcedureCodes', {
    id: serial('ID').primaryKey().notNull(),
    appointmentReasonId: text('AppointmentReasonId').references(() => appointmentReasons.appointmentReasonId),
    arProcedureCodeId: text('ARProcedureCodeId'),
});

/**
 * Appointments table
 */
export const appointments = ediTebraSchema.table('Appointments', {
    serial: serial('Serial').primaryKey().notNull(),
    source: varchar('source', { length: 100 }).notNull().default('EDI'),
    id: text('ID').notNull().unique(),
    clientId: integer('client_id').references(() => client.clientId),
    patientId: text('PatientID'),
    patientCaseId: text('PatientCaseID'),
    patientCaseName: text('PatientCaseName'),
    patientCasePayerScenario: text('PatientCasePayerScenario'),
    appointmentDuration: text('AppointmentDuration'),
    allDay: boolean('AllDay'),
    confirmationStatus: text('ConfirmationStatus'),
    type: text('Type'),
    notes: text('Notes'),
    authorizationId: text('AuthorizationID'),
    authorizationNumber: text('AuthorizationNumber'),
    authorizationInsurancePlan: text('AuthorizationInsurancePlan'),
    authorizationStartDate: text('AuthorizationStartDate'),
    authorizationEndDate: text('AuthorizationEndDate'),
    startDate: text('StartDate'),
    endDate: text('EndDate'),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
    serviceLocationId: integer('ServiceLocationID'),
    appointmentReasonId1: text('AppointmentReasonID1'),
    appointmentReasonId2: text('AppointmentReasonID2'),
    appointmentReasonId3: text('AppointmentReasonID3'),
    appointmentReasonId4: text('AppointmentReasonID4'),
    appointmentReasonId5: text('AppointmentReasonID5'),
    appointmentReasonId6: text('AppointmentReasonID6'),
    appointmentReasonId7: text('AppointmentReasonID7'),
    appointmentReasonId8: text('AppointmentReasonID8'),
    appointmentReasonId9: text('AppointmentReasonID9'),
    appointmentReasonId10: text('AppointmentReasonID10'),
    resourceId1: text('ResourceID1'),
    resourceId2: text('ResourceID2'),
    resourceId3: text('ResourceID3'),
    resourceId4: text('ResourceID4'),
    resourceId5: text('ResourceID5'),
    resourceId6: text('ResourceID6'),
    resourceId7: text('ResourceID7'),
    resourceId8: text('ResourceID8'),
    resourceId9: text('ResourceID9'),
    resourceId10: text('ResourceID10'),
    resourceName1: text('ResourceName1'),
    resourceName2: text('ResourceName2'),
    resourceName3: text('ResourceName3'),
    resourceName4: text('ResourceName4'),
    resourceName5: text('ResourceName5'),
    resourceName6: text('ResourceName6'),
    resourceName7: text('ResourceName7'),
    resourceName8: text('ResourceName8'),
    resourceName9: text('ResourceName9'),
    resourceName10: text('ResourceName10'),
    resourceTypeId1: text('ResourceTypeID1'),
    resourceTypeId2: text('ResourceTypeID2'),
    resourceTypeId3: text('ResourceTypeID3'),
    resourceTypeId4: text('ResourceTypeID4'),
    resourceTypeId5: text('ResourceTypeID5'),
    resourceTypeId6: text('ResourceTypeID6'),
    resourceTypeId7: text('ResourceTypeID7'),
    resourceTypeId8: text('ResourceTypeID8'),
    resourceTypeId9: text('ResourceTypeID9'),
    resourceTypeId10: text('ResourceTypeID10'),
    recurring: boolean('Recurring'),
});

// ============================================================================
// PROCEDURE CODES & PROVIDERS TABLES (Tebra)
// ============================================================================

/**
 * Procedure Codes table - Medical procedure codes
 */
export const procedureCodes = ediTebraSchema.table('ProcedureCodes', {
    serial: serial('Serial').primaryKey().notNull(),
    id: text('ID'),
    active: boolean('Active'),
    billableCode: text('BillableCode'),
    createdDate: text('CreatedDate'),
    customerSpecific: text('CustomerSpecific'),
    defaultUnits: text('DefaultUnits'),
    drugName: text('DrugName'),
    localName: text('LocalName'),
    modifiedDate: text('ModifiedDate'),
    ndc: text('NDC'),
    officialDescription: text('OfficialDescription'),
    officialName: text('OfficialName'),
    clientName: text('client_name'),
    procedureCode: text('ProcedureCode').notNull().unique(),
    procedureCodeCategoryId: text('ProcedureCodeCategoryID'),
    typeofServiceCode: text('TypeofServiceCode'),
});

/**
 * Providers table (from Tebra)
 */
export const providers = ediTebraSchema.table('Providers', {
    serial: serial('Serial').primaryKey().notNull(),
    id: text('ID').notNull().unique(),
    clientId: integer('client_id').references(() => client.clientId),
    fullName: text('FullName'),
    active: boolean('Active'),
    departmentName: text('DepartmentName'),
    suffix: text('Suffix'),
    prefix: text('Prefix'),
    encounterFormName: text('EncounterFormName'),
    firstName: text('FirstName'),
    middleName: text('MiddleName'),
    lastName: text('LastName'),
    degree: text('Degree'),
    specialtyName: text('SpecialtyName'),
    type: text('Type'),
    billingType: text('BillingType'),
    nationalProviderIdentifier: text('NationalProviderIdentifier'),
    providerPerformanceReportActive: boolean('ProviderPerformanceReportActive'),
    providerPerformanceReportScope: text('ProviderPerformanceReportScope'),
    providerPerformanceReportFequency: text('ProviderPerformanceReportFequency'),
    providerPerformanceReportDelay: text('ProviderPerformanceReportDelay'),
    providerPerformanceReportCCEmailRecipients: text('ProviderPerformanceReportCCEmailRecipients'),
    emailAddress: text('EmailAddress'),
    workPhone: text('WorkPhone'),
    workPhoneExt: text('WorkPhoneExt'),
    mobilePhone: text('MobilePhone'),
    mobilePhoneExt: text('MobilePhoneExt'),
    homePhone: text('HomePhone'),
    homePhoneExt: text('HomePhoneExt'),
    fax: text('Fax'),
    faxExt: text('FaxExt'),
    pager: text('Pager'),
    pagerExt: text('PagerExt'),
    addressLine1: text('AddressLine1'),
    addressLine2: text('AddressLine2'),
    city: text('City'),
    state: text('State'),
    zipCode: text('ZipCode'),
    country: text('Country'),
    createdDate: text('CreatedDate'),
    lastModifiedDate: text('LastModifiedDate'),
    notes: text('Notes'),
    socialSecurityNumber: text('SocialSecurityNumber'),
});

// ============================================================================
// LOOKUP TABLES (Reference Data)
// ============================================================================

/**
 * Group Code Lookup table - CAS segment group codes
 */
export const groupCodeLookup = ediTebraSchema.table('group_code_lookup', {
    groupCode: varchar('group_code', { length: 5 }).primaryKey().notNull(),
    description: text('description'),
    usageNotes: text('usage_notes'),
    owedBy: varchar('owed_by', { length: 50 }),
});

/**
 * Reason Code Lookup table - CARC codes
 */
export const reasonCodeLookup = ediTebraSchema.table('reason_code_lookup', {
    reasonCode: varchar('reason_code', { length: 10 }).primaryKey().notNull(),
    description: text('description'),
    groupCode: varchar('group_code', { length: 5 }).references(() => groupCodeLookup.groupCode),
    category: varchar('category', { length: 100 }),
    status: varchar('status', { length: 20 }),
});

/**
 * RARC Codes table - Remittance Advice Remark Codes
 */
export const rarcCodes = ediTebraSchema.table('rarc_codes', {
    rarcCode: varchar('rarc_code', { length: 20 }),
    description: text('description'),
});

// ============================================================================
// REMITTANCE (835) TABLES
// ============================================================================

/**
 * EOB Deposits table - Deposit-level extracted remittance summary rows
 */
export const eobDeposits = ediTebraSchema.table('eob_deposits', {
    id: serial('id').primaryKey().notNull(),
    sourceFile: text('source_file').notNull(),
    batchRunId: text('batch_run_id'),
    depositDate: date('deposit_date'),
    payerName: text('payer_name'),
    checkEftNumber: text('check_eft_number'),
    paymentMethod: text('payment_method'),
    depositAmount: numeric('deposit_amount', { precision: 12, scale: 2 }),
    claimsCount: integer('claims_count'),
    billingProviderName: text('billing_provider_name'),
    billingProviderNpi: text('billing_provider_npi'),
    billingProviderTin: text('billing_provider_tin'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow(),
    rawExtraction: jsonb('raw_extraction'),
    rawPdfText: text('raw_pdf_text'),
    entity: text('entity'),
    subEntity: text('sub_entity'),
    loadDate: date('load_date').default(sql`CURRENT_DATE`),
    depositType: text('deposit_type').default('FULL_REMITTANCE'),
    extractionPath: text('extraction_path'),
    processed: boolean('processed').default(false).notNull(),
}, (table) => ({
    sourceFileCheckEftNumberKey: uniqueIndex('eob_deposits_source_file_check_eft_number_key').on(table.sourceFile, table.checkEftNumber),
    checkEftNumberIdx: index('idx_eob_deposits_check').on(table.checkEftNumber),
    depositDateIdx: index('idx_eob_deposits_date').on(table.depositDate),
    payerNameIdx: index('idx_eob_deposits_payer').on(table.payerName),
}));

/**
 * EOB Postings table - Claim/service-line posting rows extracted from EOB deposits
 */
export const eobPostings = ediTebraSchema.table('eob_postings', {
    id: serial('id').primaryKey().notNull(),
    sourceFile: text('source_file').notNull(),
    batchRunId: text('batch_run_id'),
    eobDepositId: integer('eob_deposit_id').references(() => eobDeposits.id),
    depositDate: date('deposit_date'),
    checkEftNumber: text('check_eft_number'),
    payerName: text('payer_name'),
    patientLastName: text('patient_last_name'),
    patientFirstName: text('patient_first_name'),
    memberId: text('member_id'),
    patientAccountNumber: text('patient_account_number'),
    groupNumber: text('group_number'),
    claimNumber: text('claim_number'),
    claimStatus: text('claim_status'),
    serviceLineNumber: integer('service_line_number'),
    dateOfService: date('date_of_service'),
    cptCode: text('cpt_code'),
    modifier: text('modifier'),
    placeOfService: text('place_of_service'),
    units: numeric('units', { precision: 8, scale: 2 }),
    billedAmount: numeric('billed_amount', { precision: 12, scale: 2 }),
    allowedAmount: numeric('allowed_amount', { precision: 12, scale: 2 }),
    insurancePaid: numeric('insurance_paid', { precision: 12, scale: 2 }),
    contractualAdjustment: numeric('contractual_adjustment', { precision: 12, scale: 2 }),
    patientCopay: numeric('patient_copay', { precision: 12, scale: 2 }),
    patientCoinsurance: numeric('patient_coinsurance', { precision: 12, scale: 2 }),
    patientDeductible: numeric('patient_deductible', { precision: 12, scale: 2 }),
    patientResponsibility: numeric('patient_responsibility', { precision: 12, scale: 2 }),
    adjustmentReasonCodes: text('adjustment_reason_codes'),
    remarkCodes: text('remark_codes'),
    billingProviderName: text('billing_provider_name'),
    billingProviderNpi: text('billing_provider_npi'),
    billingProviderTin: text('billing_provider_tin'),
    renderingProviderName: text('rendering_provider_name'),
    renderingProviderNpi: text('rendering_provider_npi'),
    postingStatus: text('posting_status'),
    posted: boolean('posted').default(false),
    postedAt: timestamp('posted_at', { withTimezone: true }),
    postedBy: text('posted_by'),
    matchedPatientId: integer('matched_patient_id'),
    matchedClaimHeaderId: integer('matched_claim_header_id'),
    matchedClaimDetailId: integer('matched_claim_detail_id'),
    matchConfidence: text('match_confidence'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow(),
    payerClaimControlNumber: text('payer_claim_control_number'),
    claimFilingIndicator: text('claim_filing_indicator'),
    forwardedToPayer: text('forwarded_to_payer'),
    claimStatusCode: text('claim_status_code'),
    nonCovered: numeric('non_covered', { precision: 12, scale: 2 }),
    needsReview: boolean('needs_review').default(false),
    reviewReasons: text('review_reasons'),
    entity: text('entity'),
    subEntity: text('sub_entity'),
    loadDate: date('load_date').default(sql`CURRENT_DATE`),
    sourcePages: integer('source_pages').array(),
    extractionPath: text('extraction_path'),
    otherPayerPaid: numeric('other_payer_paid', { precision: 12, scale: 2 }).default('0'),
    sequestrationAmount: numeric('sequestration_amount', { precision: 12, scale: 2 }).default('0'),
    priorPayment: numeric('prior_payment', { precision: 12, scale: 2 }).default('0'),
    interestPaid: numeric('interest_paid', { precision: 12, scale: 2 }).default('0'),
    deniedOrExcluded: numeric('denied_or_excluded', { precision: 12, scale: 2 }).default('0'),
    balanceGapType: text('balance_gap_type'),
    otherProviderAdjustment: numeric('other_provider_adjustment', { precision: 12, scale: 2 }),
    documentType: varchar('document_type', { length: 100 }),
    remitCreated: boolean('remit_created').default(false).notNull(),
}, (table) => ({
    checkEftNumberIdx: index('idx_eob_postings_check').on(table.checkEftNumber),
    claimNumberIdx: index('idx_eob_postings_claim_number').on(table.claimNumber),
    cptCodeIdx: index('idx_eob_postings_cpt').on(table.cptCode),
    eobDepositIdIdx: index('idx_eob_postings_deposit').on(table.eobDepositId),
    dateOfServiceIdx: index('idx_eob_postings_dos').on(table.dateOfService),
    matchedPatientIdIdx: index('idx_eob_postings_matched').on(table.matchedPatientId),
    memberIdIdx: index('idx_eob_postings_member_id').on(table.memberId),
    patientAccountNumberIdx: index('idx_eob_postings_patient_account').on(table.patientAccountNumber),
    skipCheckIdx: index('idx_eob_postings_skip_check').on(table.sourceFile, table.loadDate, table.entity, table.subEntity),
    postingStatusIdx: index('idx_eob_postings_status').on(table.postingStatus),
    unpostedIdx: index('idx_eob_postings_unposted').on(table.posted).where(sql`${table.posted} = false`),
}));

/**
 * EOB Remit Carriers table - Carrier records for uploaded remit files
 */
export const eobRemitCarriers = ediTebraSchema.table('eob_remit_carriers', {
    id: uuid('id').primaryKey().defaultRandom().notNull(),
    carrierName: text('carrier_name'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow(),
});

/**
 * EOB Remit Files table - Files attached to remit carriers
 */
export const eobRemitFiles = ediTebraSchema.table('eob_remit_files', {
    id: uuid('id').primaryKey().defaultRandom().notNull(),
    eobRemitId: uuid('eob_remit_id').notNull().references(() => eobRemitCarriers.id, { onDelete: 'cascade' }),
    fileName: text('file_name'),
    remitAmount: numeric('remit_amount', { precision: 18, scale: 2 }),
    response: jsonb('response'),
    checkNumber: text('check_number'),
    processed: boolean('processed').default(false),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow(),
    claimCount: integer('claim_count'),
});

/**
 * Remit Check Header table - Payment/check information
 */
export const remitChkHdr = ediTebraSchema.table('remit_chkhdr', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id').references(() => client.clientId),
    groupId: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id').references(() => practice.id),
    source: varchar('source', { length: 500 }).default('electronic').notNull(),
    dcn: varchar('dcn', { length: 500 }),
    vHdrPayeeName: varchar('v_hdr_payee_name', { length: 500 }),
    vHdrPayeeAddress1: varchar('v_hdr_payee_address1', { length: 500 }),
    vHdrPayeeAddress2: varchar('v_hdr_payee_address2', { length: 500 }),
    vHdrPayeeCity: varchar('v_hdr_payee_city', { length: 500 }),
    vHdrPayeeState: varchar('v_hdr_payee_state', { length: 500 }),
    vHdrPayeeZip: varchar('v_hdr_payee_zip', { length: 500 }),
    vHdrPayeeNum: varchar('v_hdr_payee_num', { length: 500 }),
    vHdrPayeeTinNum: varchar('v_hdr_payee_tin_num', { length: 500 }),
    vHdrCheckDate: varchar('v_hdr_check_date', { length: 500 }),
    vHdrCheckNum: varchar('v_hdr_check_num', { length: 500 }),
    vHdrCheckAmt: numeric('v_hdr_check_amt', { precision: 18, scale: 2 }),
    vHdrPaperfreeStatus: varchar('v_hdr_paperfree_status', { length: 500 }),
    payerName: varchar('payer_name', { length: 500 }),
    payerId: varchar('payer_id', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Remit Check Summary table - Payment summary totals
 */
export const remitChkSummary = ediTebraSchema.table('remit_chk_summary', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    vTotBillAmt: numeric('v_tot_bill_amt', { precision: 18, scale: 2 }),
    vTotPendAmt: numeric('v_tot_pend_amt', { precision: 18, scale: 2 }),
    vTotDisallowAmt: numeric('v_tot_disallow_amt', { precision: 18, scale: 2 }),
    vTotDenyAmt: numeric('v_tot_deny_amt', { precision: 18, scale: 2 }),
    vTotCopayAmt: numeric('v_tot_copay_amt', { precision: 18, scale: 2 }),
    vTotRiskAmt: numeric('v_tot_risk_amt', { precision: 18, scale: 2 }),
    vTotCobAmt: numeric('v_tot_cob_amt', { precision: 18, scale: 2 }),
    vTotPaidAmt: numeric('v_tot_paid_amt', { precision: 18, scale: 2 }),
    vTotCreditAmt: numeric('v_tot_credit_amt', { precision: 18, scale: 2 }),
    vTotAdvanceAmt: numeric('v_tot_advance_amt', { precision: 18, scale: 2 }),
    vTotClaimsPaid: integer('v_tot_claims_paid'),
    vTotCheckNum: varchar('v_tot_check_num', { length: 500 }),
    remitChkHdrId: integer('remit_chk_hdr_id').references(() => remitChkHdr.id),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
    postedStatus: varchar('posted_status', { length: 50 }),
    routedStatus: varchar('routed_status', { length: 50 }),
    routingReason: text('routing_reason'),
    assignedToId: uuid('assigned_to_id').references(() => users.id),
    postingStatus: boolean('posting_status').default(false),
    assignedToName: varchar('assigned_to_name', { length: 255 }),
    queue: jsonb('queue').default({}),
});

/**
 * Check Level Notes table - Notes attached to remittance checks
 */
export const checkLevelNotes = ediTebraSchema.table('check_level_notes', {
    id: uuid('id').primaryKey().defaultRandom(),
    remitChkHdrId: integer('remit_chk_hdr_id').notNull().references(() => remitChkHdr.id, { onDelete: 'cascade' }),
    notesType: varchar('notes_type', { length: 500 }),
    notes: text('notes'),
    createdAt: varchar('created_at', { length: 50 }),
    createdBy: uuid('created_by').references(() => users.id),
});

/**
 * Check Level Attachments table - Files attached to remittance checks
 */
export const checkLevelAttachments = ediTebraSchema.table('check_level_attachments', {
    id: uuid('id').primaryKey().defaultRandom(),
    remitChkHdrId: integer('remit_chk_hdr_id').notNull().references(() => remitChkHdr.id, { onDelete: 'cascade' }),
    fileName: varchar('file_name', { length: 500 }),
    filePath: text('file_path'),
    uploadedAt: varchar('uploaded_at', { length: 50 }),
    uploadedBy: uuid('uploaded_by').references(() => users.id),
});

/**
 * Remit Claim Header table - Claim-level payment information
 */
export const remitClmHdr = ediTebraSchema.table('remit_clmhdr', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    vChPatLastName: varchar('v_ch_pat_last_name', { length: 500 }),
    vChPatFirstName: varchar('v_ch_pat_first_name', { length: 500 }),
    vChPatDOB: varchar('v_ch_pat_dob', { length: 500 }),
    vChClaimNum: varchar('v_ch_claim_num', { length: 500 }),
    vChPatMemberNum: varchar('v_ch_pat_member_num', { length: 500 }),
    vChPatAccount: varchar('v_ch_pat_account', { length: 500 }),
    vChPrincipalDiagCode: varchar('v_ch_principal_diag_code', { length: 500 }),
    vChAdmitDate: varchar('v_ch_admit_date', { length: 500 }),
    vChDischargeDate: varchar('v_ch_discharge_date', { length: 500 }),
    vChPlanNum: varchar('v_ch_plan_num', { length: 500 }),
    vChTotalCharges: numeric('v_ch_total_charges', { precision: 18, scale: 2 }),
    vChTotPatResp: numeric('v_ch_tot_pat_resp', { precision: 18, scale: 2 }),
    vChTotPaid: numeric('v_ch_tot_paid', { precision: 18, scale: 2 }),
    vChDrgCode: varchar('v_ch_drg_code', { length: 500 }),
    vChTobCode: varchar('v_ch_tob_code', { length: 500 }),
    vChInsdLastName: varchar('v_ch_insd_last_name', { length: 500 }),
    vChInsdFirstName: varchar('v_ch_insd_first_name', { length: 500 }),
    vChProvNum: varchar('v_ch_prov_num', { length: 500 }),
    vChCheckNum: varchar('v_ch_check_num', { length: 500 }),
    vChInsdMemberNum: varchar('v_ch_insd_member_num', { length: 500 }),
    remitChkHdrId: integer('remit_chk_hdr_id').references(() => remitChkHdr.id),
    claimHeaderId: integer('claim_header_id'),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
    vChClaimStatus: varchar('v_ch_claim_status', { length: 50 }),
    excluded: boolean('excluded').default(false),
    posted: boolean('posted').default(false),
    mappedTo: boolean('mapped_to').default(true),
    payerClaim: varchar('payer_claim', { length: 500 }),
    postingDate: varchar('posting_date', { length: 50 }),
});

/**
 * Remit Claim Service table - Service-level payment details
 */
export const remitClmSrv = ediTebraSchema.table('remit_clmsrv', {
    id: serial('id').primaryKey().notNull(),
    clientId: integer('client_id'),
    groupId: integer('group_id'),
    practiceId: integer('practice_id'),
    vCdClaimNum: varchar('v_cd_claim_num', { length: 500 }),
    vCdServNum: varchar('v_cd_serv_num', { length: 500 }),
    vCdServDate: varchar('v_cd_serv_date', { length: 500 }),
    vCdRevCode: varchar('v_cd_rev_code', { length: 500 }),
    vCdProcCode: varchar('v_cd_proc_code', { length: 500 }),
    vCdProcMod1: varchar('v_cd_proc_mod1', { length: 500 }),
    vCdProcMod2: varchar('v_cd_proc_mod2', { length: 500 }),
    vCdPendExCode1: varchar('v_cd_pend_ex_code1', { length: 500 }),
    vCdPendExCode2: varchar('v_cd_pend_ex_code2', { length: 500 }),
    vCdPendExCode3: varchar('v_cd_pend_ex_code3', { length: 500 }),
    vCdPendExCode4: varchar('v_cd_pend_ex_code4', { length: 500 }),
    vCdPendExCode5: varchar('v_cd_pend_ex_code5', { length: 500 }),
    vCdPendExCode6: varchar('v_cd_pend_ex_code6', { length: 500 }),
    vCdPaidExCode1: varchar('v_cd_paid_ex_code1', { length: 500 }),
    vCdPaidExCode2: varchar('v_cd_paid_ex_code2', { length: 500 }),
    vCdPaidExCode3: jsonb('v_cd_paid_ex_code3'),
    vCdAmtBill: numeric('v_cd_amt_bill', { precision: 18, scale: 2 }),
    vCdAmtDisallow: numeric('v_cd_amt_disallow', { precision: 18, scale: 2 }),
    vCdAmtDeny: numeric('v_cd_amt_deny', { precision: 18, scale: 2 }),
    vCdAmtDeduct: numeric('v_cd_amt_deduct', { precision: 18, scale: 2 }),
    vCdAmtCoIns: numeric('v_cd_amt_co_ins', { precision: 18, scale: 2 }),
    vCdAmtRisk: numeric('v_cd_amt_risk', { precision: 18, scale: 2 }),
    vCdAmtCob: numeric('v_cd_amt_cob', { precision: 18, scale: 2 }),
    vCdAmtPaid: numeric('v_cd_amt_paid', { precision: 18, scale: 2 }),
    userModifiedPaidAmount: numeric('user_modified_paid', { precision: 18, scale: 2 }),
    vCdAmtCopay: numeric('v_cd_amt_copay', { precision: 18, scale: 2 }),
    vCdLocationCode: varchar('v_cd_location_code', { length: 500 }),
    vCdCheckNum: varchar('v_cd_check_num', { length: 500 }),
    vCdPaidStatus: varchar('v_cd_paid_status', { length: 500 }),
    vCdPaidFlag: varchar('v_cd_paid_flag', { length: 500 }),
    vCdUnits: varchar('v_cd_units', { length: 500 }),
    userAdjustments: jsonb('user_adjustments').default({}),
    remitChkHdrId: integer('remit_chk_hdr_id').references(() => remitChkHdr.id),
    remitClmHdrId: integer('remit_clm_hdr_id').references(() => remitClmHdr.id),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

// ============================================================================
// RELATIONS
// ============================================================================

export const groupRelations = relations(group, ({ one }) => ({
    client: one(client, {
        fields: [group.clientId],
        references: [client.clientId],
    }),
}));

export const practiceRelations = relations(practice, ({ one }) => ({
    client: one(client, {
        fields: [practice.clientId],
        references: [client.clientId],
    }),
    group: one(group, {
        fields: [practice.groupId],
        references: [group.id],
    }),
}));

export const physicianRelations = relations(physician, ({ one }) => ({
    client: one(client, {
        fields: [physician.clientId],
        references: [client.clientId],
    }),
    group: one(group, {
        fields: [physician.groupId],
        references: [group.id],
    }),
    practice: one(practice, {
        fields: [physician.practiceId],
        references: [practice.id],
    }),
}));

// ============================================================================
// PATIENT RELATIONS
// ============================================================================

export const patientHeaderRelations = relations(patientHeader, ({ one, many }) => ({
    client: one(client, {
        fields: [patientHeader.clientId],
        references: [client.clientId],
    }),
    group: one(group, {
        fields: [patientHeader.groupId],
        references: [group.id],
    }),
    practice: one(practice, {
        fields: [patientHeader.practiceId],
        references: [practice.id],
    }),
    patientGuarantors: many(patientGuarantor),
    patientNotes: many(patientNotes),
    patientCoverages: many(patientCoverages),
}));

export const patientGuarantorRelations = relations(patientGuarantor, ({ one }) => ({
    patientHeader: one(patientHeader, {
        fields: [patientGuarantor.patId],
        references: [patientHeader.patId],
    }),
}));

export const patientNotesRelations = relations(patientNotes, ({ one }) => ({
    patientHeader: one(patientHeader, {
        fields: [patientNotes.patId],
        references: [patientHeader.patId],
    }),
}));

export const patientCoveragesRelations = relations(patientCoverages, ({ one }) => ({
    patientHeader: one(patientHeader, {
        fields: [patientCoverages.patId],
        references: [patientHeader.patId],
    }),
}));

export const reasonCodeLookupRelations = relations(reasonCodeLookup, ({ one }) => ({
    groupCode: one(groupCodeLookup, {
        fields: [reasonCodeLookup.groupCode],
        references: [groupCodeLookup.groupCode],
    }),
}));

export const remitChkHdrRelations = relations(remitChkHdr, ({ one }) => ({
    client: one(client, {
        fields: [remitChkHdr.clientId],
        references: [client.clientId],
    }),
    group: one(group, {
        fields: [remitChkHdr.groupId],
        references: [group.id],
    }),
    practice: one(practice, {
        fields: [remitChkHdr.practiceId],
        references: [practice.id],
    }),
}));

export const eobRemitCarriersRelations = relations(eobRemitCarriers, ({ many }) => ({
    files: many(eobRemitFiles),
}));

export const eobRemitFilesRelations = relations(eobRemitFiles, ({ one }) => ({
    remitCarrier: one(eobRemitCarriers, {
        fields: [eobRemitFiles.eobRemitId],
        references: [eobRemitCarriers.id],
    }),
}));

export const remitChkSummaryRelations = relations(remitChkSummary, ({ one }) => ({
    remitCheckHeader: one(remitChkHdr, {
        fields: [remitChkSummary.remitChkHdrId],
        references: [remitChkHdr.id],
    }),
}));

export const remitClmHdrRelations = relations(remitClmHdr, ({ one }) => ({
    remitCheckHeader: one(remitChkHdr, {
        fields: [remitClmHdr.remitChkHdrId],
        references: [remitChkHdr.id],
    }),
}));

export const remitClmSrvRelations = relations(remitClmSrv, ({ one }) => ({
    remitCheckHeader: one(remitChkHdr, {
        fields: [remitClmSrv.remitChkHdrId],
        references: [remitChkHdr.id],
    }),
    remitClaimHeader: one(remitClmHdr, {
        fields: [remitClmSrv.remitClmHdrId],
        references: [remitClmHdr.id],
    }),
}));

// ============================================================================
// CLAIM RELATIONS
// ============================================================================

export const claimHeaderRelations = relations(claimHeader, ({ one, many }) => ({
    client: one(client, {
        fields: [claimHeader.clientId],
        references: [client.clientId],
    }),
    group: one(group, {
        fields: [claimHeader.groupId],
        references: [group.id],
    }),
    practice: one(practice, {
        fields: [claimHeader.practiceId],
        references: [practice.id],
    }),
    claimDetails: many(claimDetails),
    claimNotes: many(claimNotes),
    claimAttachments: many(claimAttachments),
    claimAuditHistory: many(claimAuditHistory),
    outboundFiles: many(outboundFiles),
}));

export const claimDetailsRelations = relations(claimDetails, ({ one }) => ({
    claimHeader: one(claimHeader, {
        fields: [claimDetails.claimHeaderId],
        references: [claimHeader.claimId],
    }),
}));

export const claimNotesRelations = relations(claimNotes, ({ one }) => ({
    claimHeader: one(claimHeader, {
        fields: [claimNotes.claimId],
        references: [claimHeader.claimId],
    }),
    claimDetail: one(claimDetails, {
        fields: [claimNotes.claimDetailId],
        references: [claimDetails.id],
    }),
}));

export const claimAttachmentsRelations = relations(claimAttachments, ({ one }) => ({}));

export const claimAuditHistoryRelations = relations(claimAuditHistory, ({ one }) => ({
    claimHeader: one(claimHeader, {
        fields: [claimAuditHistory.claimId],
        references: [claimHeader.claimId],
    }),
}));

// ============================================================================
// APPOINTMENT RELATIONS
// ============================================================================

export const appointmentReasonsRelations = relations(appointmentReasons, ({ one, many }) => ({
    client: one(client, {
        fields: [appointmentReasons.clientId],
        references: [client.clientId],
    }),
    procedureCodes: many(appointmentReasonProcedureCodes),
}));

export const appointmentReasonProcedureCodesRelations = relations(appointmentReasonProcedureCodes, ({ one }) => ({
    appointmentReason: one(appointmentReasons, {
        fields: [appointmentReasonProcedureCodes.appointmentReasonId],
        references: [appointmentReasons.appointmentReasonId],
    }),
}));

export const appointmentsRelations = relations(appointments, ({ one }) => ({
    // Relations can be added based on business requirements
    // Currently appointments reference multiple IDs that aren't directly linked in schema
}));

// ============================================================================
// PROCEDURE CODES & PROVIDERS RELATIONS
// ============================================================================

export const procedureCodesRelations = relations(procedureCodes, ({ one }) => ({
    // Relations can be added based on business requirements
}));

export const providersRelations = relations(providers, ({ one }) => ({
    client: one(client, {
        fields: [providers.clientId],
        references: [client.clientId],
    }),
}));

// ============================================================================
// CARRIERS (PAYERS) TABLE
// ============================================================================

/**
 * Carriers table - Insurance carriers/payers information
 * Maps to EDI_Tebra.Carriers table
 * Used for patient coverage and claim payer lookups
 */
// export const carriers = ediTebraSchema.table('Carriers', {
//     payerId: varchar('payer_id', { length: 20 }).primaryKey().notNull(),
//     payerName: varchar('payer_name', { length: 255 }).notNull(),
//     payerType: varchar('payer_type', { length: 50 }),
//     claimPayerId: varchar('claim_payer_id', { length: 20 }),
//     eligibilityPayerId: varchar('eligibility_payer_id', { length: 20 }),
//     eraPayerId: varchar('era_payer_id', { length: 20 }),
//     addressLine1: varchar('address_line1', { length: 255 }),
//     addressLine2: varchar('address_line2', { length: 255 }),
//     city: varchar('city', { length: 100 }),
//     state: varchar('state', { length: 10 }),
//     zipCode: varchar('zip_code', { length: 20 }),
//     country: varchar('country', { length: 100 }),
// });

// ============================================================================
// ZIP CODES TABLE
// ============================================================================

/**
 * ZipCodes table - US ZIP code information with demographics and geographic data
 */
export const zipCodes = ediTebraSchema.table('ZipCodes', {
    ZipCode: varchar('ZipCode', { length: 10 }),
    City: text('City'),
    State: text('State'),
    StateFullName: text('StateFullName'),
    County: text('County'),
    Latitude: text('Latitude'),
    Longitude: text('Longitude'),
    TimeZone: text('TimeZone'),
    // Additional demographic fields available but not required for basic lookup
    PrimaryRecord: text('PrimaryRecord'),
    Population: text('Population'),
    HouseholdsPerZipCode: text('HouseholdsPerZipCode'),
    WhitePopulation: text('WhitePopulation'),
    BlackPopulation: text('BlackPopulation'),
    HispanicPopulation: text('HispanicPopulation'),
    AsianPopulation: text('AsianPopulation'),
    HawaiianPopulation: text('HawaiianPopulation'),
    IndianPopulation: text('IndianPopulation'),
    OtherPopulation: text('OtherPopulation'),
    MalePopulation: text('MalePopulation'),
    FemalePopulation: text('FemalePopulation'),
    PersonsPerHousehold: text('PersonsPerHousehold'),
    AverageHouseValue: text('AverageHouseValue'),
    IncomePerHousehold: text('IncomePerHousehold'),
    MedianAge: text('MedianAge'),
    MedianAgeMale: text('MedianAgeMale'),
    MedianAgeFemale: text('MedianAgeFemale'),
    Elevation: text('Elevation'),
    CityType: text('CityType'),
    CityAliasAbbreviation: text('CityAliasAbbreviation'),
    AreaCode: text('AreaCode'),
    CityAliasName: text('CityAliasName'),
    CountyFIPS: text('CountyFIPS'),
    StateFIPS: text('StateFIPS'),
    DayLightSaving: text('DayLightSaving'),
    MSA: text('MSA'),
    PMSA: text('PMSA'),
    CSA: text('CSA'),
    CBSA: text('CBSA'),
    CBSA_Div: text('CBSA_Div'),
    CBSA_Type: text('CBSA_Type'),
    CBSA_Name: text('CBSA_Name'),
    MSA_Name: text('MSA_Name'),
    PMSA_Name: text('PMSA_Name'),
    Region: text('Region'),
    Division: text('Division'),
    MailingName: text('MailingName'),
    NumberOfBusinesses: text('NumberOfBusinesses'),
    NumberOfEmployees: text('NumberOfEmployees'),
    BusinessFirstQuarterPayroll: text('BusinessFirstQuarterPayroll'),
    BusinessAnnualPayroll: text('BusinessAnnualPayroll'),
    BusinessEmploymentFlag: text('BusinessEmploymentFlag'),
    GrowthRank: text('GrowthRank'),
    GrowingCountiesA: text('GrowingCountiesA'),
    GrowingCountiesB: text('GrowingCountiesB'),
    GrowthIncreaseNumber: text('GrowthIncreaseNumber'),
    GrowthIncreasePercentage: text('GrowthIncreasePercentage'),
    CBSAPopulation: text('CBSAPopulation'),
    CBSADivisionPopulation: text('CBSADivisionPopulation'),
    CongressionalDistrict: text('CongressionalDistrict'),
    CongressionalLandArea: text('CongressionalLandArea'),
    DeliveryResidential: text('DeliveryResidential'),
    DeliveryBusiness: text('DeliveryBusiness'),
    DeliveryTotal: text('DeliveryTotal'),
    PreferredLastLineKey: text('PreferredLastLineKey'),
    ClassificationCode: text('ClassificationCode'),
    MultiCounty: text('MultiCounty'),
    CSAName: text('CSAName'),
    CBSA_Div_Name: text('CBSA_Div_Name'),
    CityStateKey: text('CityStateKey'),
    PopulationEstimate: text('PopulationEstimate'),
    LandArea: text('LandArea'),
    WaterArea: text('WaterArea'),
    CityAliasCode: text('CityAliasCode'),
    CityMixedCase: text('CityMixedCase'),
    CityAliasMixedCase: text('CityAliasMixedCase'),
    BoxCount: text('BoxCount'),
    SFDU: text('SFDU'),
    MFDU: text('MFDU'),
    StateANSI: text('StateANSI'),
    CountyANSI: text('CountyANSI'),
    ZIPIntroDate: text('ZIPIntroDate'),
    AliasIntroDate: text('AliasIntroDate'),
    FacilityCode: text('FacilityCode'),
    CityDeliveryIndicator: text('CityDeliveryIndicator'),
    CarrierRouteRateSortation: text('CarrierRouteRateSortation'),
    FinanceNumber: text('FinanceNumber'),
    UniqueZIPName: text('UniqueZIPName'),
    SSAStateCountyCode: text('SSAStateCountyCode'),
    MedicareCBSACode: text('MedicareCBSACode'),
    MedicareCBSAName: text('MedicareCBSAName'),
    MedicareCBSAType: text('MedicareCBSAType'),
    MarketRatingAreaID: text('MarketRatingAreaID'),
    CountyMixedCase: text('CountyMixedCase'),
});

// ============================================================================
// FEE SCHEDULE TABLES
// ============================================================================

/**
 * Fee Schedules table - Main fee schedule information
 */
export const feeSchedules = ediTebraSchema.table('fee_schedules', {
    id: uuid('id').primaryKey().defaultRandom(),
    clientId: integer('client_id').references(() => client.clientId),
    groupId: integer('group_id').references(() => group.id),
    practiceId: integer('practice_id').references(() => practice.id),
    name: varchar('name', { length: 255 }).notNull(),
    startDate: date('start_date'),
    feeSource: varchar('fee_source', { length: 50 }).notNull(),
    status: varchar('status', { length: 20 }).notNull(),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
    createdBy: uuid('created_by'),
    payerIds: text('payer_ids').array(),
    eClaimsCount: integer('e_claims_count'),
    paperClaimsCount: integer('paper_claims_count'),
    multiplier: numeric('multiplier', { precision: 10, scale: 2 }),
    masterRecord: boolean('master_record').default(false),
    payerTypes: text('payer_types').array(),
    payer_sort_ids: integer('payer_sort_ids').array(),
});

/**
 * Fee Schedule Procedures table - Individual procedure codes and fees
 */
export const feeScheduleProcedures = ediTebraSchema.table('fee_schedule_procedures', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull().references(() => feeSchedules.id, { onDelete: 'cascade' }),
    masterCodeId: bigint('master_code_id', { mode: 'number' }).references(() => masterCodes.id, { onDelete: 'set null' }),
    codeType: masterCodeTypeEnum('code_type').notNull(),
    code: varchar('code', { length: 20 }).notNull(),
    category: text('category'),
    description: text('description').notNull(),
    shortDescription: text('short_description'),
    modifier: varchar('modifier', { length: 10 }),
    placeOfService: varchar('place_of_service', { length: 10 }),
    baseRate: numeric('base_rate', { precision: 10, scale: 2 }),
    multiplier: numeric('multiplier', { precision: 10, scale: 2 }),
    units: integer('units').default(1),
    expectedFee: numeric('expected_fee', { precision: 10, scale: 2 }),
    yourFee: numeric('your_fee', { precision: 10, scale: 2 }).notNull(),
    medicareRate: numeric('medicare_rate', { precision: 10, scale: 2 }),
    providerNpi: varchar('provider_npi', { length: 500 }),
    exclude: boolean('exclude').default(false).notNull(),
    isCliaRequired: boolean('is_clia_required').default(false).notNull(),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),

}, (table) => ({
    feeScheduleCodeIdx: index('idx_fee_schedule_procedures_fee_schedule_code').on(table.feeScheduleId, table.code),
    feeScheduleCodeTypeCodeIdx: index('idx_fee_schedule_procedures_fee_schedule_type_code').on(table.feeScheduleId, table.codeType, table.code),
}));

/**
 * Master Codes table - Canonical master codes linked optionally to fee schedules
 */
export const masterCodes = ediTebraSchema.table('master_codes', {
    id: bigint('id', { mode: 'number' }).primaryKey().generatedAlwaysAsIdentity(),
    feeScheduleId: uuid('fee_schedule_id').references(() => feeSchedules.id, { onDelete: 'set null' }),
    codeType: masterCodeTypeEnum('code_type').notNull(),
    category: text('category'),
    code: varchar('code', { length: 50 }).notNull(),
    replacementCode: varchar('replacement_code', { length: 20 }),
    description: text('description').notNull(),
    shortDescription: text('short_description'),
    effectiveDate: date('effective_date'),
    endDate: date('end_date'),
    baseRate: numeric('base_rate', { precision: 18, scale: 2 }),
    status: boolean('status').default(true).notNull(),
    isFromRCM: boolean('is_from_rcm').default(false).notNull(),
}, (table) => ({
    feeScheduleCodeIdx: index('idx_master_codes_fee_schedule_code').on(table.feeScheduleId, table.code),
    feeScheduleCodeTypeCodeIdx: index('idx_master_codes_fee_schedule_type_code').on(table.feeScheduleId, table.codeType, table.code),
}));

/**
 * Fee Schedule Providers table - Links fee schedules to providers
 */
export const feeScheduleProviders = ediTebraSchema.table('fee_schedule_providers', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull(),
    providerId: uuid('provider_id').notNull(),
    createdAt: timestamp('created_at').defaultNow(),
});

/**
 * Fee Schedule Practices table - Links fee schedules to practices
 */
export const feeSchedulePractices = ediTebraSchema.table('fee_schedule_practices', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull(),
    practiceId: uuid('practice_id').notNull(),
    createdAt: timestamp('created_at').defaultNow(),
});

/**
 * Fee Schedule Carriers table - Links fee schedules to specific carriers
 */
export const feeScheduleCarriers = ediTebraSchema.table('fee_schedule_carriers', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull().references(() => feeSchedules.id, { onDelete: 'cascade' }),
    carrierId: uuid('payer_id').notNull(),
    carrierName: varchar('carrier_name', { length: 255 }),
    createdAt: timestamp('created_at').defaultNow(),
});

/**
 * Fee Schedule Carrier Types table - Links fee schedules to carrier types
 */
export const feeScheduleCarrierTypes = ediTebraSchema.table('fee_schedule_carrier_types', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull().references(() => feeSchedules.id, { onDelete: 'cascade' }),
    carrierType: varchar('carrier_type', { length: 50 }).notNull(),
    createdAt: timestamp('created_at').defaultNow(),
});

/**
 * Fee Schedule Groups table - Links fee schedules to groups
 */
export const feeScheduleGroups = ediTebraSchema.table('fee_schedule_groups', {
    id: uuid('id').primaryKey().defaultRandom(),
    feeScheduleId: uuid('fee_schedule_id').notNull().references(() => feeSchedules.id, { onDelete: 'cascade' }),
    groupId: integer('group_id').notNull(),
    groupName: varchar('group_name', { length: 255 }),
    createdAt: timestamp('created_at').defaultNow(),
});

// ============================================================================
// MEDICAL CODES TABLES
// ============================================================================

/**
 * CPT Codes table - Current Procedural Terminology codes
 */
export const cptCodes = ediTebraSchema.table('cpt_codes', {
    cptCode: varchar('cpt_code', { length: 5 }).primaryKey().notNull(),
    description: varchar('description', { length: 500 }),
    category: varchar('category', { length: 100 }),
    mueValue: integer('mue_value'),
    effectiveDate: varchar('effective_date', { length: 50 }),
    terminationDate: varchar('termination_date', { length: 50 }),
    isActive: boolean('is_active').default(true),
});

/**
 * ICD-10 CM Codes table - International Classification of Diseases codes
 */
export const icd10CmCodes = ediTebraSchema.table('icd10_cm_codes', {
    icd10Code: varchar('icd10_code', { length: 7 }).primaryKey().notNull(),
    description: varchar('description', { length: 500 }),
    category: varchar('category', { length: 100 }),
    isBillable: boolean('is_billable').default(true),
    effectiveDate: varchar('effective_date', { length: 50 }),
    terminationDate: varchar('termination_date', { length: 50 }),
    isActive: boolean('is_active').default(true),
});

/**
 * HCPCS Codes table - Healthcare Common Procedure Coding System codes
 */
export const hcpcsCodes = ediTebraSchema.table('hcpcs_codes', {
    hcpcsCode: varchar('hcpcs_code', { length: 5 }).primaryKey().notNull(),
    description: varchar('description', { length: 500 }),
    category: varchar('category', { length: 100 }),
    medicareOnly: boolean('medicare_only').default(false),
    effectiveDate: varchar('effective_date', { length: 50 }),
    terminationDate: varchar('termination_date', { length: 50 }),
    isActive: boolean('is_active').default(true),
});

/**
 * Revenue Codes table - Hospital billing revenue codes
 */
export const revenueCodes = ediTebraSchema.table('revenue_codes', {
    revenueCode: varchar('revenue_code', { length: 4 }).primaryKey().notNull(),
    description: varchar('description', { length: 500 }),
    category: varchar('category', { length: 100 }),
    requiresHcpcs: boolean('requires_hcpcs').default(false),
    effectiveDate: varchar('effective_date', { length: 50 }),
    terminationDate: varchar('termination_date', { length: 50 }),
    isActive: boolean('is_active').default(true),
});

/**
 * Modifiers table - Procedure code modifiers
 */
export const modifiers = ediTebraSchema.table('modifiers', {
    modifierCode: varchar('modifier_code', { length: 2 }).primaryKey().notNull(),
    description: varchar('description', { length: 255 }),
    modifierType: varchar('modifier_type', { length: 50 }),
    appliesTo: varchar('applies_to', { length: 20 }),
    effectiveDate: varchar('effective_date', { length: 50 }),
    terminationDate: varchar('termination_date', { length: 50 }),
    isActive: boolean('is_active').default(true),
});


/**
 * tags: medical_coding, ai, cache, persistence
 * intention:
 *     Cache a complete context-aware modifier answer so an identical claim context is
 *     served from the database instead of paying for a second LLM call.
 * business_logic:
 *     One row per distinct request context, keyed by a SHA-256 over the canonical
 *     (normalized, key-sorted, line-sorted) request. The key is deliberately NOT the
 *     procedure code and NOT the DCN: 99214 billed alone takes no modifier while the
 *     same code beside a same-day procedure may take 25, and a claim's lines can be
 *     edited after it is first scored. Keying on the data means an edited claim misses
 *     the cache and is re-answered, instead of being handed a stale result.
 *
 *     Holds only what the model returned — no seed data, no reference data. It fills
 *     itself on first use.
 * sample_io:
 *     Row: { contextHash: "9f2b…" (64 hex), request: { serviceLines: [...] },
 *            response: { lines: [{ lineNumber: 1, modifiers: [] }] },
 *            modelVersion: "gpt-4o-2024-11-20", generatedAt: <ts> }
 *     Edge cases:
 *       - context needs no modifiers -> row IS written, with empty per-line `modifiers`
 *         arrays. "No modifier needed" is a real cached answer, not a cache miss.
 *       - same context requested twice -> primary-key conflict; callers upsert
 *       - claim edited after scoring   -> different hash, cache miss, fresh answer
 */
export const claimModifierSuggestionRuns = ediTebraSchema.table('claim_modifier_suggestion_runs', {
    contextHash: char('context_hash', { length: 64 }).primaryKey().notNull(),
    request: jsonb('request').notNull(),
    response: jsonb('response').notNull(),
    modelVersion: text('model_version').notNull(),
    generatedAt: timestamp('generated_at', { withTimezone: true }).notNull().defaultNow(),
}, (table) => ({
    modelGeneratedIdx: index('claim_modifier_suggestion_runs_model_generated_idx')
        .on(table.modelVersion, table.generatedAt),
}));

// ============================================================================
// CARE MANAGEMENT TABLES
// ============================================================================

/**
 * Care Gaps table - Tracks care gaps for patients
 */
export const careGaps = ediTebraSchema.table('care_gaps', {
    careGapId: uuid('care_gap_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    status: varchar('status', { length: 100 }),
    gap: varchar('gap', { length: 255 }),
    gapDescription: text('gap_description'),
    openDate: date('open_date'),
    closedDate: date('closed_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Immunizations table - Tracks patient immunizations
 */
export const immunizations = ediTebraSchema.table('immunizations', {
    immunizationId: uuid('immunization_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    status: varchar('status', { length: 100 }),
    fields: varchar('fields', { length: 255 }),
    category: varchar('category', { length: 100 }),
    procedure: varchar('procedure', { length: 255 }),
    requirement: varchar('requirement', { length: 100 }),
    condition: varchar('condition', { length: 100 }),
    completionDate: date('completion_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Screenings table - Tracks patient screenings
 */
export const screenings = ediTebraSchema.table('screenings', {
    screeningId: uuid('screening_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    status: varchar('status', { length: 100 }),
    fields: varchar('fields', { length: 255 }),
    category: varchar('category', { length: 100 }),
    procedure: varchar('procedure', { length: 255 }),
    requirement: varchar('requirement', { length: 100 }),
    condition: varchar('condition', { length: 100 }),
    completionDate: date('completion_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Measurements table - Tracks patient measurements (weight, height, BMI)
 */
export const measurements = ediTebraSchema.table('measurements', {
    measurementId: uuid('measurement_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    weight: numeric('weight', { precision: 5, scale: 2 }),
    height: numeric('height', { precision: 5, scale: 2 }),
    bmi: numeric('bmi', { precision: 5, scale: 2 }),
    reportDate: date('report_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Monitoring table - Tracks patient care monitoring
 */
export const monitoring = ediTebraSchema.table('monitoring', {
    monitoringId: uuid('monitoring_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    status: varchar('status', { length: 100 }),
    careType: varchar('care_type', { length: 100 }),
    careName: varchar('care_name', { length: 255 }),
    diagnosis: varchar('diagnosis', { length: 255 }),
    onboardingDate: date('onboarding_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Diagnosis table - Tracks patient diagnoses with HCC risk information
 */
export const diagnosis = ediTebraSchema.table('diagnosis', {
    diagnosisId: uuid('diagnosis_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    status: varchar('status', { length: 100 }),
    hccRisk: varchar('hcc_risk', { length: 100 }),
    diagnosis: varchar('diagnosis', { length: 255 }),
    count: integer('count').default(0),
    date: date('date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

/**
 * Vitals table - Tracks patient vital signs
 */
export const vitals = ediTebraSchema.table('vitals', {
    vitalsId: uuid('vitals_id').primaryKey().defaultRandom(),
    patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId).notNull(),
    systolic: integer('systolic'),
    diastolic: integer('diastolic'),
    reportDate: date('report_date'),
    source: varchar('source', { length: 100 }),
    createdAt: timestamp('created_at').defaultNow().notNull(),
    updatedAt: timestamp('updated_at').defaultNow().notNull(),
});

// ============================================================================
// ML TRAINING DATA
// ============================================================================

/**
 * ML Training Data — one row per training sample per training run.
 * Stores the 22 engineered features + denial label exactly as passed to XGBoost.
 */
export const mlTrainingData = ediTebraSchema.table('ml_training_data', {
    id: serial('id').primaryKey(),
    trainingRunId: uuid('training_run_id').notNull(),
    trainingDate: timestamp('training_date', { withTimezone: true }).defaultNow().notNull(),
    dataSource: varchar('data_source', { length: 50 }).notNull(),
    remitId: varchar('remit_id', { length: 100 }),
    denialFlag: smallint('denial_flag').notNull(),

    // 24 engineered features (v6 — raw categorical values, no encoding)
    totalBilled: real('total_billed'),
    seniorFlag: smallint('senior_flag'),
    procDenialRate: real('proc_denial_rate'),
    diagDenialRate: real('diag_denial_rate'),
    cptCategory: varchar('cpt_category', { length: 30 }),
    placeOfService: varchar('place_of_service', { length: 10 }),
    patientBalanceFlag: smallint('patient_balance_flag'),
    hasSecondaryInsurance: smallint('has_secondary_insurance'),
    gender: varchar('gender', { length: 5 }),
    maritalStatus: varchar('marital_status', { length: 5 }),
    patientAge: real('patient_age'),
    numDiagnoses: smallint('num_diagnoses'),
    procDiagInteraction: real('proc_diag_interaction'),
    chargePerUnit: real('charge_per_unit'),
    hasModifier: smallint('has_modifier'),
    payerDenialRate: real('payer_denial_rate'),
    carcDataAvailable: smallint('carc_data_available'),
    numServiceLines: smallint('num_service_lines'),
    daysSinceService: real('days_since_service'),
    resubmissionFlag: smallint('resubmission_flag'),
    totalChargesBucket: smallint('total_charges_bucket'),
    subscriberRelationship: varchar('subscriber_relationship', { length: 20 }),
    serviceMonth: smallint('service_month'),
    serviceQuarter: smallint('service_quarter'),

    // CARC features (v7)
    carcCoAdjRatio: real("carc_co_adj_ratio"),
    carcPrAdjRatio: real("carc_pr_adj_ratio"),
    carcOaAdjRatio: real("carc_oa_adj_ratio"),
    carcGroupCo: real("carc_group_co"),
    carcGroupPr: real("carc_group_pr"),
    carcGroupOa: real("carc_group_oa"),
    carcGroupPi: real("carc_group_pi"),
    carcGroupCr: real("carc_group_cr"),
    carcNumGroups: real("carc_num_groups"),

    // Interaction features (v7)
    payerCptDenialRate: real("payer_cpt_denial_rate"),
    providerDenialRate: real("provider_denial_rate"),

    // Diagnosis features (v7)
    diagSpecificity: real("diag_specificity"),
    icd10Chapter: real("icd10_chapter"),

    // Temporal features (v7)
    dayOfWeek: real("day_of_week"),
    timelyFilingRisk: real("timely_filing_risk"),
    claimAgeDays: real("claim_age_days"),

    // Raw data columns for audit/reproducibility
    totalChargesRaw: real('total_charges_raw'),
    totalPaid: real('total_paid'),
    principalDiag: varchar('principal_diag', { length: 20 }),
    claimStatus: varchar('claim_status', { length: 50 }),
    procCode: varchar('proc_code', { length: 20 }),
    posCode: varchar('pos_code', { length: 10 }),
    modifier1: varchar('modifier1', { length: 10 }),
    units: real('units'),
    patDobRaw: varchar('pat_dob_raw', { length: 20 }),
    genderRaw: varchar('gender_raw', { length: 50 }),
    maritalStatusRaw: varchar('marital_status_raw', { length: 50 }),
    insuranceBalanceRaw: real('insurance_balance_raw'),
    patientBalanceRaw: real('patient_balance_raw'),
    lastPaymentDateRaw: varchar('last_payment_date_raw', { length: 30 }),
    hasSecondaryRaw: smallint('has_secondary_raw'),
    providerSpecialtyRaw: varchar('provider_specialty_raw', { length: 100 }),
    providerTypeRaw: varchar('provider_type_raw', { length: 100 }),
    carrierTypeRaw: varchar('carrier_type_raw', { length: 50 }),
    carrierName: varchar('carrier_name', { length: 500 }),
    patientState: varchar('patient_state', { length: 50 }),

    // CARC audit columns (flattened)
    numAdjustments: smallint('num_adjustments'),
    coAdjTotal: real('co_adj_total'),
    prAdjTotal: real('pr_adj_total'),
    oaAdjTotal: real('oa_adj_total'),
    carcCodes: text('carc_codes'),
    carcGroupCodes: text('carc_group_codes'),
    carcCategories: text('carc_categories'),
    primaryCarcCode: varchar('primary_carc_code', { length: 10 }),
    primaryCarcGroup: varchar('primary_carc_group', { length: 20 }),
    primaryCarcCategory: varchar('primary_carc_category', { length: 100 }),

    // FK to the model version this data was used to train
    modelVersionId: integer('model_version_id').references(() => mlModelVersions.id),
}, (table) => ({
    modelVersionIdIdx: index("idx_ml_training_data_model_version").on(table.modelVersionId),
    trainingRunIdIdx: index("idx_ml_training_data_run_id").on(table.trainingRunId),
    denialFlagIdx: index("idx_ml_training_data_denial_flag").on(table.denialFlag),
}));

// ============================================================================
// OUTBOUND FILES TABLE
// ============================================================================

/**
 * Outbound Files - Track generated claim submission files
 */
export const outboundFiles = ediTebraSchema.table('outbound_files', {
    id: uuid('id').primaryKey().defaultRandom(),
    fileName: text('file_name'),
    filePath: text('file_path'),
    claimSubmissionStatus: boolean('claim_submission_status').default(false),
    clearingHouseId: uuid('clearing_house_id').references(() => clearingHouses.id),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

export const outboundFileClaims = ediTebraSchema.table('outbound_file_claims', {
    id: uuid('id').primaryKey().defaultRandom(),
    outboundFileId: uuid('outbound_file_id').notNull().references(() => outboundFiles.id),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId),
    interchangeCtrlNum: text('interchange_ctrl_num'),
    functionalGroupNum: text('functional_group_num'),
    transactionSetCtrlNum: text('transaction_set_ctrl_num'),
    outTime: timestamp('out_time').defaultNow().notNull(),
    errorText: text('error_text'),
});

export const ediFileCtrlNum = ediTebraSchema.table('edi_file_ctrl_num', {
    id: serial('id').primaryKey().notNull(),
    clearingHouseId: uuid('clearing_house_id').notNull().references(() => clearingHouses.id),
    generatedFileCount: bigint('generated_file_count', { mode: 'number' }).default(0).notNull(),
    maxCount: bigint('max_count', { mode: 'number' }).default(999999999).notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow().notNull(),
}, (table) => ({
    clearingHouseIdUniqueIdx: uniqueIndex('edi_file_ctrl_num_clearing_house_id_uidx').on(table.clearingHouseId),
}));

export const outboundFileClaimsRelations = relations(outboundFileClaims, ({ one }) => ({
    outboundFile: one(outboundFiles, {
        fields: [outboundFileClaims.outboundFileId],
        references: [outboundFiles.id],
    }),
    claimHeader: one(claimHeader, {
        fields: [outboundFileClaims.claimId],
        references: [claimHeader.claimId],
    }),
}));

/**
 * Claim Resubmissions From 277 - Payer claim control numbers captured from
 * inbound 277 (claim status) responses, used as a fallback source for the
 * resubmission's REF*F8 payer claim control number when remit_clmhdr has no
 * matching 835 remittance yet (e.g. only a 277CA rejection has come back).
 */
export const claimResubmissionsFrom277 = ediTebraSchema.table('claim_resubmissions_from_277', {
    id: uuid('id').primaryKey().defaultRandom(),
    clearingHouseId: uuid('clearing_house_id').references(() => clearingHouses.id),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId),
    covType: varchar('cov_type', { length: 1 }),
    payerId: varchar('payer_id', { length: 500 }),
    payerClaimControlNumber: varchar('payer_claim_control_number', { length: 100 }),
    transmissionCorrelationId: varchar('transmission_correlation_id', { length: 100 }),
    createdAt: timestamp('created_at').default(sql`(CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago'::text)`).notNull(),
    updatedAt: timestamp('updated_at').default(sql`(CURRENT_TIMESTAMP AT TIME ZONE 'America/Chicago'::text)`).notNull(),
});

export const claimResubmissionsFrom277Relations = relations(claimResubmissionsFrom277, ({ one }) => ({
    clearingHouse: one(clearingHouses, {
        fields: [claimResubmissionsFrom277.clearingHouseId],
        references: [clearingHouses.id],
    }),
    claimHeader: one(claimHeader, {
        fields: [claimResubmissionsFrom277.claimId],
        references: [claimHeader.claimId],
    }),
}));

/**
 * Claim Processing History - Track claim processing events through clearing house
 * Records when claims are sent, responses received, and resubmissions
 */
export const claimProcessingHistory = ediTebraSchema.table('claim_processing_history', {
    id: uuid('id').primaryKey().defaultRandom(),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId),
    clearingHouseId: uuid('clearing_house_id').references(() => clearingHouses.id),
    inboundFileId: uuid('inbound_file_id').references(() => inboundFiles.id),
    outboundFileId: uuid('outbound_file_id').references(() => outboundFiles.id),
    processType: text('process_type').notNull(), // 'claim_ingested', 'sent_claim', 'receive_response', 'resubmit_claim'
    processDescription: text('process_description'),
    response: jsonb('response'),
    processedBy: varchar('processed_by', { length: 500 }),
    processedAt: timestamp('processed_at').defaultNow(),
});

/**
 * Prediction Outcomes - ML Model Prediction Results and Actual Claim Outcomes
 * Tracks XGBoost denial risk predictions and their corresponding actual claim outcomes
 * Used for model performance monitoring, feedback loop, and outcome analysis
 */
export const predictionOutcomes = ediTebraSchema.table('prediction_outcomes', {
    predictionId: uuid('prediction_id').primaryKey().notNull(),
    claimId: uuid('claim_id').references(() => claimHeader.claimId),
    serviceLineId: integer('service_line_id'),
    denialProbability: numeric('denial_probability', { precision: 10, scale: 8 }),
    riskClassification: riskClassificationEnum('risk_classification'),
    // FK: references ml_model_versions.version (application-level, not enforced by DB constraint)
    modelVersion: varchar('model_version', { length: 100 }),
    scoringTimestamp: timestamp('scoring_timestamp', { withTimezone: true }),
    featuresSnapshot: jsonb('features_snapshot'),
    actualOutcome: actualOutcomeEnum('actual_outcome'),
    outcomeTimestamp: timestamp('outcome_timestamp', { withTimezone: true }),
    adjustmentCodes: jsonb('adjustment_codes'),
    paidAmount: numeric('paid_amount', { precision: 12, scale: 2 }),
    denialReason: text('denial_reason'),
    suggestedAction: text('suggested_action'),
    riskFactors: text('risk_factors'),
    confidence: numeric('confidence', { precision: 10, scale: 8 }),
    fullResponse: jsonb('full_response'),
    predictedCategory: varchar('predicted_category', { length: 50 }),
    predictedCategoryProbabilities: jsonb('predicted_category_probabilities'),
    feedbackUsedInTraining: boolean('feedback_used_in_training').default(false),
    feedbackUsedInModelVersion: varchar('feedback_used_in_model_version', { length: 50 }),
    isExploration: boolean('is_exploration').default(false),
    wasModifiedPostPrediction: boolean('was_modified_post_prediction').default(false),
}, (table) => ({
    claimIdIdx: index("idx_prediction_outcomes_claim_id").on(table.claimId),
    modelVersionIdx: index("idx_prediction_outcomes_model_version").on(table.modelVersion),
    actualOutcomeIdx: index("idx_prediction_outcomes_actual_outcome").on(table.actualOutcome),
    feedbackIdx: index("idx_prediction_outcomes_feedback").on(table.feedbackUsedInTraining),
}));

/**
 * Action Notes table - Track action items for claims
 */
export const actionNotes = ediTebraSchema.table('action_notes', {
    id: uuid('id').primaryKey().defaultRandom(),
    actionNote: text('action_note').notNull(),
    actionPerform: text('action_perform').notNull(),
    description: text('description'),
    createdAt: timestamp('created_at'),
});

// ============================================================================
// EDI LOOKUP TABLES - 999 Acknowledgment Error Mapping
// ============================================================================

/**
 * EDI Segment Codes - Maps segment codes to human-readable names
 * Used to provide context when parsing 999 acknowledgments
 */
export const ediSegmentCodes = ediTebraSchema.table('edi_segment_codes', {
    id: uuid('id').primaryKey().defaultRandom(),
    segmentCode: varchar('segment_code', { length: 3 }).notNull().unique(),
    segmentName: varchar('segment_name', { length: 255 }).notNull(),
    description: text('description'),
    usedIn837: varchar('used_in_837', { length: 10 }).default('true'),
    usedIn999: varchar('used_in_999', { length: 10 }).default('false'),
    loopContext: varchar('loop_context', { length: 50 }),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

/**
 * EDI Error Codes - Maps error codes to human-readable messages
 * Used to enrich 999 acknowledgment error messages with context
 */
export const ediErrorCodes = ediTebraSchema.table('edi_error_codes', {
    id: uuid('id').primaryKey().defaultRandom(),
    errorCode: varchar('error_code', { length: 10 }).notNull().unique(),
    errorType: varchar('error_type', { length: 50 }),
    errorMessage: varchar('error_message', { length: 255 }).notNull(),
    userMessage: varchar('user_message', { length: 500 }),
    severity: varchar('severity', { length: 20 }).default('ERROR'),
    remediationHint: varchar('remediation_hint', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

/**
 * EDI Loop Codes - Maps loop identifiers to human-readable names
 * Used to provide context about where errors occur in the claim structure
 */
export const ediLoopCodes = ediTebraSchema.table('edi_loop_codes', {
    id: uuid('id').primaryKey().defaultRandom(),
    loopCode: varchar('loop_code', { length: 10 }).notNull().unique(),
    loopName: varchar('loop_name', { length: 255 }).notNull(),
    loopType: varchar('loop_type', { length: 50 }),
    loopContext: varchar('loop_context', { length: 100 }),
    description: text('description'),
    segmentContent: varchar('segment_content', { length: 500 }),
    createdAt: timestamp('created_at').defaultNow(),
    updatedAt: timestamp('updated_at').defaultNow(),
});

// ============================================================================
// ML/AI FEEDBACK LOOP TABLES
// ============================================================================

/**
 * ML Model Versions - Track every trained model version with metadata, metrics, and lineage
 * Each nightly training run creates a row. Used for model comparison, promotion, and audit trail.
 */
export const mlModelVersions = ediTebraSchema.table('ml_model_versions', {
    id: serial('id').primaryKey(),
    version: varchar('version', { length: 50 }).notNull().unique(),
    modelType: varchar('model_type', { length: 50 }).notNull().default('xgboost'),
    headType: varchar('head_type', { length: 50 }).notNull().default('binary'),
    trainingStartedAt: timestamp('training_started_at', { withTimezone: true }).notNull(),
    trainingCompletedAt: timestamp('training_completed_at', { withTimezone: true }),
    status: varchar('status', { length: 20 }).notNull().default('training'),
    trainingSampleCount: integer('training_sample_count').notNull(),
    featureCount: integer('feature_count').notNull(),
    featureList: jsonb('feature_list').notNull(),
    classDistribution: jsonb('class_distribution'),
    dataDateRange: jsonb('data_date_range'),
    metrics: jsonb('metrics').notNull(),
    validationMetrics: jsonb('validation_metrics'),
    comparisonToPrevious: jsonb('comparison_to_previous'),
    blobPathOnnx: varchar('blob_path_onnx', { length: 500 }),
    blobPathFeatures: varchar('blob_path_features', { length: 500 }),
    blobPathMetrics: varchar('blob_path_metrics', { length: 500 }),
    featurePsi: jsonb('feature_psi'),
    promotedAt: timestamp('promoted_at', { withTimezone: true }),
    retiredAt: timestamp('retired_at', { withTimezone: true }),
    retirementReason: text('retirement_reason'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow().notNull(),
});

/**
 * ML Training Run Summary — aggregate metadata per training run.
 * One row per run with class balance, feature stats, date ranges, split info.
 */
export const mlTrainingRunSummary = ediTebraSchema.table('ml_training_run_summary', {
    id: serial('id').primaryKey(),
    trainingRunId: uuid('training_run_id').notNull().unique(),
    modelVersionId: integer('model_version_id').references(() => mlModelVersions.id),
    totalSamples: integer('total_samples').notNull(),
    dbSamples: integer('db_samples').notNull().default(0),
    csvSamples: integer('csv_samples').notNull().default(0),
    denialCount: integer('denial_count').notNull(),
    nonDenialCount: integer('non_denial_count').notNull(),
    denialRate: numeric('denial_rate', { precision: 5, scale: 4 }).notNull(),
    featureCount: integer('feature_count').notNull(),
    featureList: jsonb('feature_list').notNull(),
    featureStats: jsonb('feature_stats'),
    dataDateRange: jsonb('data_date_range'),
    trainTestSplit: jsonb('train_test_split'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
});

/**
 * Remediation Actions - Track every remediation step taken on a claim
 * Records what was done, by whom, and the result. Links to predictions and claims.
 * Used for learning which actions work for which denial types.
 */
export const remediationActions = ediTebraSchema.table('remediation_actions', {
    id: serial('id').primaryKey(),
    claimId: uuid('claim_id').notNull().references(() => claimHeader.claimId),
    predictionId: uuid('prediction_id'),
    actionType: varchar('action_type', { length: 50 }).notNull(),
    actionDescription: text('action_description').notNull(),
    actionSource: varchar('action_source', { length: 20 }).notNull(),
    denialReasonCodes: jsonb('denial_reason_codes'),
    predictionRiskScore: numeric('prediction_risk_score', { precision: 5, scale: 4 }),
    predictedCategory: varchar('predicted_category', { length: 50 }),
    result: varchar('result', { length: 30 }),
    resultDescription: text('result_description'),
    resultTimestamp: timestamp('result_timestamp', { withTimezone: true }),
    postRemediationOutcome: varchar('post_remediation_outcome', { length: 50 }),
    postRemediationPaidAmount: numeric('post_remediation_paid_amount', { precision: 12, scale: 2 }),
    performedBy: uuid('performed_by'),
    performedAt: timestamp('performed_at', { withTimezone: true }).defaultNow().notNull(),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow().notNull(),
}, (table) => ({
    claimIdIdx: index("idx_remediation_claim").on(table.claimId),
    typeResultIdx: index("idx_remediation_type_result").on(table.actionType, table.result),
}));

/**
 * Remediation Knowledge Base - Stores successful remediation patterns as embeddings
 * Uses pgvector for semantic similarity search by the LLM.
 * Entries are auto-generated from successful remediation patterns and manually curated.
 */
export const remediationKnowledgeBase = ediTebraSchema.table('remediation_knowledge_base', {
    id: serial('id').primaryKey(),
    scenarioTitle: varchar('scenario_title', { length: 200 }).notNull(),
    scenarioDescription: text('scenario_description').notNull(),
    denialReasonCodes: jsonb('denial_reason_codes'),
    claimType: varchar('claim_type', { length: 10 }),
    payerCategory: varchar('payer_category', { length: 50 }),
    serviceCategory: varchar('service_category', { length: 50 }),
    resolutionSteps: jsonb('resolution_steps').notNull(),
    resolutionSummary: text('resolution_summary').notNull(),
    averageResolutionDays: numeric('average_resolution_days', { precision: 5, scale: 1 }),
    successRate: numeric('success_rate', { precision: 5, scale: 4 }),
    timesApplied: integer('times_applied').notNull().default(0),
    timesSuccessful: integer('times_successful').notNull().default(0),
    lastAppliedAt: timestamp('last_applied_at', { withTimezone: true }),
    scenarioEmbedding: jsonb('scenario_embedding'),
    resolutionEmbedding: jsonb('resolution_embedding'),
    source: varchar('source', { length: 20 }).notNull(),
    sourceClaimIds: jsonb('source_claim_ids'),
    status: varchar('status', { length: 20 }).notNull().default('active'),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow().notNull(),
});

/**
 * Check Level Audit History - Tracks changes to remittance checks
 * Records assignments, status changes, and audit trail for checks
 */
export const checkLevelAuditHistory = ediTebraSchema.table('check_level_audit_history', {
    id: uuid('id').primaryKey().defaultRandom(),
    checkHeaderId: integer('check_header_id').references(() => remitChkHdr.id),
    remitClmhdrId: integer('remit_clmhdr_id').references(() => remitClmHdr.id),
    currentUserId: uuid('current_user_id').references(() => users.id),
    previousUserId: uuid('previous_user_id').references(() => users.id),
    audit: text('audit'),
    description: text('description'),
    user_login: uuid('user_login').references(() => users.id),
    createdAt: varchar('created_at', { length: 500 }),
});

/**
 * Claim Edit Audit - Tracks individual field-level edits on claims
 * Links edits to predictions so the feedback loop can measure whether
 * pre-submission corrections improve outcomes.
 */
export const claimEditAudit = ediTebraSchema.table('claim_edit_audit', {
    id: uuid('id').primaryKey().defaultRandom(),
    claimId: varchar('claim_id', { length: 50 }).notNull(),
    predictionId: uuid('prediction_id'),
    fieldName: varchar('field_name', { length: 100 }).notNull(),
    oldValue: text('old_value'),
    newValue: text('new_value'),
    editedBy: uuid('edited_by').notNull().references(() => users.id),
    editedAt: timestamp('edited_at', { withTimezone: true }).defaultNow().notNull(),
    editSource: varchar('edit_source', { length: 50 }).default('manual'),
    clientId: integer('client_id').notNull(),
}, (table) => ({
    claimIdIdx: index("idx_claim_edit_audit_claim_id").on(table.claimId),
    editedAtIdx: index("idx_claim_edit_audit_edited_at").on(table.editedAt),
}));

// Check Level Process History - Tracks processing events for remittance checks
export const checkLevelProcessHistory = ediTebraSchema.table('check_level_process_history', {
    id: uuid('id').primaryKey().defaultRandom(),
    checkHeaderId: integer('check_header_id').references(() => remitChkHdr.id),
    remitClmhdrId: integer('remit_clmhdr_id').references(() => remitClmHdr.id),
    login: varchar('login', { length: 500 }),
    process: text('process'),
    description: text('description'),
    createdAt: varchar('created_at', { length: 500 }),
});

/**
 * Remediation Rules - Curated, application-grounded rules Timmy uses to suggest
 * fixes for denied claims. Industry-standard CARC rules, validation-rule mappings,
 * ML risk-factor mitigations, modifier guidance, and timely-filing matrices live here.
 *
 * Lookup is OR-overlap on the JSONB array trigger fields (carc_codes, group_codes,
 * validation_rule_ids, risk_factors, modifiers) using the JSONB ?| operator, which
 * uses the GIN indexes below. cpt_pattern / payer_pattern / claim_type are post-filtered
 * in JS.
 *
 * client_id NULL = platform-wide rule (applies to all clients). Non-null client_id =
 * client-specific override; lookup gives client overrides priority over platform rules.
 */
export const remediationRules = ediTebraSchema.table('remediation_rules', {
    id: uuid('id').primaryKey().defaultRandom(),

    // Identity
    ruleCode: varchar('rule_code', { length: 50 }).notNull().unique(),
    name: varchar('name', { length: 200 }).notNull(),
    category: varchar('category', { length: 30 }).notNull(),
    confidence: varchar('confidence', { length: 30 }).notNull(),

    // Trigger conditions (OR semantics across fields)
    carcCodes: jsonb('carc_codes').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    groupCodes: jsonb('group_codes').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    validationRuleIds: jsonb('validation_rule_ids').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    riskFactors: jsonb('risk_factors').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    modifiers: jsonb('modifiers').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    cptPattern: varchar('cpt_pattern', { length: 200 }),
    payerPattern: varchar('payer_pattern', { length: 200 }),
    claimType: varchar('claim_type', { length: 5 }),

    // Body
    diagnosis: text('diagnosis').notNull(),
    remediationSteps: jsonb('remediation_steps').$type<string[]>().notNull(),
    applicationFields: jsonb('application_fields').$type<Array<{
        table?: string;
        column?: string;
        uiPath?: string;
        validationRuleId?: string;
    }>>().default(sql`'[]'::jsonb`).notNull(),
    externalRefs: jsonb('external_refs').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    successIndicators: jsonb('success_indicators').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),
    caveats: jsonb('caveats').$type<string[]>().default(sql`'[]'::jsonb`).notNull(),

    // Lifecycle
    status: varchar('status', { length: 20 }).default('active').notNull(),
    priority: integer('priority').default(100).notNull(),
    version: integer('version').default(1).notNull(),
    publishedAt: timestamp('published_at', { withTimezone: true }),

    // Multi-tenancy: NULL = platform-wide; non-null = client-specific override
    clientId: integer('client_id').references(() => client.clientId, { onDelete: 'cascade' }),

    // Audit
    createdBy: uuid('created_by').notNull().references(() => users.id),
    updatedBy: uuid('updated_by').references(() => users.id),
    createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
    updatedAt: timestamp('updated_at', { withTimezone: true }).defaultNow().notNull(),
}, (table) => ({
    carcCodesGin: index('idx_remediation_rules_carc_gin').using('gin', table.carcCodes),
    groupCodesGin: index('idx_remediation_rules_group_gin').using('gin', table.groupCodes),
    validationIdsGin: index('idx_remediation_rules_val_gin').using('gin', table.validationRuleIds),
    riskFactorsGin: index('idx_remediation_rules_risk_gin').using('gin', table.riskFactors),
    modifiersGin: index('idx_remediation_rules_mod_gin').using('gin', table.modifiers),
    statusCategoryIdx: index('idx_remediation_rules_status_cat').on(table.status, table.category),
    clientIdx: index('idx_remediation_rules_client').on(table.clientId),
}));

/**
 * Remediation Rule Changes - Field-level audit log for rule edits.
 * Mirrors the claim_edit_audit pattern. One row per changed field per save.
 */
export const remediationRuleChanges = ediTebraSchema.table('remediation_rule_changes', {
    id: uuid('id').primaryKey().defaultRandom(),
    ruleId: uuid('rule_id').notNull().references(() => remediationRules.id, { onDelete: 'cascade' }),
    fieldName: varchar('field_name', { length: 100 }).notNull(),
    oldValue: text('old_value'),
    newValue: text('new_value'),
    changeType: varchar('change_type', { length: 20 }).notNull(),
    changedBy: uuid('changed_by').notNull().references(() => users.id),
    changedAt: timestamp('changed_at', { withTimezone: true }).defaultNow().notNull(),
}, (table) => ({
    ruleIdIdx: index('idx_rule_changes_rule_id').on(table.ruleId),
    changedAtIdx: index('idx_rule_changes_changed_at').on(table.changedAt),
}));