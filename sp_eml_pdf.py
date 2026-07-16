import os
import re
import requests
import unicodedata
from io import BytesIO
from bs4 import BeautifulSoup
from email import policy
from email.parser import BytesParser
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from msal import ConfidentialClientApplication
from urllib.parse import quote

# ====================== CONFIG ===========================
KEY_VAULT_URL = os.getenv("KEYVAULT_URL", "")
SITE_HOSTNAME = "agilityins.sharepoint.com"
SOURCE_SITE_PATH = "834labs-dataai"
BASE_FOLDER = "AI"
DEST_SITE_PATH = "834Labs"  # PDF destination site

GRAPH_API_URL = "https://graph.microsoft.com/v1.0"
GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]

PDF_BASE_FOLDER = f"#0 - Product Enablement/AI Projects/Email - Training Files/{BASE_FOLDER}/GeneratedPDF"

# ====================== AUTH HELPERS =====================
def get_sp_credentials():
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=KEY_VAULT_URL, credential=credential)
    client_id = client.get_secret(os.getenv("KEYVAULT_CLIENT_ID_SECRET_NAME", "")).value
    client_secret = client.get_secret(os.getenv("KEYVAULT_CLIENT_SECRET_NAME", "")).value
    tenant_id = client.get_secret(os.getenv("KEYVAULT_TENANT_ID_SECRET_NAME", "")).value
    return client_id, client_secret, tenant_id

def get_graph_token(client_id, client_secret, tenant_id):
    app = ConfidentialClientApplication(
        client_id=client_id,
        authority=f"https://login.microsoftonline.com/{tenant_id}",
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise Exception(f"Graph token request failed: {result.get('error_description')}")
    print("🔐 Graph API token retrieved.")
    return result["access_token"]

def get_site_id(access_token, site_path):
    url = f"{GRAPH_API_URL}/sites/{SITE_HOSTNAME}:/sites/{site_path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    site_id = resp.json()["id"]
    print(f"✅ Site ID resolved: {site_id} for {site_path}")
    return site_id

# ====================== TEXT CLEANER ======================
def clean_unicode_text(text: str) -> str:
    """Normalize and remove invalid Unicode characters causing PDF symbols."""
    text = unicodedata.normalize("NFC", text)

    # Keep only printable chars (allow \n and \t)
    text = ''.join(ch for ch in text if ch.isprintable() or ch in ['\n', '\t'])

    # Replace problematic Unicode punctuation
    replacements = {
        '\u2018': "'", '\u2019': "'",
        '\u201c': '"', '\u201d': '"',
        '\u2013': '-', '\u2014': '-',
        '\u2026': '...', '\u00a0': ' ',
        '\ufffd': '',  # replacement char
        '\u200b': '',  # zero-width space
        '\u202c': '',  # pop directional formatting
    }
    for k, v in replacements.items():
        text = text.replace(k, v)

    return text.strip()

# ====================== FILE HELPERS =====================
def sanitize_filename(filename, max_length=100):
    filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
    filename = filename.replace('$', '_')
    return filename[:max_length]

def save_text_to_pdf_bytes(text):
    pdfmetrics.registerFont(UnicodeCIDFont("HeiseiMin-W3"))
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter)
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            name="UnicodeSafe",
            fontName="HeiseiMin-W3",
            fontSize=10,
            leading=14,
            alignment=TA_LEFT,
        )
    )
    story = []

    # 🧹 Clean and sanitize text
    text = clean_unicode_text(text)
    safe_text = (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
    )

    for line in safe_text.splitlines():
        line = line.strip()
        if not line:
            story.append(Spacer(1, 6))
        else:
            story.append(Paragraph(line, styles["UnicodeSafe"]))
    doc.build(story)
    pdf_bytes = buffer.getvalue()
    buffer.close()
    return pdf_bytes

