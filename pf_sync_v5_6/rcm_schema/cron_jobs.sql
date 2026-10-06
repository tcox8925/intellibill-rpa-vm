CREATE TYPE "EDI_Tebra".cron_job_frequency AS ENUM (
    'hourly',
    'daily',
    'weekly',
    'monthly'
);


CREATE TABLE "EDI_Tebra".cron_jobs (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	frequency "EDI_Tebra".cron_job_frequency NOT NULL,
	interval_hours int4 NULL,
	day_of_week int4 NULL,
	day_of_month int4 NULL,
	setup_at time NULL,
	job_name text NOT NULL,
	job_setting text NULL,
	job_description text NULL,
	active bool DEFAULT true NOT NULL,
	created_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
	CONSTRAINT chk_day_of_month CHECK (((day_of_month IS NULL) OR ((day_of_month >= 1) AND (day_of_month <= 31)))),
	CONSTRAINT chk_day_of_week CHECK (((day_of_week IS NULL) OR ((day_of_week >= 0) AND (day_of_week <= 6)))),
	CONSTRAINT chk_interval_by_frequency CHECK ((((frequency = 'hourly'::"EDI_Tebra".cron_job_frequency) AND (interval_hours IS NOT NULL)) OR ((frequency <> 'hourly'::"EDI_Tebra".cron_job_frequency) AND (interval_hours IS NULL)))),
	CONSTRAINT chk_interval_hours CHECK (((interval_hours IS NULL) OR ((interval_hours >= 1) AND (interval_hours <= 24)))),
	CONSTRAINT chk_monthly_day CHECK ((((frequency = 'monthly'::"EDI_Tebra".cron_job_frequency) AND (day_of_month IS NOT NULL)) OR ((frequency <> 'monthly'::"EDI_Tebra".cron_job_frequency) AND (day_of_month IS NULL)))),
	CONSTRAINT chk_setup_at_by_frequency CHECK ((((frequency = 'hourly'::"EDI_Tebra".cron_job_frequency) AND (setup_at IS NULL)) OR ((frequency = ANY (ARRAY['daily'::"EDI_Tebra".cron_job_frequency, 'weekly'::"EDI_Tebra".cron_job_frequency, 'monthly'::"EDI_Tebra".cron_job_frequency])) AND (setup_at IS NOT NULL)))),
	CONSTRAINT chk_weekly_day CHECK ((((frequency = 'weekly'::"EDI_Tebra".cron_job_frequency) AND (day_of_week IS NOT NULL)) OR ((frequency <> 'weekly'::"EDI_Tebra".cron_job_frequency) AND (day_of_week IS NULL)))),
	CONSTRAINT cron_jobs_pkey PRIMARY KEY (id)
);


CREATE TYPE "EDI_Tebra".cron_job_execution_status AS ENUM (
    'started',
    'processing',
    'failed',
    'success'
);

CREATE TABLE "EDI_Tebra".cron_job_executions (
	id uuid DEFAULT gen_random_uuid() NOT NULL,
	cron_job_id uuid NOT NULL,
	triggered_at timestamptz DEFAULT CURRENT_TIMESTAMP NOT NULL,
	status "EDI_Tebra".cron_job_execution_status NOT NULL,
	error_description text NULL,
	response jsonb NULL,
	CONSTRAINT chk_error_description CHECK ((((status = 'failed'::"EDI_Tebra".cron_job_execution_status) AND (error_description IS NOT NULL)) OR (status <> 'failed'::"EDI_Tebra".cron_job_execution_status))),
	CONSTRAINT cron_job_executions_pkey PRIMARY KEY (id),
	CONSTRAINT cron_job_executions_cron_job_id_fkey FOREIGN KEY (cron_job_id) REFERENCES "EDI_Tebra".cron_jobs(id)
);



INSERT INTO "EDI_Tebra".cron_jobs (
    frequency,
    setup_at,
    job_name,
    job_setting,
    job_description,
    active
)
VALUES
(
    'daily',
    '06:00:00',
    'practiceFusionFacesheetPull',
    'PRACTICE_FUSION_FACESHEET_PULL',
    'Pull the facesheet extract from Practice Fusion, update patient demographics and coverages, load historical diagnoses, and capture visit diagnoses.',
    TRUE
),
(
    'daily',
    '07:00:00',
    'practiceFusionAppointmentsPull',
    'PF_SC_appointments_pull',
    'Pull scheduled appointments from the Practice Fusion schedule and sync them into EDI_Tebra.patient_appointments.',
    TRUE
),
(
    'daily',
    '08:00:00',
    'tebraPatientSync',
    'TEBRA_PATIENT_SYNC',
    'Update patient data from Tebra for Tebra clients.',
    TRUE
),
(
    'daily',
    '09:00:00',
    'tebraFacesheetPull',
    'TEBRA_FACESHEET_PULL',
    'Pull facesheets from Tebra, extract the required data, and create claims for Tebra clients.',
    TRUE
);
