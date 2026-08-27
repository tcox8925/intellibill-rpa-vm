import { sql } from "drizzle-orm";
import {
	boolean,
	pgTable,
	serial,
	char,
	text,
	timestamp,
	uuid,
	varchar,
	pgSchema,
	jsonb,
	integer,
} from "drizzle-orm/pg-core";

export const rcmSchema = pgSchema("rcm");

// Common column definitions shared between rcm and carriers schemas
const practicesRawColumns = {
	Serial: serial("Serial").notNull(),
	PracticeID: text("PracticeID").notNull().primaryKey(),
	PracticeName: text("PracticeName"),
};

const patientsRawColumns = {
	id: serial("id").primaryKey(),
	AddressLine1: text("AddressLine1"),
	AddressLine2: text("AddressLine2"),
	Adjustments: text("Adjustments"),
	Age: text("Age"),
	AlertMessage: text("AlertMessage"),
	AlertShowWhenDisplayingPatientDetails: text("AlertShowWhenDisplayingPatientDetails"),
	AlertShowWhenEnteringEncounters: text("AlertShowWhenEnteringEncounters"),
	AlertShowWhenPostingPayments: text("AlertShowWhenPostingPayments"),
	AlertShowWhenPreparingPatientStatements: text("AlertShowWhenPreparingPatientStatements"),
	AlertShowWhenSchedulingAppointments: text("AlertShowWhenSchedulingAppointments"),
	AlertShowWhenViewingClaimDetails: text("AlertShowWhenViewingClaimDetails"),
	Cases: text("Cases"),
	Charges: text("Charges"),
	City: text("City"),
	CollectionCategoryName: text("CollectionCategoryName"),
	Country: text("Country"),
	CreatedDate: text("CreatedDate"),
	DOB: text("DOB"),
	DefaultCaseConditionRelatedToAbuse: text("DefaultCaseConditionRelatedToAbuse"),
	DefaultCaseConditionRelatedToAutoAccident: text("DefaultCaseConditionRelatedToAutoAccident"),
	DefaultCaseConditionRelatedToAutoAccidentState: text("DefaultCaseConditionRelatedToAutoAccidentState"),
	DefaultCaseConditionRelatedToEPSDT: text("DefaultCaseConditionRelatedToEPSDT"),
	DefaultCaseConditionRelatedToEmergency: text("DefaultCaseConditionRelatedToEmergency"),
	DefaultCaseConditionRelatedToEmployment: text("DefaultCaseConditionRelatedToEmployment"),
	DefaultCaseConditionRelatedToFamilyPlanning: text("DefaultCaseConditionRelatedToFamilyPlanning"),
	DefaultCaseConditionRelatedToOther: text("DefaultCaseConditionRelatedToOther"),
	DefaultCaseConditionRelatedToPregnancy: text("DefaultCaseConditionRelatedToPregnancy"),
	DefaultCaseDatesAccidentDate: text("DefaultCaseDatesAccidentDate"),
	DefaultCaseDatesAcuteManifestationDate: text("DefaultCaseDatesAcuteManifestationDate"),
	DefaultCaseDatesInjuryEndDate: text("DefaultCaseDatesInjuryEndDate"),
	DefaultCaseDatesInjuryStartDate: text("DefaultCaseDatesInjuryStartDate"),
	DefaultCaseDatesLastMenstrualPeriodDate: text("DefaultCaseDatesLastMenstrualPeriodDate"),
	DefaultCaseDatesLastSeenDate: text("DefaultCaseDatesLastSeenDate"),
	DefaultCaseDatesLastXRayDate: text("DefaultCaseDatesLastXRayDate"),
	DefaultCaseDatesReferralDate: text("DefaultCaseDatesReferralDate"),
	DefaultCaseDatesRelatedDisabilityEndDate: text("DefaultCaseDatesRelatedDisabilityEndDate"),
	DefaultCaseDatesRelatedDisabilityStartDate: text("DefaultCaseDatesRelatedDisabilityStartDate"),
	DefaultCaseDatesRelatedHospitalizationEndDate: text("DefaultCaseDatesRelatedHospitalizationEndDate"),
	DefaultCaseDatesRelatedHospitalizationStartDate: text("DefaultCaseDatesRelatedHospitalizationStartDate"),
	DefaultCaseDatesSameOrSimilarIllnessEndDate: text("DefaultCaseDatesSameOrSimilarIllnessEndDate"),
	DefaultCaseDatesSameOrSimilarIllnessStartDate: text("DefaultCaseDatesSameOrSimilarIllnessStartDate"),
	DefaultCaseDatesUnableToWorkEndDate: text("DefaultCaseDatesUnableToWorkEndDate"),
	DefaultCaseDatesUnableToWorkStartDate: text("DefaultCaseDatesUnableToWorkStartDate"),
	DefaultCaseDescription: text("DefaultCaseDescription"),
	DefaultCaseID: text("DefaultCaseID"),
	DefaultCaseName: text("DefaultCaseName"),
	DefaultCasePayerScenario: text("DefaultCasePayerScenario"),
	DefaultCaseReferringProviderFullName: text("DefaultCaseReferringProviderFullName"),
	DefaultCaseReferringProviderID: text("DefaultCaseReferringProviderID"),
	DefaultCaseSendPatientStatements: text("DefaultCaseSendPatientStatements"),
	DefaultRenderingProviderFullName: text("DefaultRenderingProviderFullName"),
	DefaultRenderingProviderId: text("DefaultRenderingProviderId"),
	DefaultServiceLocationBillingName: text("DefaultServiceLocationBillingName"),
	DefaultServiceLocationFaxPhone: text("DefaultServiceLocationFaxPhone"),
	DefaultServiceLocationFaxPhoneExt: text("DefaultServiceLocationFaxPhoneExt"),
	DefaultServiceLocationId: text("DefaultServiceLocationId"),
	DefaultServiceLocationName: text("DefaultServiceLocationName"),
	DefaultServiceLocationNameAddressLine1: text("DefaultServiceLocationNameAddressLine1"),
	DefaultServiceLocationNameAddressLine2: text("DefaultServiceLocationNameAddressLine2"),
	DefaultServiceLocationNameCity: text("DefaultServiceLocationNameCity"),
	DefaultServiceLocationNameCountry: text("DefaultServiceLocationNameCountry"),
	DefaultServiceLocationNameState: text("DefaultServiceLocationNameState"),
	DefaultServiceLocationNameZipCode: text("DefaultServiceLocationNameZipCode"),
	DefaultServiceLocationPhone: text("DefaultServiceLocationPhone"),
	DefaultServiceLocationPhoneExt: text("DefaultServiceLocationPhoneExt"),
	EmailAddress: text("EmailAddress"),
	EmergencyName: text("EmergencyName"),
	EmergencyPhone: text("EmergencyPhone"),
	EmergencyPhoneExt: text("EmergencyPhoneExt"),
	EmployerName: text("EmployerName"),
	EmploymentStatus: text("EmploymentStatus"),
	FirstName: text("FirstName"),
	Gender: text("Gender"),
	GuarantorDifferentThanPatient: text("GuarantorDifferentThanPatient"),
	GuarantorFirstName: text("GuarantorFirstName"),
	GuarantorLastName: text("GuarantorLastName"),
	GuarantorMiddleName: text("GuarantorMiddleName"),
	GuarantorPrefix: text("GuarantorPrefix"),
	GuarantorSuffix: text("GuarantorSuffix"),
	HomePhone: text("HomePhone"),
	HomePhoneExt: text("HomePhoneExt"),
	ID: text("ID"),
	InsuranceBalance: text("InsuranceBalance"),
	InsurancePayments: text("InsurancePayments"),
	LastAppointmentDate: text("LastAppointmentDate"),
	LastDiagnosis: text("LastDiagnosis"),
	LastEncounterDate: text("LastEncounterDate"),
	LastModifiedDate: text("LastModifiedDate"),
	LastName: text("LastName"),
	LastPaymentDate: text("LastPaymentDate"),
	LastStatementDate: text("LastStatementDate"),
	MaritalStatus: text("MaritalStatus"),
	MedicalRecordNumber: text("MedicalRecordNumber"),
	MiddleName: text("MiddleName"),
	MobilePhone: text("MobilePhone"),
	MobilePhoneExt: text("MobilePhoneExt"),
	MostRecentNote1Date: text("MostRecentNote1Date"),
	MostRecentNote1Message: text("MostRecentNote1Message"),
	MostRecentNote1User: text("MostRecentNote1User"),
	MostRecentNote2Date: text("MostRecentNote2Date"),
	MostRecentNote2Message: text("MostRecentNote2Message"),
	MostRecentNote2User: text("MostRecentNote2User"),
	MostRecentNote3Date: text("MostRecentNote3Date"),
	MostRecentNote3Message: text("MostRecentNote3Message"),
	MostRecentNote3User: text("MostRecentNote3User"),
	MostRecentNote4Date: text("MostRecentNote4Date"),
	MostRecentNote4Message: text("MostRecentNote4Message"),
	MostRecentNote4User: text("MostRecentNote4User"),
	PatientBalance: text("PatientBalance"),
	PatientFullName: text("PatientFullName"),
	PatientPayments: text("PatientPayments"),
	PracticeId: text("PracticeId"),
	PracticeName: text("PracticeName"),
	Prefix: text("Prefix"),
	PrimaryCarePhysicianFullName: text("PrimaryCarePhysicianFullName"),
	PrimaryCarePhysicianId: text("PrimaryCarePhysicianId"),
	PrimaryInsurancePolicyCompanyID: text("PrimaryInsurancePolicyCompanyID"),
	PrimaryInsurancePolicyCompanyName: text("PrimaryInsurancePolicyCompanyName"),
	PrimaryInsurancePolicyCopay: text("PrimaryInsurancePolicyCopay"),
	PrimaryInsurancePolicyDeductible: text("PrimaryInsurancePolicyDeductible"),
	PrimaryInsurancePolicyEffectiveEndDate: text("PrimaryInsurancePolicyEffectiveEndDate"),
	PrimaryInsurancePolicyEffectiveStartDate: text("PrimaryInsurancePolicyEffectiveStartDate"),
	PrimaryInsurancePolicyGroupNumber: text("PrimaryInsurancePolicyGroupNumber"),
	PrimaryInsurancePolicyInsuredAddressLine1: text("PrimaryInsurancePolicyInsuredAddressLine1"),
	PrimaryInsurancePolicyInsuredAddressLine2: text("PrimaryInsurancePolicyInsuredAddressLine2"),
	PrimaryInsurancePolicyInsuredCity: text("PrimaryInsurancePolicyInsuredCity"),
	PrimaryInsurancePolicyInsuredCountry: text("PrimaryInsurancePolicyInsuredCountry"),
	PrimaryInsurancePolicyInsuredDateOfBirth: text("PrimaryInsurancePolicyInsuredDateOfBirth"),
	PrimaryInsurancePolicyInsuredFullName: text("PrimaryInsurancePolicyInsuredFullName"),
	PrimaryInsurancePolicyInsuredGender: text("PrimaryInsurancePolicyInsuredGender"),
	PrimaryInsurancePolicyInsuredIDNumber: text("PrimaryInsurancePolicyInsuredIDNumber"),
	PrimaryInsurancePolicyInsuredNotes: text("PrimaryInsurancePolicyInsuredNotes"),
	PrimaryInsurancePolicyInsuredSocialSecurityNumber: text("PrimaryInsurancePolicyInsuredSocialSecurityNumber"),
	PrimaryInsurancePolicyInsuredState: text("PrimaryInsurancePolicyInsuredState"),
	PrimaryInsurancePolicyInsuredZipCode: text("PrimaryInsurancePolicyInsuredZipCode"),
	PrimaryInsurancePolicyNumber: text("PrimaryInsurancePolicyNumber"),
	PrimaryInsurancePolicyPatientRelationshipToInsured: text("PrimaryInsurancePolicyPatientRelationshipToInsured"),
	PrimaryInsurancePolicyPlanAddressLine1: text("PrimaryInsurancePolicyPlanAddressLine1"),
	PrimaryInsurancePolicyPlanAddressLine2: text("PrimaryInsurancePolicyPlanAddressLine2"),
	PrimaryInsurancePolicyPlanAdjusterFullName: text("PrimaryInsurancePolicyPlanAdjusterFullName"),
	PrimaryInsurancePolicyPlanCity: text("PrimaryInsurancePolicyPlanCity"),
	PrimaryInsurancePolicyPlanCountry: text("PrimaryInsurancePolicyPlanCountry"),
	PrimaryInsurancePolicyPlanFaxNumber: text("PrimaryInsurancePolicyPlanFaxNumber"),
	PrimaryInsurancePolicyPlanFaxNumberExt: text("PrimaryInsurancePolicyPlanFaxNumberExt"),
	PrimaryInsurancePolicyPlanID: text("PrimaryInsurancePolicyPlanID"),
	PrimaryInsurancePolicyPlanName: text("PrimaryInsurancePolicyPlanName"),
	PrimaryInsurancePolicyPlanPhoneNumber: text("PrimaryInsurancePolicyPlanPhoneNumber"),
	PrimaryInsurancePolicyPlanPhoneNumberExt: text("PrimaryInsurancePolicyPlanPhoneNumberExt"),
	PrimaryInsurancePolicyPlanState: text("PrimaryInsurancePolicyPlanState"),
	PrimaryInsurancePolicyPlanZipCode: text("PrimaryInsurancePolicyPlanZipCode"),
	ReferralSource: text("ReferralSource"),
	ReferringProviderFullName: text("ReferringProviderFullName"),
	ReferringProviderId: text("ReferringProviderId"),
	SSN: text("SSN"),
	SecondaryInsurancePolicyCompanyID: text("SecondaryInsurancePolicyCompanyID"),
	SecondaryInsurancePolicyCompanyName: text("SecondaryInsurancePolicyCompanyName"),
	SecondaryInsurancePolicyCopay: text("SecondaryInsurancePolicyCopay"),
	SecondaryInsurancePolicyDeductible: text("SecondaryInsurancePolicyDeductible"),
	SecondaryInsurancePolicyEffectiveEndDate: text("SecondaryInsurancePolicyEffectiveEndDate"),
	SecondaryInsurancePolicyEffectiveStartDate: text("SecondaryInsurancePolicyEffectiveStartDate"),
	SecondaryInsurancePolicyGroupNumber: text("SecondaryInsurancePolicyGroupNumber"),
	SecondaryInsurancePolicyInsuredAddressLine1: text("SecondaryInsurancePolicyInsuredAddressLine1"),
	SecondaryInsurancePolicyInsuredAddressLine2: text("SecondaryInsurancePolicyInsuredAddressLine2"),
	SecondaryInsurancePolicyInsuredCity: text("SecondaryInsurancePolicyInsuredCity"),
	SecondaryInsurancePolicyInsuredCountry: text("SecondaryInsurancePolicyInsuredCountry"),
	SecondaryInsurancePolicyInsuredDateOfBirth: text("SecondaryInsurancePolicyInsuredDateOfBirth"),
	SecondaryInsurancePolicyInsuredFullName: text("SecondaryInsurancePolicyInsuredFullName"),
	SecondaryInsurancePolicyInsuredGender: text("SecondaryInsurancePolicyInsuredGender"),
	SecondaryInsurancePolicyInsuredIDNumber: text("SecondaryInsurancePolicyInsuredIDNumber"),
	SecondaryInsurancePolicyInsuredNotes: text("SecondaryInsurancePolicyInsuredNotes"),
	SecondaryInsurancePolicyInsuredSocialSecurityNumber: text("SecondaryInsurancePolicyInsuredSocialSecurityNumber"),
	SecondaryInsurancePolicyInsuredState: text("SecondaryInsurancePolicyInsuredState"),
	SecondaryInsurancePolicyInsuredZipCode: text("SecondaryInsurancePolicyInsuredZipCode"),
	SecondaryInsurancePolicyNumber: text("SecondaryInsurancePolicyNumber"),
	SecondaryInsurancePolicyPatientRelationshipToInsured: text("SecondaryInsurancePolicyPatientRelationshipToInsured"),
	SecondaryInsurancePolicyPlanAddressLine1: text("SecondaryInsurancePolicyPlanAddressLine1"),
	SecondaryInsurancePolicyPlanAddressLine2: text("SecondaryInsurancePolicyPlanAddressLine2"),
	SecondaryInsurancePolicyPlanAdjusterFullName: text("SecondaryInsurancePolicyPlanAdjusterFullName"),
	SecondaryInsurancePolicyPlanCity: text("SecondaryInsurancePolicyPlanCity"),
	SecondaryInsurancePolicyPlanCountry: text("SecondaryInsurancePolicyPlanCountry"),
	SecondaryInsurancePolicyPlanFaxNumber: text("SecondaryInsurancePolicyPlanFaxNumber"),
	SecondaryInsurancePolicyPlanFaxNumberExt: text("SecondaryInsurancePolicyPlanFaxNumberExt"),
	SecondaryInsurancePolicyPlanID: text("SecondaryInsurancePolicyPlanID"),
	SecondaryInsurancePolicyPlanName: text("SecondaryInsurancePolicyPlanName"),
	SecondaryInsurancePolicyPlanPhoneNumber: text("SecondaryInsurancePolicyPlanPhoneNumber"),
	SecondaryInsurancePolicyPlanPhoneNumberExt: text("SecondaryInsurancePolicyPlanPhoneNumberExt"),
	SecondaryInsurancePolicyPlanState: text("SecondaryInsurancePolicyPlanState"),
	SecondaryInsurancePolicyPlanZipCode: text("SecondaryInsurancePolicyPlanZipCode"),
	State: text("State"),
	StatementNote: text("StatementNote"),
	Suffix: text("Suffix"),
	TotalBalance: text("TotalBalance"),
	WorkPhone: text("WorkPhone"),
	WorkPhoneExt: text("WorkPhoneExt"),
	ZipCode: text("ZipCode"),
	uuid: uuid("uuid").defaultRandom(),
}

