import io
import re
import pdfplumber
from azure.storage.blob import BlobServiceClient
from azure.identity import DefaultAzureCredential
try:
    from utils.db_utils import get_postgres_connection
except ImportError:
    from db_utils import get_postgres_connection


BLOB_CONTAINER = "834analytics-dev"
BLOB_PREFIX = "raw/agent_license_update/NIPR"
ACCOUNT_URL = "https://834analyticsdatalake.blob.core.windows.net"

DATE = r"\d{2}/\d{2}/\d{4}"


# ----------------------------------------------------------
# BLOB
# ----------------------------------------------------------
def blob_client():
    return BlobServiceClient(
        account_url=ACCOUNT_URL,
        credential=DefaultAzureCredential()
    )


def list_pdfs():
    cc = blob_client().get_container_client(BLOB_CONTAINER)

    for b in cc.list_blobs(name_starts_with=BLOB_PREFIX):
        name = b.name

        # Only allow files directly under NIPR root
        if not name.startswith(f"{BLOB_PREFIX}/archive/") and name.lower().endswith(".pdf"):
            yield name


def pdf_text(blob_name: str) -> str:
    bc = blob_client().get_blob_client(BLOB_CONTAINER, blob_name)
    stream = io.BytesIO(bc.download_blob().readall())

    pages = []
    with pdfplumber.open(stream) as pdf:
        for p in pdf.pages:
            pages.append(p.extract_text() or "")
    return "\n".join(pages)


# ----------------------------------------------------------
# PARSERS
# ----------------------------------------------------------
def parse_header(text):
    def g(p):
        m = re.search(p, text, re.MULTILINE)
        return m.group(1).strip() if m else None

    return {
        "npn": g(r"NPN:\s*(\d+)"),
        "name": g(r"Name:\s*(.+)"),
        "dob": g(r"DOB:\s*(" + DATE + ")"),
        "report_date": g(r"Report Date:\s*(" + DATE + ")"),
        "demo_updated": g(r"Demographics:\s*(" + DATE + ")"),
        "producer_updated": g(r"Producer Licensing:\s*(" + DATE + ")"),
    }

def move_to_archive(blob_name: str):
    """
    Move processed PDF to archive folder inside same container.
    raw/agent_license_update/NIPR/file.pdf
    → raw/agent_license_update/NIPR/archive/file.pdf
    """

    bsc = blob_client()
    container = bsc.get_container_client(BLOB_CONTAINER)

    # Build archive path
    filename = blob_name.split("/")[-1]
    archive_name = f"{BLOB_PREFIX}/archive/{filename}"

    source_blob = bsc.get_blob_client(BLOB_CONTAINER, blob_name)
    archive_blob = bsc.get_blob_client(BLOB_CONTAINER, archive_name)

    # Start copy
    archive_blob.start_copy_from_url(source_blob.url)

    # Optional: wait for copy to complete (usually instant within same account)
    props = archive_blob.get_blob_properties()
    if props.copy.status != "success":
        raise Exception(f"Archive copy failed for {blob_name}")

    # Delete original
    source_blob.delete_blob()

    print(f"Archived → {archive_name}")

import re

def split_address(raw: str):
    """
    Parse NIPR address line from right → left into:
    line1, city, state, zip, country
    """

    raw = raw.replace(",", " ").replace("  ", " ").strip()

    # ZIP (last 5 digits)
    m_zip = re.search(r"(\d{5})(?:-\d{4})?$", raw)
    zip_code = m_zip.group(1) if m_zip else None

    # Remove zip
    left = raw[:m_zip.start()].strip() if m_zip else raw

    # Country (usually U.S.A.)
    m_country = re.search(r"(U\.S\.A\.|USA)$", left)
    country = "USA" if m_country else None
    left = left[:m_country.start()].strip() if m_country else left

    # State (2 letter)
    m_state = re.search(r"\b([A-Z]{2})$", left)
    state = m_state.group(1) if m_state else None
    left = left[:m_state.start()].strip() if m_state else left

    # City is last word chunk now
    parts = left.split()
    city = parts[-1] if parts else None
    line1 = " ".join(parts[:-1]) if len(parts) > 1 else left

    return line1, city, state, zip_code, country


