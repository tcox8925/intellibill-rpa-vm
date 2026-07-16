import os
import shutil
from datetime import datetime
import pytz
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

# Initializing a list for logging 
log_entries = [] 
# Define logging functions 
def log_message(message, level='INFO'):
    log_entries.append({ 
        'timestamp': datetime.now(pytz.timezone("America/Chicago")), # timestamp for each step is recorded in CST
        'level': level,
        'message': message })

# Set today's date in mmddyyyy format
today_date = datetime.now(pytz.timezone("America/Chicago")).strftime("%m%d%Y")

# Set temp folder
temp_folder = r"G:/My Drive/UHC_Open_Enrollments"

file = next((f for f in os.listdir(temp_folder)), None)
file_path = os.path.join(temp_folder, file)

g_drive_folder = r"G:/Shared drives/Data Analytics/Data Analytics Projects/Open Enrollment Report"

# here g_drive_folder is the speicifc path in shared drive
try: 
    shutil.move(file_path, g_drive_folder)
    log_message(f"Sucessfully moved {file} to G-Drive at {g_drive_folder}")
except:
    log_message("Unexpectde error: {e}", level = 'ERROR')
    raise


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
    raise

# Blob folders
blob_base_folder = "raw/open_enrollment/"
current_month_number = datetime.now().strftime("%m")
current_month_short = datetime.now().strftime("%b")
current_year = datetime.now().strftime("%Y")
blob_folder = f"{blob_base_folder}/{current_year} {current_month_number} {current_month_short}/"

#upload the file to Azure Blob
try:
    blob_client = blob_service_client.get_blob_client(
                container = "834analytics-dev",
                blob=os.path.join(blob_folder,file))

    with open(os.path.join(g_drive_folder,file), "rb") as data:
        blob_client.upload_blob(data, overwrite=True)  # Set overwrite=True to overwrite any existing blob with the same name
    log_message(f"Successfully uploaded {file} to blob storage at {blob_folder}")      
except Exception as e:
    log_message(f"Error uploading file to Azure Blob Storage: {e}", level = 'ERROR')
    raise 
finally:
    log_message("The UHC agent enrollment file is successfully uploaded to shared G-Drive and Azure storage ")