const serviceLocationsColumns = {
	id: serial("id").primaryKey(),
	uuid: uuid("uuid"),
	AddressLine1: varchar("AddressLine1", { length: 255 }),
	AddressLine2: varchar("AddressLine2", { length: 255 }),
	BillingName: varchar("BillingName", { length: 255 }),
	CLIANumber: varchar("CLIANumber", { length: 100 }),
	City: varchar("City", { length: 100 }),
	Country: varchar("Country", { length: 100 }),
	CreatedDate: timestamp("CreatedDate"),
	FacilityIDType: varchar("FacilityIDType", { length: 100 }),
	FaxPhone: varchar("FaxPhone", { length: 50 }),
	FaxPhoneExt: varchar("FaxPhoneExt", { length: 20 }),
	HCFABox32FacilityID: varchar("HCFABox32FacilityID", { length: 100 }),
	ID: varchar("ID", { length: 100 }),
	ModifiedDate: timestamp("ModifiedDate"),
	NPI: varchar("NPI", { length: 50 }),
	Name: varchar("Name", { length: 255 }),
	Phone: varchar("Phone", { length: 50 }),
	PhoneExt: varchar("PhoneExt", { length: 20 }),
	PlaceOfService: varchar("PlaceOfService", { length: 100 }),
	PracticeID: varchar("PracticeID", { length: 100 }),
	PracticeName: varchar("PracticeName", { length: 255 }),
	State: varchar("State", { length: 50 }),
	ZipCode: varchar("ZipCode", { length: 20 }),
};