def parse_contacts(text):
    contacts = {"addresses": [], "phones": [], "emails": []}

    addr_blocks = {
        "business": r"Date Updated Business Addresses:\s*\n(" + DATE + r")\s+(.+?(?:U\.S\.A\.|USA)\s*\d+)",
        "mailing": r"Date Updated Mailing Addresses:\s*\n(" + DATE + r")\s+(.+?(?:U\.S\.A\.|USA)\s*\d+)",
        "residence": r"Date Updated Residence Addresses:\s*\n(" + DATE + r")\s+(.+?(?:U\.S\.A\.|USA)\s*\d+)",
    }

    for t, pat in addr_blocks.items():
        for dt, raw in re.findall(pat, text, re.S):
            contacts["addresses"].append((t, dt, raw.strip()))

    for dt, ph in re.findall(
        r"Date Updated Business Phone:\s*\n(" + DATE + r")\s+([\d\-\(\) ]+)",
        text,
    ):
        contacts["phones"].append(("business", dt, ph.strip()))

    for dt, ph in re.findall(
        r"Date Updated Fax:\s*\n(" + DATE + r")\s+([\d\-\(\) ]+)",
        text,
    ):
        contacts["phones"].append(("fax", dt, ph.strip()))

    for dt, em in re.findall(
        r"Date Updated Business Email:\s*\n(" + DATE + r")\s+([\w\.-]+@[\w\.-]+)",
        text,
    ):
        contacts["emails"].append(("business", dt, em.lower().strip()))

    return contacts


def parse_licenses(text):
    licenses = []
    blocks = re.split(r"License Summary", text)[1:]

    for block in blocks:
        state = re.search(r"State:\s*([A-Z]{2})", block)
        lic_no = re.search(r"License #:\s*([A-Z0-9\-\./]+)", block)
        issue = re.search(r"Issue Date:\s*(" + DATE + ")", block)
        exp = re.search(r"Expiration Date:\s*(" + DATE + ")", block)
        last_upd = re.search(r"Last Updated:\s*(" + DATE + ")", block)
        residency = re.search(r"Residency:\s*([A-Z]{1,2})", block)
        lic_class = re.search(r"Class:\s*(.+?)\s+Residency", block)
        active = re.search(r"Active:\s*(Yes|No)", block)

        if not state or not lic_no:
            continue

        issue_dt = issue.group(1) if issue else None
        exp_dt = exp.group(1) if exp else None
        upd_dt = last_upd.group(1) if last_upd else None
        is_active = True if active and active.group(1) == "Yes" else False

        # -------------------------
        # LOA: stop at Appointments
        # -------------------------
        # -------------------------
        # LOA: real NIPR pattern
        # -------------------------
        loas = []
        loa_sec = re.search(r"Line of Authority(.+?)Appointments", block, re.S)

        if loa_sec:
            for ln in loa_sec.group(1).splitlines():
                ln = ln.strip()
                if not ln:
                    continue

                m = re.search(
                    r"^(.*?)\s+(" + DATE + r")\s+(Active|Inactive)\s+\*\s+(" + DATE + r")",
                    ln
                )

                if not m:
                    continue

                loa_name = m.group(1).strip()
                loa_issue = m.group(2)
                loa_status = m.group(3)
                status_date = m.group(4)

                loas.append({
                    "name": loa_name,
                    "issue": loa_issue,
                    "status": loa_status,
                    "date": status_date,
                })

        # -------------------------
        # APPOINTMENTS (inside block)
        # -------------------------
        appointments = []
        appt_sec = re.search(r"Appointments(.+?)(?:\n\d+ of \d+|\Z)", block, re.S)
        if appt_sec:
            sec = appt_sec.group(1)

            # Each appointment starts with "Company Name: ... FEIN:... Cocode:..."
            company_hits = re.findall(
                r"Company Name:\s*(.+?)\s+FEIN:(\d+)\s+Cocode:\s*([0-9]+)",
                sec,
                re.S,
            )

            for company_name, fein, cocode in company_hits:
                # For each company hit, try to find "Appointed mm/dd/yyyy" nearby
                # We take the first appointed date after the company line.
                # (NIPR formatting varies but this works well in practice.)
                # Grab a small window after the match
                idx = sec.find(company_name)
                window = sec[idx: idx + 800] if idx >= 0 else sec

                m_appt = re.search(r"Appointed\s+(" + DATE + ")", window)
                appt_date = m_appt.group(1) if m_appt else None

                # Termination often shown as "Term" or "Termination" with a date; optional
                m_term = re.search(r"(?:Terminated|Termination|Term)\s+(" + DATE + ")", window)
                term_date = m_term.group(1) if m_term else None

                appointments.append({
                    "fein": fein,
                    "company": company_name.strip(),
                    "cocode": cocode.strip(),
                    "appt_date": appt_date,
                    "term_date": term_date,
                    "last_updated": upd_dt,
                })

        licenses.append({
            "state": state.group(1),
            "number": lic_no.group(1),
            "issue": issue_dt,
            "exp": exp_dt,
            "updated": upd_dt,
            "residency": residency.group(1) if residency else None,
            "class": lic_class.group(1).strip() if lic_class else None,
            "active": is_active,
            "loas": loas,
            "appointments": appointments,
        })

    return licenses


