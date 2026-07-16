import os
import shutil
import pandas as pd
from azure.storage.blob import BlobServiceClient
from azure.identity import ClientSecretCredential
from azure.keyvault.secrets import SecretClient

# Azure Key Vault settings
#key_vault_name = os.getenv("KEY_VAULT_NAME", "")
#key_vault_url = f"https://{key_vault_name}.vault.azure.net/"

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

    print("Successfully authenticated to Azure Blob Storage.")
except Exception as e:
    print(f"Error authenticating to Azure Blob Storage: {e}")
    exit(1)

# Azure Blob Storage settings
container_name = "834analytics-dev"
blob_folder = "raw/luminos_call_logs/New Reports/"
container_client = blob_service_client.get_container_client(container_name)

# Define local paths
source_folder = r"C:\Users\myopsadmin\Downloads"
g_drive_folder = r"G:\Shared drives\Data Analytics\Data Analytics Projects\Luminos Call Logs"
archive_folder = r"G:\Shared drives\Data Analytics\Data Analytics Projects\Luminos Call Logs\Archive"

# Process each file in the source folder
for filename in os.listdir(source_folder):
    file_path = os.path.join(source_folder, filename)

    # Check if the file is a regular file and contains the keyword "Luminos Call Log Report"
    if os.path.isfile(file_path) and "Luminos Call Log Report" in filename:
        # Move the file to the G drive folder
        new_file_path = os.path.join(g_drive_folder, filename)
        shutil.move(file_path, new_file_path)
        print(f"Moved: {filename} to {g_drive_folder}")

        # Load the data into a pandas DataFrame
        data = pd.read_csv(new_file_path)

        # Convert data types for specified columns to integers
        int_columns = [
            "DAY OF MONTH", "HOUR", "HOUR OF DAY", "MONTH", "YEAR"
        ]
        for col in int_columns:
            if col in data.columns:
                data[col] = pd.to_numeric(data[col], errors="coerce").astype("Int64")

        # Convert datetime columns
        datetime_columns = ["TIMESTAMP", "DATE AND HOUR"]
        for col in datetime_columns:
            if col in data.columns:
                data[col] = pd.to_datetime(data[col], errors="coerce")

        # Save the modified DataFrame back to CSV temporarily for upload
        temp_csv_path = os.path.join(g_drive_folder, f"temp_{filename}")
        data.to_csv(temp_csv_path, index=False)

        # Upload the file to Azure Blob Storage in the New Reports folder
        blob_name = f"{blob_folder}{filename}"
        with open(temp_csv_path, "rb") as data_file:
            container_client.upload_blob(name=blob_name, data=data_file, overwrite=True)
        print(f"Uploaded {filename} to blob storage as {blob_name}")

        # Clean up the temporary file
        os.remove(temp_csv_path)

        # After successful upload, move the file to the Archive folder in G drive
        archive_file_path = os.path.join(archive_folder, filename)
        shutil.move(new_file_path, archive_file_path)
        print(f"Moved: {filename} to {archive_folder}")

    else:
        print(f"Skipped: {filename}")