const batchesRawColumns = {
	id: serial("id").primaryKey(),
	encounterId: text("encounter_id"),
	status: varchar("status", { length: 50 }),
	createdAt: timestamp("created_at"),
};

const batchesColumns = {
	id: serial("id").primaryKey(),
	encounterId: char("encounterid", { length: 36 }),
	status: varchar("status", { length: 50 }),
	patientId: char("patientid", { length: 36 }),
	createdDate: timestamp("createddate"),
	batchId: text("batchid"),
};

const claimsAssignmentsColumns = {
	ID: serial("ID").primaryKey(),
	claimID: text("claimID").unique(),
	userid: uuid("userid"),
};

const claimAssessmentNotesColumns = {
	id: uuid("id").defaultRandom().primaryKey(),
	encounter_id: text("encounter_id").notNull(),
	user_login: varchar("user_login", { length: 200 }).notNull(),
	note_text: text("note_text").notNull(),
	note_type: varchar("note_type", { length: 250 }),
	created_at: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	updated_at: timestamp("updated_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
};

const claimAuditHistoryColumns = {
	id: uuid("id").defaultRandom().primaryKey(),
	encounter_id: text("encounter_id").notNull(),
	action_type: varchar("action_type", { length: 100 }).notNull(),
	action_description: text("action_description").notNull(),
	old_value: text("old_value"),
	new_value: text("new_value"),
	user_login: varchar("user_login", { length: 200 }),
	user_name: varchar("user_name", { length: 255 }),
	metadata: jsonb("metadata"),
	created_at: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
};

// Schema for practicesRaw and clients are Same
export const practicesRaw = rcmSchema.table("practices_raw", practicesRawColumns);

// Example schema - you can modify this based on your needs
export const users = rcmSchema.table("users", {
	id: uuid("id").defaultRandom().primaryKey(),
	email: varchar("email", { length: 255 }).notNull().unique(),
	name: varchar("name", { length: 255 }).notNull(),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	updatedAt: timestamp("updated_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	login: varchar("login", { length: 255 }).notNull(),
	password: varchar("password", { length: 255 }).notNull(),
	role: varchar("role", { length: 50 }).notNull(),
	firstName: varchar("first_name", { length: 255 }),
	lastName: varchar("last_name", { length: 255 }),
	contact: varchar("contact", { length: 20 }),
	active: boolean("active").default(true),
	dataAccess: boolean("data_access").default(false),
	dashboardPermissionIds: integer("dashboard_permission_ids")
		.array()
		.default(sql`ARRAY[]::integer[]`)
		.notNull(),
	affiliations: jsonb("affiliations")
		.default(sql`'{}'::jsonb`)
		.notNull(),
	updatedBy: varchar("updated_by", { length: 250 }),
	defaultEntityId: integer("default_entity_id"),
	logo: text("logo"),
});

export const dashboardDefinitions = rcmSchema.table("dashboard_definitions", {
	id: serial("id").primaryKey(),
	code: text("code").notNull().unique(),
	label: text("label").notNull(),
	parentId: integer("parent_id").references((): any => dashboardDefinitions.id, {
		onDelete: "cascade",
	}),
	sortOrder: integer("sort_order").notNull().default(0),
	isActive: boolean("is_active").notNull().default(true),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	updatedAt: timestamp("updated_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
});


// Schema for service locations and Clinics are Same
export const serviceLocations = rcmSchema.table("service_locations_raw", serviceLocationsColumns);

export const tokens = rcmSchema.table("tokens", {
	id: uuid("id").defaultRandom().primaryKey(),
	userId: uuid("user_id")
		.notNull()
		.references(() => users.id, { onDelete: "cascade" }),
	accessToken: varchar("access_token", { length: 500 }).notNull(),
	refreshToken: varchar("refresh_token", { length: 500 }).notNull(),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	updatedAt: timestamp("updated_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
});

// Login logs table for tracking user sessions
export const loginLogs = rcmSchema.table("login_logs", {
	id: uuid("id").defaultRandom().primaryKey(),
	userId: uuid("user_id")
		.notNull()
		.references(() => users.id, { onDelete: "cascade" }),
	sessionId: varchar("session_id", { length: 255 }).notNull(),
	loginTime: timestamp("login_time").default(sql`CURRENT_TIMESTAMP`).notNull(),
	logoutTime: timestamp("logout_time"),
	ipAddress: varchar("ip_address", { length: 45 }),
	userAgent: text("user_agent"),
});

// Password reset tokens table for forgot password functionality
export const passwordResetTokens = rcmSchema.table("password_reset_tokens", {
	id: uuid("id").defaultRandom().primaryKey(),
	userId: uuid("user_id")
		.notNull()
		.references(() => users.id, { onDelete: "cascade" }),
	token: varchar("token", { length: 500 }).notNull().unique(),
	expiresAt: timestamp("expires_at").notNull(),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	usedAt: timestamp("used_at"),
});

// Page visits table for tracking user navigation
export const pageVisits = rcmSchema.table("page_visits", {
	id: uuid("id").defaultRandom().primaryKey(),
	sessionId: varchar("session_id", { length: 255 }).notNull(),
	userId: uuid("user_id")
		.notNull()
		.references(() => users.id, { onDelete: "cascade" }),
	pageUrl: varchar("page_url", { length: 500 }).notNull(),
	pageName: varchar("page_name", { length: 255 }).notNull(),
	visitTime: timestamp("visit_time").default(sql`CURRENT_TIMESTAMP`).notNull(),
	duration: integer("duration"), // duration in seconds
});

// Health check table
export const healthCheck = pgTable("health_check", {
	id: serial("id").primaryKey(),
	status: varchar("status", { length: 50 }).default("ok"),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
});


// Schema for batches raw
export const batchesRaw = rcmSchema.table("batches_raw", batchesRawColumns);

// Schema for batches
export const batches = rcmSchema.table("batches", batchesColumns);

export const chargesRawColumns = {
	id: serial("id").notNull().primaryKey(),
	AdjustedCharges: text("AdjustedCharges"),
	AllowedAmount: text("AllowedAmount"),
	AppointmentID: text("AppointmentID"),
	BatchNumber: text("BatchNumber"),
	BilledTo: text("BilledTo"),
	CaseName: text("CaseName"),
	CasePayerScenario: text("CasePayerScenario"),
	CopayAmount: text("CopayAmount"),
	CopayCategory: text("CopayCategory"),
	CopayMethod: text("CopayMethod"),
	CopayReference: text("CopayReference"),
	CreatedDate: text("CreatedDate"),
	DoNotSendClaimElectronically: text("DoNotSendClaimElectronically"),
	DoNotSendElectronicallyToSecondary: text("DoNotSendElectronicallyToSecondary"),
	EClaimNote: text("EClaimNote"),
	EClaimNoteType: text("EClaimNoteType"),
	EncounterDiagnosisID1: text("EncounterDiagnosisID1"),
	EncounterDiagnosisID2: text("EncounterDiagnosisID2"),
	EncounterDiagnosisID3: text("EncounterDiagnosisID3"),
	EncounterDiagnosisID4: text("EncounterDiagnosisID4"),
	EncounterID: text("EncounterID"),
	EncounterProcedureID: text("EncounterProcedureID"),
	EncounterStatus: text("EncounterStatus"),
	ExpectedAmount: text("ExpectedAmount"),
	HospitalizationEndDate: text("HospitalizationEndDate"),
	HospitalizationStartDate: text("HospitalizationStartDate"),
	ID: text("ID"),
	InsuranceBalance: text("InsuranceBalance"),
	LastModifiedDate: text("LastModifiedDate"),
	LineNote: text("LineNote"),
	LocalUseBox10d: text("LocalUseBox10d"),
	LocalUseBox19: text("LocalUseBox19"),
	Minutes: text("Minutes"),
	OtherAdjustment: text("OtherAdjustment"),
	OtherPaymentAmount: text("OtherPaymentAmount"),
	OtherPaymentCategoryDesc: text("OtherPaymentCategoryDesc"),
	OtherPaymentID: text("OtherPaymentID"),
	OtherPaymentMethodDesc: text("OtherPaymentMethodDesc"),
	OtherPaymentPostingDate: text("OtherPaymentPostingDate"),
	OtherPaymentRef: text("OtherPaymentRef"),
	PatientBalance: text("PatientBalance"),
	PatientBatchID: text("PatientBatchID"),
	PatientDateOfBirth: text("PatientDateOfBirth"),
	PatientFirstBillDate: text("PatientFirstBillDate"),
	PatientFirstName: text("PatientFirstName"),
	PatientID: text("PatientID"),
	PatientLastBillDate: text("PatientLastBillDate"),
	PatientLastName: text("PatientLastName"),
	PatientMiddleName: text("PatientMiddleName"),
	PatientName: text("PatientName"),
	PatientPaymentAmount: text("PatientPaymentAmount"),
	PatientPaymentCategoryDesc: text("PatientPaymentCategoryDesc"),
	PatientPaymentID: text("PatientPaymentID"),
	PatientPaymentMethodDesc: text("PatientPaymentMethodDesc"),
	PatientPaymentPostingDate: text("PatientPaymentPostingDate"),
	PatientPaymentRef: text("PatientPaymentRef"),
	PostingDate: text("PostingDate"),
	PracticeID: text("PracticeID"),
	PracticeName: text("PracticeName"),
	PrimaryInsuranceAddressLine1: text("PrimaryInsuranceAddressLine1"),
	PrimaryInsuranceAddressLine2: text("PrimaryInsuranceAddressLine2"),
	PrimaryInsuranceAdjudicationDate: text("PrimaryInsuranceAdjudicationDate"),
	PrimaryInsuranceBatchID: text("PrimaryInsuranceBatchID"),
	PrimaryInsuranceCity: text("PrimaryInsuranceCity"),
	PrimaryInsuranceCompanyName: text("PrimaryInsuranceCompanyName"),
	PrimaryInsuranceCountry: text("PrimaryInsuranceCountry"),
	PrimaryInsuranceFirstBillDate: text("PrimaryInsuranceFirstBillDate"),
	PrimaryInsuranceInsuranceAllowed: text("PrimaryInsuranceInsuranceAllowed"),
	PrimaryInsuranceInsuranceCoinsurance: text("PrimaryInsuranceInsuranceCoinsurance"),
	PrimaryInsuranceInsuranceContractAdjustment: text("PrimaryInsuranceInsuranceContractAdjustment"),
	PrimaryInsuranceInsuranceContractAdjustmentReason: text("PrimaryInsuranceInsuranceContractAdjustmentReason"),
	PrimaryInsuranceInsuranceCopay: text("PrimaryInsuranceInsuranceCopay"),
	PrimaryInsuranceInsuranceDeductible: text("PrimaryInsuranceInsuranceDeductible"),
	PrimaryInsuranceInsurancePayment: text("PrimaryInsuranceInsurancePayment"),
	PrimaryInsuranceInsuranceSecondaryAdjustment: text("PrimaryInsuranceInsuranceSecondaryAdjustment"),
	PrimaryInsuranceInsuranceSecondaryAdjustmentReason: text("PrimaryInsuranceInsuranceSecondaryAdjustmentReason"),
	PrimaryInsuranceLastBillDate: text("PrimaryInsuranceLastBillDate"),
	PrimaryInsurancePaymentCategoryDesc: text("PrimaryInsurancePaymentCategoryDesc"),
	PrimaryInsurancePaymentID: text("PrimaryInsurancePaymentID"),
	PrimaryInsurancePaymentMethodDesc: text("PrimaryInsurancePaymentMethodDesc"),
	PrimaryInsurancePaymentPostingDate: text("PrimaryInsurancePaymentPostingDate"),
	PrimaryInsurancePaymentRef: text("PrimaryInsurancePaymentRef"),
	PrimaryInsurancePlanName: text("PrimaryInsurancePlanName"),
	PrimaryInsuranceState: text("PrimaryInsuranceState"),
	PrimaryInsuranceZipCode: text("PrimaryInsuranceZipCode"),
	ProcedureCode: text("ProcedureCode"),
	ProcedureCodeCategory: text("ProcedureCodeCategory"),
	ProcedureModifier1: text("ProcedureModifier1"),
	ProcedureModifier2: text("ProcedureModifier2"),
	ProcedureModifier3: text("ProcedureModifier3"),
	ProcedureModifier4: text("ProcedureModifier4"),
	ProcedureName: text("ProcedureName"),
	Receipts: text("Receipts"),
	RefCode: text("RefCode"),
	ReferringProviderID: text("ReferringProviderID"),
	ReferringProviderName: text("ReferringProviderName"),
	RenderingProviderID: text("RenderingProviderID"),
	RenderingProviderName: text("RenderingProviderName"),
	SchedulingProviderID: text("SchedulingProviderID"),
	SchedulingProviderName: text("SchedulingProviderName"),
	SecondaryInsuranceAddressLine1: text("SecondaryInsuranceAddressLine1"),
	SecondaryInsuranceAddressLine2: text("SecondaryInsuranceAddressLine2"),
	SecondaryInsuranceAdjudicationDate: text("SecondaryInsuranceAdjudicationDate"),
	SecondaryInsuranceBatchID: text("SecondaryInsuranceBatchID"),
	SecondaryInsuranceCity: text("SecondaryInsuranceCity"),
	SecondaryInsuranceCompanyName: text("SecondaryInsuranceCompanyName"),
	SecondaryInsuranceCountry: text("SecondaryInsuranceCountry"),
	SecondaryInsuranceFirstBillDate: text("SecondaryInsuranceFirstBillDate"),
	SecondaryInsuranceInsuranceAllowed: text("SecondaryInsuranceInsuranceAllowed"),
	SecondaryInsuranceInsuranceCoinsurance: text("SecondaryInsuranceInsuranceCoinsurance"),
	SecondaryInsuranceInsuranceContractAdjustment: text("SecondaryInsuranceInsuranceContractAdjustment"),
	SecondaryInsuranceInsuranceContractAdjustmentReason: text("SecondaryInsuranceInsuranceContractAdjustmentReason"),
	SecondaryInsuranceInsuranceCopay: text("SecondaryInsuranceInsuranceCopay"),
	SecondaryInsuranceInsuranceDeductible: text("SecondaryInsuranceInsuranceDeductible"),
	SecondaryInsuranceInsurancePayment: text("SecondaryInsuranceInsurancePayment"),
	SecondaryInsuranceInsuranceSecondaryAdjustment: text("SecondaryInsuranceInsuranceSecondaryAdjustment"),
	SecondaryInsuranceInsuranceSecondaryAdjustmentReason: text("SecondaryInsuranceInsuranceSecondaryAdjustmentReason"),
	SecondaryInsuranceLastBillDate: text("SecondaryInsuranceLastBillDate"),
	SecondaryInsurancePaymentCategoryDesc: text("SecondaryInsurancePaymentCategoryDesc"),
	SecondaryInsurancePaymentID: text("SecondaryInsurancePaymentID"),
	SecondaryInsurancePaymentMethodDesc: text("SecondaryInsurancePaymentMethodDesc"),
	SecondaryInsurancePaymentPostingDate: text("SecondaryInsurancePaymentPostingDate"),
	SecondaryInsurancePaymentRef: text("SecondaryInsurancePaymentRef"),
	SecondaryInsurancePlanName: text("SecondaryInsurancePlanName"),
	SecondaryInsuranceState: text("SecondaryInsuranceState"),
	SecondaryInsuranceZipCode: text("SecondaryInsuranceZipCode"),
	ServiceEndDate: text("ServiceEndDate"),
	ServiceLocationBillingName: text("ServiceLocationBillingName"),
	ServiceLocationCLIANumber: text("ServiceLocationCLIANumber"),
	ServiceLocationFacilityID: text("ServiceLocationFacilityID"),
	ServiceLocationFacilityIdType: text("ServiceLocationFacilityIdType"),
	ServiceLocationFax: text("ServiceLocationFax"),
	ServiceLocationFaxExt: text("ServiceLocationFaxExt"),
	ServiceLocationId: text("ServiceLocationId"),
	ServiceLocationNPI: text("ServiceLocationNPI"),
	ServiceLocationName: text("ServiceLocationName"),
	ServiceLocationNameAddressLine1: text("ServiceLocationNameAddressLine1"),
	ServiceLocationNameAddressLine2: text("ServiceLocationNameAddressLine2"),
	ServiceLocationNameCity: text("ServiceLocationNameCity"),
	ServiceLocationNameCountry: text("ServiceLocationNameCountry"),
	ServiceLocationNameState: text("ServiceLocationNameState"),
	ServiceLocationNameZipCode: text("ServiceLocationNameZipCode"),
	ServiceLocationPhone: text("ServiceLocationPhone"),
	ServiceLocationPhoneExt: text("ServiceLocationPhoneExt"),
	ServiceLocationPlaceOfServiceCode: text("ServiceLocationPlaceOfServiceCode"),
	ServiceLocationPlaceOfServiceName: text("ServiceLocationPlaceOfServiceName"),
	ServiceStartDate: text("ServiceStartDate"),
	Status: text("Status"),
	SupervisingProviderID: text("SupervisingProviderID"),
	SupervisingProviderName: text("SupervisingProviderName"),
	TertiaryInsuranceAddressLine1: text("TertiaryInsuranceAddressLine1"),
	TertiaryInsuranceAddressLine2: text("TertiaryInsuranceAddressLine2"),
	TertiaryInsuranceAdjudicationDate: text("TertiaryInsuranceAdjudicationDate"),
	TertiaryInsuranceBatchID: text("TertiaryInsuranceBatchID"),
	TertiaryInsuranceCity: text("TertiaryInsuranceCity"),
	TertiaryInsuranceCompanyName: text("TertiaryInsuranceCompanyName"),
	TertiaryInsuranceCompanyPlanName: text("TertiaryInsuranceCompanyPlanName"),
	TertiaryInsuranceCountry: text("TertiaryInsuranceCountry"),
	TertiaryInsuranceInsuranceAllowed: text("TertiaryInsuranceInsuranceAllowed"),
	TertiaryInsuranceInsuranceCoinsurance: text("TertiaryInsuranceInsuranceCoinsurance"),
	TertiaryInsuranceInsuranceContractAdjustment: text("TertiaryInsuranceInsuranceContractAdjustment"),
	TertiaryInsuranceInsuranceContractAdjustmentReason: text("TertiaryInsuranceInsuranceContractAdjustmentReason"),
	TertiaryInsuranceInsuranceCopay: text("TertiaryInsuranceInsuranceCopay"),
	TertiaryInsuranceInsuranceDeductible: text("TertiaryInsuranceInsuranceDeductible"),
	TertiaryInsuranceInsurancePayment: text("TertiaryInsuranceInsurancePayment"),
	TertiaryInsuranceInsuranceSecondaryAdjustment: text("TertiaryInsuranceInsuranceSecondaryAdjustment"),
	TertiaryInsuranceInsuranceSecondaryAdjustmentReason: text("TertiaryInsuranceInsuranceSecondaryAdjustmentReason"),
	TertiaryInsurancePaymentCategoryDesc: text("TertiaryInsurancePaymentCategoryDesc"),
	TertiaryInsurancePaymentID: text("TertiaryInsurancePaymentID"),
	TertiaryInsurancePaymentMethodDesc: text("TertiaryInsurancePaymentMethodDesc"),
	TertiaryInsurancePaymentPostingDate: text("TertiaryInsurancePaymentPostingDate"),
	TertiaryInsurancePaymentRef: text("TertiaryInsurancePaymentRef"),
	TertiaryInsuranceState: text("TertiaryInsuranceState"),
	TertiaryInsuranceZipCode: text("TertiaryInsuranceZipCode"),
	TotalBalance: text("TotalBalance"),
	TotalCharges: text("TotalCharges"),
	TypeOfService: text("TypeOfService"),
	UnitCharge: text("UnitCharge"),
	Units: text("Units"),
	uuid: uuid("uuid").defaultRandom(),
	processStatus: varchar("processStatus", { length: 50 }),
	ExtractedDate: varchar("extractedDate", { length: 100 }),
	claimType: varchar("claimType", { length: 30 }),
	finishStatus: boolean("finishStatus").default(false),
}

// Schema for Charges
export const chargesRaw = rcmSchema.table("charges_raw", chargesRawColumns);


export const paymentsRawColumns = {
	id: serial("id").primaryKey(),
	AdjudicationDate: text("AdjudicationDate"),
	Adjustments: text("Adjustments"),
	Amount: text("Amount"),
	Applied: text("Applied"),
	AppointmentID: text("AppointmentID"),
	BatchNumber: text("BatchNumber"),
	Category: text("Category"),
	CreatedDate: text("CreatedDate"),
	ID: text("ID"),
	LastModifiedDate: text("LastModifiedDate"),
	PayerName: text("PayerName"),
	PayerType: text("PayerType"),
	PaymentMethod: text("PaymentMethod"),
	PostDate: text("PostDate"),
	PracticeId: text("PracticeId"),
	ReferenceNumber: text("ReferenceNumber"),
	Refunds: text("Refunds"),
	Unapplied: text("Unapplied"),
	uuid: uuid("uuid").defaultRandom(),
}

export const claimsAssignments = rcmSchema.table("claimsAssignments", claimsAssignmentsColumns);

// Notes table for claim assessments
export const claimAssessmentNotes = rcmSchema.table("claim_assessment_notes", claimAssessmentNotesColumns);

// Claim audit history table for tracking all claim changes
export const claimAuditHistory = rcmSchema.table("claim_audit_history", claimAuditHistoryColumns);


// Schema for Payments Raw
export const paymentsRaw = rcmSchema.table("payments_raw", paymentsRawColumns);


// Schema for Patients Raw
export const patientsRaw = rcmSchema.table("patients_raw", patientsRawColumns);

// AI Conversations table
export const aiConversations = rcmSchema.table("ai_conversations", {
	id: uuid("id").defaultRandom().primaryKey(),
	userId: uuid("user_id")
		.notNull()
		.references(() => users.id, { onDelete: "cascade" }),
	threadId: varchar("thread_id", { length: 255 }).notNull(),
	title: varchar("title", { length: 255 }).notNull(),
	claimId: uuid("claim_id"),
	claimDcn: varchar("claim_dcn", { length: 100 }),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
	updatedAt: timestamp("updated_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
});

// AI Messages table
export const aiMessages = rcmSchema.table("ai_messages", {
	id: uuid("id").defaultRandom().primaryKey(),
	conversationId: uuid("conversation_id")
		.notNull()
		.references(() => aiConversations.id, { onDelete: "cascade" }),
	role: varchar("role", { length: 10 }).notNull(),
	content: text("content").notNull(),
	sqlQuery: text("sql_query"),
	rowCount: integer("row_count"),
	rating: varchar("rating", { length: 10 }),
	createdAt: timestamp("created_at").default(sql`CURRENT_TIMESTAMP`).notNull(),
});

// ============================================
// CARRIERS SCHEMA - Separate schema for Carrier users
// Uses same structure as RCM schema
// Note: users and tokens tables are shared from rcm schema
// ============================================

export const carriersSchema = pgSchema("carriers");

// Schema for practicesRaw in carriers
export const carrierPracticesRaw = carriersSchema.table("practices_raw", practicesRawColumns);

// Schema for service locations in carriers
export const carrierServiceLocations = carriersSchema.table("service_locations_raw", serviceLocationsColumns);

// Schema for batches_raw in carriers
export const carrierBatchesRaw = carriersSchema.table("batches_raw", batchesRawColumns);

// Schema for batches in carriers
export const carrierBatches = carriersSchema.table("batches", batchesColumns);

// Schema for charges_raw in carriers (same structure as rcm.chargesRaw)
export const carrierChargesRaw = carriersSchema.table("charges_raw", chargesRawColumns);

// Schema for claims assignments in carriers
export const carrierClaimsAssignments = carriersSchema.table("claimsAssignments", claimsAssignmentsColumns);

// Schema for claim assessment notes in carriers
export const carrierClaimAssessmentNotes = carriersSchema.table("claim_assessment_notes", claimAssessmentNotesColumns);

// Schema for claim audit history in carriers
export const carrierClaimAuditHistory = carriersSchema.table("claim_audit_history", claimAuditHistoryColumns);

// Schema for payments_raw in carriers
export const carrierPaymentsRaw = carriersSchema.table("payments_raw", paymentsRawColumns);

// Schema for patients_raw in carriers
export const carrierPatientsRaw = carriersSchema.table("patients_raw", patientsRawColumns);

// Remittance Files Schema (rcm schema)
export const remittanceFiles = rcmSchema.table("remittance_files", {
	id: serial("id").primaryKey(),
	file_name: varchar("file_name", { length: 255 }).notNull(),
	payer_id: varchar("payer_id", { length: 50 }).notNull(),
	payer_name: varchar("payer_name", { length: 255 }),
	check_number: varchar("check_number", { length: 50 }),
	check_amount: text("check_amount"), // NUMERIC(12, 2) stored as text
	check_date: text("check_date"), // DATE stored as text
	eft_number: varchar("eft_number", { length: 50 }),
	eft_amount: text("eft_amount"), // NUMERIC(12, 2) stored as text
	transaction_date: text("transaction_date"), // DATE stored as text
	processed_at: timestamp("processed_at"),
	status: varchar("status", { length: 50 }).default("pending").notNull(),
	total_payments: text("total_payments").default("0.00"), // NUMERIC(12, 2) stored as text
	total_adjustments: text("total_adjustments").default("0.00"), // NUMERIC(12, 2) stored as text
	total_denials: serial("total_denials").default(0),
	file_path: text("file_path"),
	file_content: text("file_content"),
	error_message: text("error_message"),
	created_at: timestamp("created_at").defaultNow(),
	created_by: varchar("created_by", { length: 100 }),
});

// Remittance Claim Map Schema (rcm schema)
export const remittanceClaimMap = rcmSchema.table("remittance_claim_map", {
	id: serial("id").primaryKey(),
	remittance_file_id: serial("remittance_file_id").notNull(),
	claim_header_id: serial("claim_header_id"),

	patient_control_number: varchar("patient_control_number", { length: 100 }).notNull(),
	payer_claim_number: varchar("payer_claim_number", { length: 100 }),
	claim_status_code: varchar("claim_status_code", { length: 10 }),

	total_claim_charge: text("total_claim_charge"), // NUMERIC(12, 2) stored as text
	payment_amount: text("payment_amount").notNull(), // NUMERIC(12, 2) stored as text
	allowed_amount: text("allowed_amount"), // NUMERIC(12, 2) stored as text

	deductible: text("deductible").default("0.00"), // NUMERIC(12, 2) stored as text
	coinsurance: text("coinsurance").default("0.00"), // NUMERIC(12, 2) stored as text
	copay: text("copay").default("0.00"), // NUMERIC(12, 2) stored as text
	contractual_adjustment: text("contractual_adjustment").default("0.00"), // NUMERIC(12, 2) stored as text
	other_adjustments: text("other_adjustments").default("0.00"), // NUMERIC(12, 2) stored as text

	patient_responsibility: text("patient_responsibility"), // NUMERIC(12, 2) stored as text

	match_status: varchar("match_status", { length: 50 }).default("unmatched"),
	posting_status: varchar("posting_status", { length: 50 }).default("pending"),
	posted_at: timestamp("posted_at"),
	posted_by: varchar("posted_by", { length: 100 }),

	service_start_date: text("service_start_date"), // DATE stored as text
	service_end_date: text("service_end_date"), // DATE stored as text

	created_at: timestamp("created_at").defaultNow(),
});
