import json
import sys
from logger import setup_logger, log_error, log_success, log_final_entry, ERROR_CODES, init_log_entry,update_log_extra_fields

print("Flow logger output test: ")
print("Argument list: ", sys.argv)
errored = False

dict_data = {}
try:
    for entry in sys.argv[1:]:
        entry_split = entry.split("=", 1)
        if entry_split[1] == 'None':
            continue
        dict_data[entry_split[0]] = entry_split[1].strip()
    print(f"Dictionary data: {dict_data}")
except:
    print("Issue loading logging dictionary. Aborting.")
print("Finished loading logging dictionary.")

try:
    script_name_logged = setup_logger(dict_data.get("script_name", "Flow Log Handler"))
    init_log_entry(script_name_logged)
    update_log_extra_fields(script_name_logged,
        file_status=dict_data.get("file_status"),
        file_path=dict_data.get("file_path"),
        process_type=dict_data.get("process_type"),
        file_report_month=dict_data.get("file_report_month"),
        file_com_month=dict_data.get("file_com_month"),
        company_id=dict_data.get("company_id"),
        carrier_id=dict_data.get("carrier_id"),
        product_name=dict_data.get("product_name"),
        flow_id=dict_data.get("flow_id"))

    print("Logging success.")
    log_success()
    log_final_entry(script_name_logged)
    print("Log entry completed.")
except:
    print("Issue updating log entry. Aborting.")
    log_error(ERROR_CODES["general_error"], "Error with creating or updating log entry.",script_name_logged)
    log_final_entry(script_name_logged)
    print("Log entry completed.")
    
print("Process complete.")

