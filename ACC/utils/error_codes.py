# ==========================================================
# utils/error_codes.py
# ==========================================================
"""
Standardized operational error codes for ACC RPA handlers.
These map to major workflow failure points across CRM, DB, Azure, Template,
Authentication, and File subsystems.
"""

ERROR_CODES = {
    # --- CRM Layer ---
    "CRM_FETCH_ERROR": "CRM_001",
    "CRM_UPDATE_ERROR": "CRM_002",
    "CRM_AUTH_ERROR": "CRM_003",
    "CRM_TIMEOUT": "CRM_004",

    # --- Queue / Database ---
    "DB_CONNECTION_ERROR": "DB_001",
    "DB_INSERT_ERROR": "DB_002",
    "DB_UPDATE_ERROR": "DB_003",
    "DB_FETCH_ERROR": "DB_004",

    # --- Azure Storage / Blob ---
    "AZURE_UPLOAD_ERROR": "AZ_001",
    "AZURE_DOWNLOAD_ERROR": "AZ_002",
    "AZURE_BLOB_NOT_FOUND": "AZ_003",
    "AZURE_KEYVAULT_ERROR": "AZ_004",

    # --- Template / File I/O ---
    "TEMPLATE_WRITE_ERROR": "TMP_001",
    "TEMPLATE_MISSING": "TMP_002",
    "FILE_IO_ERROR": "IO_001",

    # --- Email / Notification ---
    "EMAIL_SEND_ERROR": "EM_001",
    "EMAIL_TEMPLATE_ERROR": "EM_002",

    # --- Authentication / Portal ---
    "LOGIN_FAILURE": "AUTH_001",
    "USERNAME_NOT_FOUND": "AUTH_002",
    "PASSWORD_NOT_FOUND": "AUTH_003",
    "LOGIN_BUTTON_NOT_FOUND": "AUTH_004",

    # --- External Services / APIs ---
    "API_TIMEOUT": "API_001",
    "API_UNAUTHORIZED": "API_002",
    "API_PARSING_ERROR": "API_003",

    # --- Generic / Catch-All ---
    "UNHANDLED_EXCEPTION": "GEN_001",
    "ERR" : "GEN_000"
}
