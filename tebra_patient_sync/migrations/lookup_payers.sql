CREATE TABLE "EDI_Tebra".lookup_payers (
	id serial4 NOT NULL,
	sort_id bigserial NOT NULL,
	payer_name text NULL,
	payer_id text NULL,
	payer_type _text NULL,
	transaction_type _text NULL,
	available text NULL,
	non_par text NULL,
	enrollment text NULL,
	secondary text NULL,
	attachment text NULL,
	wc_auto text NULL,
	notes text NULL,
	payer_alias _text NULL,
	portal text NULL,
	login text NULL,
	"password" text NULL,
	active_status bool DEFAULT true NULL,
	integration_details jsonb NULL,
	CONSTRAINT lookup_payers_pkey PRIMARY KEY (id),
	CONSTRAINT lookup_payers_sort_id_key UNIQUE (sort_id)
);
CREATE INDEX idx_lookup_payers_payer_id_name ON "EDI_Tebra".lookup_payers USING btree (payer_id, payer_name);
CREATE INDEX idx_lookup_payers_payer_type_gin ON "EDI_Tebra".lookup_payers USING gin (payer_type);