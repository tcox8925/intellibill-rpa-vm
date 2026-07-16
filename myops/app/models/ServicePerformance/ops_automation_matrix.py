import uuid
from sqlalchemy import Column, Date, Text, ForeignKey, text, TIMESTAMP
from sqlalchemy.dialects.postgresql import UUID
from app.models.BaseClasses import Base

from sqlalchemy import Column, Text, String


class OpsAccProcessMatrix(Base):
    __tablename__ = "ops_acc_process_matrix"
    __table_args__ = {"schema": "wpo"}
    
    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    carrier_id = Column(Text)
    carrier_name = Column(Text)
    company_id = Column(Text)
    process_type = Column(Text)
    origin = Column(Text)
    mode = Column("mode", Text)
    email_cadence = Column(Text)
    frequency = Column(Text)
    mode_details = Column(Text)
    link_agent = Column(Text)
    portal_url = Column(Text)
    carrier_template = Column(Text)
    base_gdrive_url = Column(Text)
    download_path = Column(Text)
    email_to = Column(Text)
    active_flag = Column(Text)
    last_run_time = Column(Text)
    last_error = Column(Text)
    crm_filter = Column(Text)
    folder_pattern = Column(Text)
    crm_module = Column(Text)
    crm_update_mode = Column(Text)
    crm_success_status = Column(Text)
    crm_fail_status = Column(Text)
    crm_notify_email = Column(Text)
    pa_trigger_url = Column(Text)
    requires_template_update = Column(Text)
    template_field_map = Column(Text)
    base_blob_url = Column(Text)
    notes = Column(Text)
    eod_time = Column(Text)
    in_development = Column(Text)
    last_eod_sent = Column(Text)
    current_template_path = Column(Text)
    run_cadence = Column(String)
    cadence_desc = Column(String)
    automated = Column(String)
    automation_type = Column(String)
    email_cc = Column(String)
    def __repr__(self) -> str:
        return (
            f"<OpsAccProcessMatrix("
            f"carrier_id={self.carrier_id}, "
            f"carrier_name={self.carrier_name}, "
            f"company_id={self.company_id}, "
            f"process_type={self.process_type}, "
            f"active_flag={self.active_flag}"
            f")>"
        )

class OpsRpaMatrix(Base):
    __tablename__ = "ops_rpa_matrix"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    process_name = Column(Text)
    file_type = Column(Text)
    file_prefix = Column(Text)
    expected_extension = Column(Text)
    requires_extraction = Column(Text)
    extracted_file_prefix = Column(Text)
    extracted_file_extension = Column(Text)
    rename_base = Column(Text)
    g_drive_base_path = Column(Text)
    blob_base_path = Column(Text)
    split_by_carrier = Column(Text)
    process_type = Column(Text)
    upload_data = Column(Text)
    download_path = Column(Text)
    upload_data_table = Column(Text)
    use_profile_path = Column(Text)
    log_in = Column(Text)
    url = Column(Text)
    batch_process_upload = Column(Text)
    email = Column(Text)
    batch_process_upload_set = Column(Text)
    password = Column("password", Text)
    url_nav = Column(Text)
    notification_process = Column(Text)
    notification_email = Column(Text)
    profile_path = Column(Text)
    pautomate_url = Column(Text)
    script_name = Column(Text)
    more_than_one_download = Column(Text)
    schedule = Column(Text)
    client_secret = Column(Text)
    company_id = Column(Text)
    keyvault_name = Column(Text)
    cadence = Column(Text)
    client_id = Column(Text)
    target_dates = Column(Text)
    driver = Column(Text)
    storage_account_name = Column(Text)
    server = Column("server", Text)
    authentication = Column(Text)
    container_name = Column(Text)
    database = Column("database", Text)
    tenant_id = Column(Text)
    blob_container_name = Column(Text)
    key_vault_name = Column(Text)
    key_vault_name_1 = Column("key_vault_name.1", Text)
    flag_completion = Column(Text)
    product_name = Column(Text)
    carrier_id = Column(Text)
    carrier_name = Column(Text)
    company_id_field = Column(Text)
    sftp_port = Column(Text)
    remote_path = Column(Text)
    otp_extension = Column(Text)
    parent_carrier_name = Column(Text)
    otp_path = Column(Text)
    otp_needed = Column(Text)
    otp_filename = Column(Text)
    parent_process_name = Column(Text)
    run_sandbox_only = Column(Text)
    disabled = Column(Text)
    disabled_reason = Column(Text)
    pickup_method = Column(Text)
    parent_carrier_id = Column(Text)
    automated = Column(String(15), nullable=False, default="Active")


    def __repr__(self) -> str:
        return (
            f"<OpsRpaMatrix("
            f"process_name={self.process_name}, "
            f"process_type={self.process_type}, "
            f"carrier_id={self.carrier_id}, "
            f"carrier_name={self.carrier_name}, "
            f"disabled={self.disabled}"
            f")>"
        )

