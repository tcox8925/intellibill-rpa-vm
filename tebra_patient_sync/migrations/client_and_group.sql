--- Client Table
CREATE TABLE "EDI_Tebra".client (
	client_id int4 DEFAULT nextval('"EDI_Tebra".client_id_seq'::regclass) NOT NULL,
	client_status varchar(500) NULL,
	client_taxid varchar(500) NULL,
	client_name varchar(500) NULL,
	client_contact_lnam varchar(500) NULL,
	client_contact_fnam varchar(500) NULL,
	client_contact_email varchar(500) NULL,
	client_contact_number varchar(500) NULL,
	client_addr1 varchar(500) NULL,
	client_addr2 varchar(500) NULL,
	client_city varchar(500) NULL,
	client_zip varchar(500) NULL,
	client_logo text NULL,
	created_at timestamp DEFAULT now() NOT NULL,
	updated_at timestamp DEFAULT now() NOT NULL,
	client_state varchar(500) NULL,
	denial_risk_threshold int4 DEFAULT 30 NOT NULL,
	status bool DEFAULT true NOT NULL,
	CONSTRAINT client_denial_risk_threshold_chk CHECK (((denial_risk_threshold >= 0) AND (denial_risk_threshold <= 100))),
	CONSTRAINT client_pkey PRIMARY KEY (client_id)
);


--- Group Table
CREATE TABLE "EDI_Tebra"."group" (
	id serial4 NOT NULL,
	client_id int4 NULL,
	grp_taxid varchar(500) NULL,
	grp_name varchar(500) NULL,
	grp_addr1 varchar(500) NULL,
	grp_addr2 varchar(500) NULL,
	grp_city varchar(500) NULL,
	grp_st varchar(500) NULL,
	grp_zip varchar(500) NULL,
	grp_npi varchar(500) NULL,
	grp_ptan varchar(500) NULL,
	grp_contact_lnam varchar(500) NULL,
	grp_contact_fnam varchar(500) NULL,
	grp_contact_email varchar(500) NULL,
	grp_contact_number varchar(500) NULL,
	created_at timestamp DEFAULT now() NOT NULL,
	updated_at timestamp DEFAULT now() NOT NULL,
	entity_id varchar(20) NULL,
	is_manual_review_on bool DEFAULT false NOT NULL,
	denial_risk_threshold int4 DEFAULT 30 NULL,
	taxonomy varchar(20) NULL,
	pecos varchar(20) NULL,
	npn varchar(20) NULL,
	medicaid varchar(20) NULL,
	ptan varchar(50) NULL,
	CONSTRAINT group_pkey PRIMARY KEY (id),
	CONSTRAINT group_client_id_client_client_id_fk FOREIGN KEY (client_id) REFERENCES "EDI_Tebra".client(client_id)
);

-- Table Triggers

create trigger tr_seed_group_settings_after_group_insert after
insert
    on
    "EDI_Tebra"."group" for each row execute function "EDI_Tebra".fn_seed_group_settings_after_group_insert();