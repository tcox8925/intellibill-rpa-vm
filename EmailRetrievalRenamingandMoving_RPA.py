import os
import pandas as pd
import re
from datetime import datetime
import pytz
from azure.identity import DefaultAzureCredential, ClientSecretCredential
from azure.keyvault.secrets import SecretClient
from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
from logger import setup_logger, log_success, log_error, log_final_entry, init_log_entry, update_log_extra_fields  #Logging module

# **CONFIGURATION**
script_name = setup_logger("Email Retrieval Automation")  #Set up logging
init_log_entry(script_name)
update_log_extra_fields(
    script_name,
    flow_id="A0CB67F1-8F85-4A31-86A0-21260B893131",
    sub_entity_id="270681372001"
)

# Initializing a list for logging 
log_entries = [] 
# Define logging functions 
def log_message(message, level='INFO'):
    log_entries.append({ 
        'timestamp': datetime.now(pytz.timezone("America/Chicago")), # timestamp for each step is recorded in CST
        'level': level,
        'message': message })
    

# Set download folder
download_folder = r"C:/Users/myopsadmin/Documents/AmeriHealth and Christus Files"  # Update this to the appropriate folder in which the files are downloaded


# Set today's date in mmddyyyy format
today_date = datetime.now(pytz.timezone("America/Chicago")).strftime("%m%d%Y")


# the keywords to categorize each downloaded file to a carrier, and the rename format for corresponding carrier and file

keywords = {
    'AmeriHealthCaritasACA': [['AmeriHealthCaritas','2025','Exchange','RTS'], 'raw_acu_amerihealth_aca_'],
    'AmeriHealthCaritasMedicare': [['Medicare','2025'], 'raw_acu_amerihealth_mdc_'],
    'AnthemACA': [['AGENT','REPORT'], 'raw_acu_anthem_aca_'],
    'ChristusACA': [['HIX','RTS'], 'raw_acu_christus_aca_']
}


#Azure credentials

tenant_id = os.getenv("AZURE_TENANT_ID", "")
client_id = os.getenv("AZURE_CLIENT_ID", "")
client_secret = os.getenv("AZURE_CLIENT_SECRET", "")

# Authenticate using Service Principal
try:
    azure_credential = ClientSecretCredential(
        tenant_id=tenant_id,
        client_id=client_id,
        client_secret=client_secret
    )

    # Blob Service Client setup
    storage_account_name = "834analyticsdatalake"  # Replace with your storage account name
    account_url = f"https://{storage_account_name}.blob.core.windows.net"
    blob_service_client = BlobServiceClient(account_url=account_url, credential=azure_credential)
    log_message("Successfully authenticated to Azure Blob Storage")
except Exception as e:
    log_message(f"Error authenticating to Azure Blob Storage: {e}", level = 'ERROR')
    log_error("db_connection_error", f"Azure authentication failed: {e}", script_name)
    raise

# Blob folders
blob_base_folder = "raw/agent_contract_update/"
current_month_number = datetime.now().strftime("%m")
current_month_short = datetime.now().strftime("%b")
current_year = datetime.now().strftime("%Y")
blob_folder = f"{blob_base_folder}/{current_month_number} {current_month_short} {current_year}/"

# rename the downloaded files and uplaod to azure storage
try:    
    for downloaded_file in os.listdir(download_folder):
        downloaded_file_path = os.path.join(download_folder, downloaded_file) 
        if not os.path.isfile(downloaded_file_path):    # in case there are subfolders in the current folder, skip them
            continue
        namewx,file_extension = os.path.splitext(downloaded_file_path)      # here, namewx: filename without extension
        if file_extension not in ['.csv','.xlsx']:
            continue    # the files should be either csv or xlsx files
        renamed_file = None
        for key,value in keywords.items():
            keyword_pattern = ''.join([f'(?=.*{re.escape(keyword)})' for keyword in value[0]])
            full_pattern = re.compile(rf"^{keyword_pattern}.*_(\d{{4}}\d{{2}}\d{{2}})$")    
            # the file fomart should have _YYYYmmdd in the end before file extension
            match = full_pattern.search(namewx)

            if match:
                # rename the file by appending the decided file name pattern, the date and the file extension
                renamed_file = f"{value[1]}{today_date}{file_extension}"
                renamed_file_path = os.path.join(download_folder, renamed_file)
                # Rename the downloaded file
                os.rename(downloaded_file_path, renamed_file_path)
                log_message(f"Successfully Renamed: {downloaded_file} to {renamed_file}")
        
        if renamed_file:
                   
            blob_client = blob_service_client.get_blob_client(
                container = "834analytics-dev",
                blob=os.path.join(blob_folder,renamed_file))
            # Upload the local file to Blob Storage
            try:
                with open(renamed_file_path, "rb") as data:
                    blob_client.upload_blob(data, overwrite=True)  # Set overwrite=True to overwrite any existing blob with the same name
                log_message(f"Successfully uploaded {downloaded_file} to blob storage {blob_folder}.")      
            except Exception as e:
                log_message(f"Error uploading file to Azure Blob Storage: {e}", level = 'ERROR')
                log_error("upload_error", f"Azure upload failed: {e}", script_name)
                raise    

except Exception as e:
        log_message(f" Error: {e}", level = 'ERROR')
        log_error("general_error", f"Azure upload failed: {e}", script_name)
        raise

log_entries_df = pd.DataFrame(log_entries) # all log entries in the log_entries list are now saved as a data frame

print(log_entries_df) # optionally print the dataframe to check all the logs

log_success()
log_final_entry(script_name)

