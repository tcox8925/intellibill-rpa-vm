/**
 * CLINICAL CARE SCHEMA
 * This file contains clinical care related table schemas for the EDI_Tebra database.
 */

import { date, index, integer, numeric, pgSchema, text, time, timestamp, uniqueIndex, uuid, boolean, jsonb } from 'drizzle-orm/pg-core';
import { sql } from 'drizzle-orm';
import { claimHeader, client, group, patientHeader, practice } from './new_schema.js';
import { providers } from './lookup_schema.js';
import { users } from './schema.js';
import { chargeSheetHeader, chargeSheetProcCode } from './schema/chargeSheetSchema.js';

// ============================================================================
// SCHEMA DEFINITION
// ============================================================================
export const ediTebraSchema = pgSchema(process.env.DB_SCHEMA || 'EDI_Tebra');

// ============================================================================
// CLINICAL CARE TABLES
// ============================================================================

/**
 * Patient Appointments table
 * Maps to EDI_Tebra.patient_appointments
 */
export const patientAppointments = ediTebraSchema.table('patient_appointments', {
  id: uuid('id').primaryKey().defaultRandom().notNull(),
  clientId: integer('client_id').notNull().references(() => client.clientId),
  groupId: integer('group_id').notNull().references(() => group.id),
  practiceId: integer('practice_id').notNull().references(() => practice.id),
  patientHeaderId: uuid('patient_header_id').references(() => patientHeader.patientHeaderId),
  providerId: uuid('provider_id').references(() => providers.ID),
  room: text('room'),
  visitDate: date('visit_date'),
  visitTime: time('visit_time'),
  visitType: text('visit_type'),
  durationInMin: integer('duration_in_min'),
  status: text('status'),
  createdAt: timestamp('created_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
  updatedAt: timestamp('updated_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
});

/**
 * Patient Visit Header table
 * Stores the selected patient visit/DOS context for charge sheet selection.
 */
export const patientVisitHeader = ediTebraSchema.table('patient_visit_header', {
  id: uuid('id').primaryKey().defaultRandom().notNull(),
  clientId: integer('client_id').notNull().references(() => client.clientId),
  groupId: integer('group_id').notNull().references(() => group.id),
  practiceId: integer('practice_id').notNull().references(() => practice.id),
  claimId: uuid('claim_id').references(() => claimHeader.claimId),
  chargeSheetId: uuid('charge_sheet_id').references(() => chargeSheetHeader.id),
  patientHeaderId: uuid('patient_header_id').notNull().references(() => patientHeader.patientHeaderId),
  visitTime: time('visit_time'),
  dos: date('dos').notNull(),
  save: boolean('save').notNull().default(false),
  signed: boolean('signed').notNull().default(false),
  complete: boolean('complete').notNull().default(false),
  signedDate: timestamp('signed_date').default(sql`(now() AT TIME ZONE 'America/Chicago')`),
  createdBy: uuid('created_by').notNull().references(() => users.id),
  visit_proc_codes: jsonb('visit_proc_codes'),
  miscellaneous_fee_proc_ids: jsonb('miscellaneous_fee_proc_ids').$type<Record<string, Array<{ id: string; selection: boolean }>>>(),
  status: text('status').notNull(),
  providerId: uuid('provider_id').references(() => providers.ID),
  visitType: text('visit_type'),
  chiefComplaint: text('chief_complaint'),
  confirmation: boolean('confirmation'),
  copay: numeric('copay', { precision: 12, scale: 2 }),
  intakeForm: text('intake_form'),
  eligibility: text('eligibility'),
  balanceDue: numeric('balance_due', { precision: 12, scale: 2 }),
  createdAt: timestamp('created_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
  updatedAt: timestamp('updated_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
}, (table) => ({
  idxPatientVisitPracticeDos: index('idx_patient_visit_practice_dos').on(table.practiceId, table.dos),
  idxPatientVisitPatientDos: index('idx_patient_visit_patient_dos').on(table.patientHeaderId, table.dos),
  idxPatientVisitChargeSheetId: index('idx_patient_visit_charge_sheet_id').on(table.chargeSheetId),
}));

/**
 * Patient Visit Procedure Selection table
 * Stores selected charge sheet procedure code ids for each patient visit.
 */
export const patientVisitProcedureSelection = ediTebraSchema.table('patient_visit_procedure_selection', {
  id: uuid('id').primaryKey().defaultRandom().notNull(),
  patientVisitHeaderId: uuid('patient_visit_header_id').notNull().references(() => patientVisitHeader.id, { onDelete: 'cascade' }),
  chargeSheetProcCodeId: uuid('charge_sheet_proc_code_id').notNull().references(() => chargeSheetProcCode.id, { onDelete: 'cascade' }),
  createdAt: timestamp('created_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
  updatedAt: timestamp('updated_at').notNull().default(sql`(now() AT TIME ZONE 'America/Chicago')`),
}, (table) => ({
  uqPatientVisitProcedureSelection: uniqueIndex('uq_patient_visit_procedure_selection').on(
    table.patientVisitHeaderId,
    table.chargeSheetProcCodeId,
  ),
  idxPatientVisitProcedureSelectionHeader: index('idx_patient_visit_procedure_selection_header').on(table.patientVisitHeaderId),
}));
