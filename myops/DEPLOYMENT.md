# MyOps FastAPI Deployment Guide

Since the merge of Tebra + Practice Fusion sync into one process, this doc covers
two paths:
- **[Fresh deployment](#fresh-deployment-brand-new-vm)** -- nothing set up yet, starting from a bare VM.
- **[Update an existing deployment](#update-an-existing-deployment)** -- the VM
  is already running the old Tebra-only `myops` service and needs to move over
  to the combined `server.py` entrypoint, or is already on it and just needs a
  routine code update.

The systemd unit is checked into the repo at [`myops/myops.service`](myops.service)
-- copy it to `/etc/systemd/system/myops.service` rather than hand-typing it.

---

## Fresh deployment (brand-new VM)

### 1) Clone the repo

```bash
ssh ibrcmadmin@20.46.228.47
git clone <repo-url> ~/intellibill-rpa-vm
cd ~/intellibill-rpa-vm
git checkout prod
```

### 2) Create the shared venv (repo root -- not `myops/.venv` or `pf_sync_v5_6/.venv`)

```bash
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium   # Linux only -- system libs Chromium needs
```

`requirements.txt` at the repo root just points at `myops/requirements.txt`,
which already covers everything `pf_sync_v5_6` needs too -- one venv, one
install command, for both apps.

### 3) Configure secrets -- `.env`

```bash
cp .env.example .env
```

Fill in, at minimum, everything the combined server touches on startup/first
use:
- `MYOPS_API_ENV=production`, `PF_SYNC_API_ENV=production`
- `TEBRA_EMAIL`, `TEBRA_PASSWORD`, `TEBRA_MAILBOX_UPN`, `TEBRA_STORAGE_ACCOUNT_*`
- `PF_USERNAME`, `PF_PASSWORD`, `PF_PRACTICE_TIMEZONE`
- `MYOPS_DB_*` (and any of `PCH_DB_*` / `RCM_DB_*` your entity actually uses)
- Azure/Graph vars for the OTP mailbox (`AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`,
  `AZURE_TENANT_ID`, `MYOPS_AZURE_CLIENT_ID`/`_SECRET`) -- see
  `myops/README.md` §4a for what these do and how to obtain them.

### 4) One-time RPA/browser setup (do this before starting the service)

Both Tebra and Practice Fusion sync drive a **real, headed Chrome browser**
against an already-logged-in profile -- neither can log in unattended the
first time (Tebra needs an OTP; Practice Fusion needs its own login flow).
This is a manual, interactive, one-time step per environment:

- **Tebra**: follow `myops/README.md` (session/OTP setup, §4a for the Graph
  OTP-mailbox wiring) to get a working logged-in Chrome profile and confirm
  `otp_info.py`/`email_read.py` can read the OTP mailbox.
- **Practice Fusion**: follow `pf_sync_v5_6/README.md` §1 to log into Practice
  Fusion once in the Chrome profile at `~/pf_rpa_chrome` (or
  `%USERPROFILE%\pf_rpa_chrome` on Windows) so it's reusable headlessly-ish
  afterward.

Skip this and the first real API call will hang waiting for a login prompt
that never gets answered.

### 5) Install the systemd unit

```bash
sudo cp ~/intellibill-rpa-vm/myops/myops.service /etc/systemd/system/myops.service
sudo systemctl daemon-reload
sudo systemctl enable myops
sudo systemctl start myops
```

Double-check the `User=`, `WorkingDirectory=`, and `ExecStart=` paths in the
copied unit file actually match this VM's username and clone path before
starting it -- `myops/myops.service`'s checked-in values
(`/home/ibrcmadmin/intellibill-rpa-vm`, user `ibrcmadmin`) are examples, not
guaranteed to match every environment.

### 6) Verify

See [Verify locally on VM](#8-verify-locally-on-vm) below -- same checks apply
to a fresh install as to an update.

### 7) Put Nginx in front (recommended)

See [Production Recommendation](#production-recommendation) below.

---

## Update an existing deployment

### Moving from the old Tebra-only unit to the combined one (one-time)

The `myops` systemd service must now run the **repo-root** `server.py`, not
`myops/server.py` directly. That file loads and mounts both `myops/server.py`'s
app (Tebra, unprefixed routes) and `pf_sync_v5_6/server.py`'s app (Practice
Fusion sync, under `/pf-sync`) into one process on one port -- see `server.py`'s
module docstring for the full rationale. Re-install the unit file from the
checked-in copy rather than hand-editing the old one:

```bash
sudo cp ~/intellibill-rpa-vm/myops/myops.service /etc/systemd/system/myops.service
sudo systemctl daemon-reload
sudo systemctl restart myops
```

(Adjust `User=`/`WorkingDirectory=`/`ExecStart=` in the copied file first if
this VM's username or clone path differs from the checked-in example.)

If a **separate, standalone `pf_sync` systemd unit** exists on the VM from
before this merge (serving port 8011 on its own), disable and remove it --
see the port-8011 check in step 8 below.

### 1) SSH into the VM

```bash
ssh ibrcmadmin@20.46.228.47
```

### 2) Navigate to the project

```bash
cd ~/intellibill-rpa-vm
```

### 3) Pull latest code

```bash
git checkout dev
git fetch origin
git reset --hard origin/prod
```

### 4) Activate virtual environment

```bash
source venv/bin/activate
```

### 5) Install dependencies (only if requirements changed)

```bash
pip install -r requirements.txt
```

(The repo-root `requirements.txt` just points at `myops/requirements.txt`,
which already covers every dependency `pf_sync_v5_6` needs too -- one shared
venv, one install command.)

If Playwright/Chromium isn't already installed in this venv (needed by both
Tebra and Practice Fusion sync's browser automation):

```bash
python -m playwright install chromium
```

### 6) Restart service

```bash
sudo systemctl daemon-reload
sudo systemctl restart myops
```

### 7) Verify service

```bash
sudo systemctl status myops
sudo journalctl -u myops -n 50 --no-pager
```

### 8) Verify locally on VM

```bash
curl -sS http://127.0.0.1:8010/healthz
```

Expected response:

```json
{"status":"ok"}
```

The `myops` service now also serves Practice Fusion sync (`pf_sync_v5_6/server.py`),
mounted at `/pf-sync` by the combined `server.py` entrypoint -- verify that too:

```bash
curl -sS http://127.0.0.1:8010/pf-sync/healthz
```

```json
{"status":"ok"}
```

The standalone `pf_sync` process on port 8011 is retired -- nothing should be
listening there anymore. Confirm with:

```bash
sudo ss -tulpn | grep 8011   # expect no output
```

If a leftover standalone `pf_sync` systemd unit or ad-hoc process is still running
on 8011 from before this merge, stop and disable it on the VM.

### 9) Production URL

```text
http://<SERVER-IP>/
```

## Useful Commands

Restart:

```bash
sudo systemctl restart myops
```

Start:

```bash
sudo systemctl start myops
```

Stop:

```bash
sudo systemctl stop myops
```

Tail logs:

```bash
sudo journalctl -fu myops
```

Check port:

```bash
sudo ss -tulpn | grep 8010
```

## Production Recommendation

- Keep Uvicorn bound to `127.0.0.1:8010`.
- Put Nginx in front on port 80/443.
- Disable FastAPI docs in production using environment variables -- both apps
  gate their own docs independently off their own env var:

```dotenv
MYOPS_API_ENV=production
PF_SYNC_API_ENV=production
```

- For development, use:

```dotenv
MYOPS_API_ENV=development
PF_SYNC_API_ENV=development
```

If docs are disabled in production, `/docs`, `/redoc`, `/openapi.json`,
`/pf-sync/docs`, `/pf-sync/redoc`, and `/pf-sync/openapi.json` will not be
available.