# ================== EMAIL EXTRACTION ====================
def extract_email_content(eml_bytes):
    msg = BytesParser(policy=policy.default).parsebytes(eml_bytes)
    html_content = ""
    plain_content = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="ignore") if payload else ""
            except Exception:
                decoded = ""
            if ctype == "text/html" and not html_content:
                html_content = decoded
            elif ctype == "text/plain" and not plain_content:
                plain_content = decoded
    else:
        ctype = msg.get_content_type()
        if ctype == "text/html":
            html_content = msg.get_content()
        elif ctype == "text/plain":
            plain_content = msg.get_content()
    content = html_content or plain_content
    if not content:
        return "(No readable body found)", []
    soup = BeautifulSoup(content, 'html.parser')
    for tag in soup(["script", "style", "meta", "head", "footer", "header", "nav"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    text = re.sub(r'\n+', '\n', text)
    text = clean_unicode_text(text)
    links = [a['href'] for a in soup.find_all('a', href=True)]
    return text, links

def fetch_link_content(url):
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.content, "html.parser")
            for tag in soup(["script", "style", "header", "footer", "nav"]):
                tag.decompose()
            text = soup.get_text(separator="\n", strip=True)
            return clean_unicode_text(re.sub(r'\n+', '\n', text))
    except Exception as e:
        print(f"⚠️ Error fetching {url}: {e}")
    return ""

# ================== SHAREPOINT HELPERS ==================
def encode_path(path):
    return '/'.join([quote(segment, safe='') for segment in path.strip('/').split('/')])

def ensure_folder_exists(site_id, folder_path, access_token):
    """Ensure each folder in the path exists on SharePoint, creating it if missing."""
    parts = folder_path.strip("/").split("/")
    current_path = ""
    headers = {"Authorization": f"Bearer {access_token}"}
    for part in parts:
        current_path = f"{current_path}/{part}" if current_path else part
        encoded_current_path = encode_path(current_path)
        url = f"{GRAPH_API_URL}/sites/{site_id}/drive/root:/{encoded_current_path}"
        resp = requests.get(url, headers=headers)
        if resp.status_code == 404:
            parent = "/".join(parts[:parts.index(part)])
            encoded_parent = encode_path(parent) if parent else ''
            create_url = (
                f"{GRAPH_API_URL}/sites/{site_id}/drive/root:/{encoded_parent}:/children"
                if encoded_parent else f"{GRAPH_API_URL}/sites/{site_id}/drive/root/children"
            )
            data = {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "replace"}
            create_resp = requests.post(create_url, headers=headers, json=data)
            if create_resp.status_code not in (200, 201):
                print(f"⚠️ Failed to create folder {current_path}: {create_resp.text}")
                raise Exception(f"Folder creation failed: {create_resp.text}")
        elif resp.status_code not in (200, 201):
            resp.raise_for_status()

def upload_pdf_to_sharepoint(site_id, folder_path, file_name, pdf_bytes, access_token):
    """Upload PDF file to SharePoint ensuring the folder exists."""
    ensure_folder_exists(site_id, folder_path, access_token)
    encoded_folder = encode_path(folder_path)
    encoded_file = quote(file_name, safe='')
    upload_path = f"{encoded_folder}/{encoded_file}"
    url = f"{GRAPH_API_URL}/sites/{site_id}/drive/root:/{upload_path}:/content"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.put(url, headers=headers, data=pdf_bytes)
    print(f"UPLOAD URL: {url} | Status: {resp.status_code}")
    if resp.status_code not in (200, 201):
        print(f"⚠️ Failed to upload PDF {file_name}: {resp.text}")
        raise Exception(f"PDF upload failed: {resp.text}")
    else:
        print(f"✅ Uploaded PDF: {file_name} → {folder_path}")

def copy_eml_to_processed(site_id, folder_path, filename, dest_folder, access_token):
    """Copy a SharePoint file to another folder (safe alternative to move)."""
    ensure_folder_exists(site_id, dest_folder, access_token)
    clean_folder_path = folder_path.strip("/")
    clean_dest_folder = dest_folder.strip("/")
    url = f"{GRAPH_API_URL}/sites/{site_id}/drive/root:/{clean_folder_path}/{filename}:/copy"
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    data = {"parentReference": {"path": f"/drive/root:/{clean_dest_folder}"}}
    resp = requests.post(url, headers=headers, json=data)
    if resp.status_code in [200, 201, 202]:
        print(f"📄 Copied .eml → {dest_folder}/{filename}")
    else:
        print(f"⚠️ Copy failed ({resp.status_code}): {resp.text}")

# ================== PROCESSOR ==================
def process_sharepoint_folder(site_id, folder_path, pdf_folder, pdf_site_id, access_token):
    """Recursively process .eml files, create PDFs, and copy processed files."""
    url = f"{GRAPH_API_URL}/sites/{site_id}/drive/root:/{encode_path(folder_path)}:/children"
    headers = {"Authorization": f"Bearer {access_token}"}
    resp = requests.get(url, headers=headers)
    resp.raise_for_status()
    items = resp.json().get("value", [])

    for item in items:
        name = item["name"]
        if name in ['GeneratedPDF', 'ProcessedFile']:
            continue
        if "folder" in item:
            sub_remote = f"{folder_path}/{name}"
            process_sharepoint_folder(site_id, sub_remote, pdf_folder, pdf_site_id, access_token)
        elif name.lower().endswith(".eml"):
            print(f"\n📧 Processing {folder_path}/{name}")
            relative_path = folder_path.replace(BASE_FOLDER, "").strip("/")
            try:
                # Step 1: Copy .eml to ProcessedFile folder
                processed_folder = f"{BASE_FOLDER}/ProcessedFile/{relative_path}"
                copy_eml_to_processed(site_id, folder_path, name, processed_folder, access_token)

                # Step 2: Download .eml
                download_url = item.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    print(f"⚠️ Missing download URL for {name}")
                    continue
                r = requests.get(download_url)
                if r.status_code != 200:
                    print(f"⚠️ Failed to download {name}")
                    continue

                # Step 3: Extract email content and links
                email_text, links = extract_email_content(r.content)

                # Step 4: Generate main email PDF
                pdf_relative_folder = f"{PDF_BASE_FOLDER}/{relative_path}"
                base_name = sanitize_filename(os.path.splitext(name)[0])
                pdf_filename = f"{base_name}.pdf"
                pdf_bytes = save_text_to_pdf_bytes(email_text)
                upload_pdf_to_sharepoint(pdf_site_id, pdf_relative_folder, pdf_filename, pdf_bytes, access_token)

                # Step 5: Process and upload link PDFs
                for idx, link in enumerate(links, start=1):
                    link_text = fetch_link_content(link)
                    if link_text:
                        link_pdf_name = f"{base_name}_link{idx}.pdf"
                        link_pdf_bytes = save_text_to_pdf_bytes(link_text)
                        upload_pdf_to_sharepoint(pdf_site_id, pdf_relative_folder, link_pdf_name, link_pdf_bytes, access_token)
            except Exception as e:
                print(f"❌ Error processing {name}: {e}")

# ================== MAIN ==================
def main():
    client_id, client_secret, tenant_id = get_sp_credentials()
    access_token = get_graph_token(client_id, client_secret, tenant_id)
    source_site_id = get_site_id(access_token, SOURCE_SITE_PATH)
    pdf_site_id = get_site_id(access_token, DEST_SITE_PATH)
    print(f"📁 Reading from SharePoint folder: {BASE_FOLDER} on {SOURCE_SITE_PATH}")
    process_sharepoint_folder(source_site_id, BASE_FOLDER, PDF_BASE_FOLDER, pdf_site_id, access_token)
    print("\n🎉 All emails converted and stored on SharePoint successfully!")

if __name__ == "__main__":
    main()