class OpsProcessMatrixCom(Base):
    __tablename__ = "ops_process_matrix_com"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    process_type = Column(Text)
    company_id = Column(Text)
    company_name = Column(Text)
    carrier_name = Column(Text)
    carrier_id = Column(Text)
    raw_file_name_prefix = Column(Text)
    output_file_name_prefix = Column(Text)
    rule_type = Column(Text)
    rule_value = Column(Text)
    rule_parameter = Column(Text)
    start_date = Column(Text)
    end_date = Column(Text)
    load_date = Column(Text)

    def __repr__(self) -> str:
        return (
            f"<OpsProcessMatrixCom("
            f"process_type={self.process_type}, "
            f"company_id={self.company_id}, "
            f"carrier_id={self.carrier_id}, "
            f"rule_type={self.rule_type}"
            f")>"
        )

class OpsAcrProcessMatrix(Base):
    __tablename__ = "ops_acr_process_matrix"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    carrier_id = Column(Text)
    company_id = Column(Text)
    script_name = Column(Text)
    active_flag = Column(Text)
    schedule = Column(Text)
    dry_run = Column(Text)
    email_export = Column(Text)
    status_message = Column(Text)
    url = Column(Text)
    log_in = Column(Text)
    email = Column(Text)
    password = Column("password", Text)
    otp_required = Column(Text)
    otp_path = Column(Text)
    file_path = Column(Text)
    file_name = Column(Text)
    file_extension = Column(Text)
    batch_size = Column(Text)
    thread_count = Column(Text)
    use_test_npns = Column(Text)
    email_to = Column(Text)
    email_flow_url = Column(Text)
    email_message = Column(Text)
    sftp_to_path = Column(Text)
    sftp_to_file_name = Column(Text)
    blob_to_path = Column(Text)
    g_drive_to_path = Column(Text)
    storage_file_name = Column(Text)
    automatic_export = Column(Text)
    in_development = Column(Text)
    eod_times = Column(Text)
    eod_flag = Column(Text)
    last_eod_refresh_date = Column(Text)
    disabled_until = Column(Text)
    schedule_desc = Column(String)
    automated = Column(String)
    automation_type = Column(String)

    def __repr__(self) -> str:
        return (
            f"<OpsAcrProcessMatrix("
            f"carrier_id={self.carrier_id}, "
            f"company_id={self.company_id}, "
            f"script_name={self.script_name}, "
            f"active_flag={self.active_flag}, "
            f"automated={self.automated}"
            f")>"
        )

class OpsLoadMatrixAcu(Base):
    __tablename__ = "ops_load_matrix_acu"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    process_type = Column(Text, nullable=True)
    carrier_name = Column(Text, nullable=True)
    carrier_id = Column(Text, nullable=True)
    raw_file_name_prefix = Column(Text, nullable=True)
    file_extension = Column(Text, nullable=True)
    sheet_name = Column(Text, nullable=True)
    skip_rows = Column(Text, nullable=True)
    contract_count = Column(Text, nullable=True)
    contract_type = Column(Text, nullable=True)
    has_headers = Column(Text, nullable=True)
    database_column = Column(Text, nullable=True)
    mapping = Column("mapping", Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    automated = Column(
        String(15),
        nullable=True,
        server_default=text("'Active'")
    )

    def __repr__(self):
        return (
            f"<OpsLoadMatrixAcu("
            f"process_type={self.process_type!r}, "
            f"carrier_name={self.carrier_name!r}, "
            f"carrier_id={self.carrier_id!r}, "
            f"automated={self.automated!r}"
            f")>"
        )

class OpsProcessMatrix(Base):
    __tablename__ = "ops_process_matrix"
    __table_args__ = {"schema": "wpo"}

    pk_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    process_type = Column(Text, nullable=True)
    carrier_name = Column(Text, nullable=True)
    carrier_id = Column(Text, nullable=True)
    raw_file_name_prefix = Column(Text, nullable=True)
    rule_type = Column(Text, nullable=True)
    rule_value = Column(Text, nullable=True)
    start_date = Column(Text, nullable=True)
    end_date = Column(Text, nullable=True)
    load_date = Column(Text, nullable=True)
    automated = Column(
        String(15),
        nullable=True,
        server_default=text("'Active'")
    )

    def __repr__(self):
        return (
            f"<OpsProcessMatrix("
            f"process_type={self.process_type!r}, "
            f"carrier_name={self.carrier_name!r}, "
            f"carrier_id={self.carrier_id!r}, "
            f"rule_type={self.rule_type!r}, "
            f"automated={self.automated!r}"
            f")>"
        )