# ----------------------------------------------------------
# UPSERT
# ----------------------------------------------------------
def upsert(cur, header, contacts, licenses):
    npn = header["npn"]

    cur.execute("""
        insert into wpo.nipr_producer_info
        (npn, full_name, dob, report_date,
         demographics_last_updated, producer_last_updated)
        values (%s,%s,%s,%s,%s,%s)
        on conflict (npn) do update set
            full_name=excluded.full_name,
            dob=excluded.dob,
            report_date=excluded.report_date,
            demographics_last_updated=excluded.demographics_last_updated,
            producer_last_updated=excluded.producer_last_updated;
    """, (
        npn,
        header["name"],
        header["dob"],
        header["report_date"],
        header["demo_updated"],
        header["producer_updated"],
    ))

    for t, dt, raw in contacts["addresses"]:
        line1, city, state, zip_code, country = split_address(raw)

        cur.execute("""
            insert into wpo.nipr_addresses
            (npn,address_type,line1,city,state,zip,country,last_updated)
            values (%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict do nothing;
        """, (npn, t, line1, city, state, zip_code, country, dt))

    for t, dt, ph in contacts["phones"]:
        cur.execute("""
            insert into wpo.nipr_phones
            (npn,phone_type,phone,last_updated)
            values (%s,%s,%s,%s)
            on conflict do nothing;
        """, (npn, t, ph, dt))

    for t, dt, em in contacts["emails"]:
        cur.execute("""
            insert into wpo.nipr_emails
            (npn,email_type,email,last_updated)
            values (%s,%s,%s,%s)
            on conflict do nothing;
        """, (npn, t, em, dt))

    for lic in licenses:
        cur.execute("""
            insert into wpo.nipr_licenses
            (npn,state,license_number,residency,
             license_class,issue_date,expiration_date,
             active,last_updated)
            values (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            on conflict (npn,state,license_number)
            do update set
                expiration_date=excluded.expiration_date,
                active=excluded.active,
                last_updated=excluded.last_updated
            returning id;
        """, (
            npn,
            lic["state"],
            lic["number"],
            lic["residency"],
            lic["class"],
            lic["issue"],
            lic["exp"],
            lic["active"],
            lic["updated"],
        ))

        lid = cur.fetchone()[0]

        for loa in lic["loas"]:
            cur.execute("""
                insert into wpo.nipr_license_loa
                (license_id, loa_name, loa_issue_date,
                 loa_status, status_date)
                values (%s,%s,%s,%s,%s)
                on conflict do nothing;
            """, (
                lid,
                loa["name"],
                loa["issue"],
                loa["status"],
                loa["date"],
            ))
        # ---- Appointments (tied to this license_id)
        for appt in lic.get("appointments", []):
            cur.execute("""
                insert into wpo.nipr_appointments
                (license_id, fein, company_name, cocode,
                 appointment_status, appointment_date,
                 termination_date, last_updated)
                values (%s,%s,%s,%s,%s,%s,%s,%s)
                on conflict do nothing;
            """, (
                lid,
                appt["fein"],
                appt["company"],
                appt["cocode"],
                "Active" if lic["active"] else "Inactive",
                appt["appt_date"],
                appt["term_date"],
                appt["last_updated"],
            ))

# ----------------------------------------------------------
# MAIN
# ----------------------------------------------------------
def main():
    for blob in list_pdfs():
        print("Processing:", blob)
        text = pdf_text(blob)

        header = parse_header(text)
        contacts = parse_contacts(text)
        licenses = parse_licenses(text)
        #appointments = parse_appointments(text)

        conn = get_postgres_connection()
        cur = conn.cursor()

        try:
            upsert(cur, header, contacts, licenses)
            #upsert_appointments(cur, header["npn"], appointments)
            conn.commit()
            print("Committed:", header["npn"])
            move_to_archive(blob)
        except Exception as e:
            conn.rollback()
            print("Error:", e)
        finally:
            conn.close()


if __name__ == "__main__":
    main()