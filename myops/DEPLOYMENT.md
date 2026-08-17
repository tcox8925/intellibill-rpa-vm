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

Both Tebra and Practice Fusion sync drive a **real Chrome browser**, but they
differ in what that actually requires on a headless Linux VM (no monitor, no
X server by default):

- **Tebra can run fully headless.** `myops/ehr/pipeline.py` calls
  `playwright.chromium.launch(headless=...)` directly, and its OTP step is
  automated by reading the code from an email mailbox via Microsoft Graph
  (`otp_info.py`/`email_read.py`) -- no human ever needs to see the browser.
  Set `EHR_PLAYWRIGHT_HEADLESS=true` in `.env` explicitly (don't leave it
  blank -- see the Xvfb note below for why) and follow `myops/README.md`
  (§4a) once to wire up the Graph OTP-mailbox app.
- **Practice Fusion sync can run headless too, but only *after* its first
  login.** `pf_sync_pkg/browser.py`'s `_pf_headless()` reads
  `PF_PLAYWRIGHT_HEADLESS` from `.env` (same true/false parsing as
  `EHR_PLAYWRIGHT_HEADLESS`) and adds real Chrome's own `--headless=new` flag
  when it's true -- same profile, same binary, just off-screen. Its OTP/
  security-check step still has **no automated reader**, though -- a human
  has to see and solve it -- so:
  - **Leave `PF_PLAYWRIGHT_HEADLESS=false` (the default) until the first
    login below has already succeeded** in this exact
    `chrome_user_data_dir` (`~/pf_rpa_chrome`).
  - **Only flip it to `true` afterward**, for ongoing unattended runs. PF's
    "remember this device" session persists in that Chrome profile across
    runs, so headless calls shouldn't hit the OTP screen again -- but if PF
    ever does re-challenge it, a headless run has no way for anyone to see or
    solve it and will just time out after `login_timeout_seconds`. Set it
    back to `false` and re-run the login step below (via VNC) to recover.
  - Either way, on this display-less VM the **first login itself always
    needs**:
    1. A **virtual display (Xvfb)** so headed Chrome has somewhere to render
       into at all -- without this it crashes immediately with
       `Missing X server or $DISPLAY`.
    2. A **VNC session** into that virtual display, to actually see and solve
       Practice Fusion's OTP challenge.

**Set up Xvfb (once, before anything else in this section):**

```bash
sudo apt update
sudo apt install -y xvfb x11vnc
sudo cp ~/intellibill-rpa-vm/myops/xvfb.service /etc/systemd/system/xvfb.service
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb
```

`myops.service` (see step 5 below) already depends on `xvfb.service` and sets
`DISPLAY=:99` for the app -- **important side effect**: this also makes
`DISPLAY` visible to Tebra's own auto-headless-detection in
`myops/ehr/config.py`, which otherwise defaults to headless *because* no
`DISPLAY` was set. With Xvfb's `DISPLAY=:99` now present, that auto-detection
would flip Tebra to headed too unless `EHR_PLAYWRIGHT_HEADLESS=true` is set
explicitly in `.env` -- which is why that's called out above as required, not
optional, once Xvfb is in the picture.

**Log into Practice Fusion once, via VNC on the virtual display:**

```bash
# On the VM: start a temporary VNC server pointed at the Xvfb display
x11vnc -display :99 -nopw -listen localhost -xkb &

# On your laptop: tunnel that port over SSH, then connect a VNC client to localhost:5900
ssh -L 5900:localhost:5900 ibrcmadmin@20.46.228.47
# (in a VNC client) connect to localhost:5900

# Now, ALSO on the VM, run pf_sync's login flow with DISPLAY=:99 so Chrome
# renders into the display you're VNC'd into -- follow pf_sync_v5_6/README.md
# §1's login steps, e.g.:
cd ~/intellibill-rpa-vm/pf_sync_v5_6
DISPLAY=:99 ../venv/bin/python pf_soap_sync_v5_16.py doctor \
  --config-json config/pf_pdf_sync_config.json \
  --report-config-json config/pf_appointment_report_config.json
# watch the VNC window, solve the OTP challenge when it appears, then close
# the temporary x11vnc process (kill %1) once login succeeds
```

Skip this and the first real API call will either hang waiting for a login
prompt no one can see, or crash immediately with a `Missing X server`
error -- see whichever applies based on whether Xvfb is installed yet.

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

**If this VM didn't already have a virtual display set up**: the updated
`myops.service` now sets `Environment=DISPLAY=:99` and depends on
`xvfb.service` -- if that unit doesn't exist yet on the VM, `myops` will fail
to start (or Chrome-driving endpoints will crash with `Missing X server or
$DISPLAY`). Do the [Xvfb + one-time Practice Fusion login setup](#4-one-time-rpabrowser-setup-do-this-before-starting-the-service)
from the fresh-deployment section above before restarting `myops` -- it
applies here too, not just to brand-new VMs.

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

**`.env` is the single source of truth for these two vars** -- `myops.service`
only does `EnvironmentFile=.../.env`, it does NOT hardcode `Environment=` lines
for `MYOPS_API_ENV`/`PF_SYNC_API_ENV` on top of that. If you ever add an
`Environment=MYOPS_API_ENV=...` (or `PF_SYNC_API_ENV=...`) line directly to the
unit file, know that it silently wins over whatever `.env` says (systemd
applies `EnvironmentFile=` first, then explicit `Environment=` lines override
it) -- editing `.env` afterward would look like it does nothing.

After changing `.env`, restart for it to take effect -- editing the file alone
doesn't affect an already-running process:

```bash
sudo systemctl restart myops
```

If docs are disabled in production, `/docs`, `/redoc`, `/openapi.json`,
`/pf-sync/docs`, `/pf-sync/redoc`, and `/pf-sync/openapi.json` will not be
available.
