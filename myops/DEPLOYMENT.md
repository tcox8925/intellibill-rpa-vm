# MyOps FastAPI Deployment Guide

## Purpose

This guide covers deployment and the one-time Practice Fusion (PF) browser/OTP setup for the combined Tebra + Practice Fusion service.

There are two deployment paths:

1. **Fresh deployment** — brand-new VM.
2. **Update an existing deployment** — existing VM already running the service.

The important browser rule is:

- Tebra can run headless.
- Practice Fusion's **first login must be headed** so a human can complete the OTP/security challenge.
- Use the persistent Chrome profile `~/pf_rpa_chrome` for that first login.
- After the first successful login/device verification, `PF_PLAYWRIGHT_HEADLESS=true` can be used for unattended PF runs.
- If PF later asks for OTP again, temporarily set it back to `false` and repeat the headed login.

---

# 1. Fresh Deployment — Brand-New VM

## 1.1 SSH into the VM

From your local computer:

```bash
ssh ibrcmadmin@20.46.228.47
```

## 1.2 Clone the repository

```bash
git clone <repo-url> ~/intellibill-rpa-vm
cd ~/intellibill-rpa-vm
git checkout dev
```

## 1.3 Create the shared virtual environment

The virtual environment belongs at the **repository root**:

```text
~/intellibill-rpa-vm/venv
```

Create and activate it:

```bash
python3.12 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
python -m playwright install-deps chromium
```

`requirements.txt` at the repository root covers the dependencies needed by both MyOps and PF sync.

## 1.4 Configure `.env`

```bash
cp .env.example .env
```

Set the required production values, including:

```dotenv
MYOPS_API_ENV=production
PF_SYNC_API_ENV=production

EHR_PLAYWRIGHT_HEADLESS=true
PF_PLAYWRIGHT_HEADLESS=false
```

Also configure the required Tebra, Practice Fusion, database, Azure/Graph, and storage variables described by the application.

**Important:** Keep `PF_PLAYWRIGHT_HEADLESS=false` until the first PF login has successfully completed in `~/pf_rpa_chrome`.

---

# 2. One-Time Practice Fusion Browser Setup

This section is the same for a fresh VM or an existing VM that does not yet have the PF browser session.

## 2.1 Install the virtual display and VNC packages

Run on the **VM**:

```bash
sudo apt update
sudo apt install -y xvfb x11vnc openbox
```

Install the Xvfb systemd service from the repository:

```bash
sudo cp ~/intellibill-rpa-vm/myops/xvfb.service /etc/systemd/system/xvfb.service
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb
```

Verify:

```bash
systemctl status xvfb --no-pager
```

You should see:

```text
Active: active (running)
```

Verify that display `:99` is accessible:

```bash
DISPLAY=:99 xdpyinfo >/dev/null && echo "DISPLAY OK"
```

Expected:

```text
DISPLAY OK
```

## 2.2 Start Openbox

Xvfb provides the virtual X display but does not provide a window manager.

Run on the **VM**:

```bash
DISPLAY=:99 openbox &
```

A warning about:

```text
/var/lib/openbox/debian-menu.xml
```

is harmless for this setup.

## 2.3 Start x11vnc

Run on the **VM**.

First make sure an older x11vnc process is not already using port 5900:

```bash
pkill -x x11vnc
```

Then start exactly one x11vnc process:

```bash
x11vnc \
  -display :99 \
  -nopw \
  -localhost \
  -forever \
  -shared \
  -noxdamage \
  -noxfixes \
  -noxrecord \
  -nowf \
  -noscr \
  -wait 5 \
  -defer 5 \
  -rfbport 5900
```

**Leave this terminal running.**

In another VM terminal, verify:

```bash
sudo ss -ltnp | grep 5900
```

Expected:

```text
127.0.0.1:5900 ... x11vnc
```

There should be only one x11vnc process listening on port 5900.

### Important

If you see:

```text
Address already in use
Error: could not obtain listening port
```

another x11vnc process is already running. Run:

```bash
pkill -x x11vnc
```

and start the command again.

---

# 3. Connect From Your Mac Using SSH + TigerVNC

This section is performed on the **Mac**, not on the VM.

## 3.1 Create the SSH tunnel

Open a Mac Terminal:

```bash
ssh -L 5900:127.0.0.1:5900 ibrcmadmin@20.46.228.47
```

Enter the **VM SSH password** when SSH asks for it.

Leave this Terminal open.

This creates:

```text
Mac localhost:5900
        |
        | SSH tunnel
        v
VM localhost:5900
        |
        v
x11vnc
```

## 3.2 Verify the tunnel

Open a **second Mac Terminal**.

Run:

```bash
nc -vz localhost 5900
```

Expected:

```text
Connection to localhost port 5900 [tcp/rfb] succeeded!
```

If this succeeds, the SSH tunnel is working.

If it says connection refused, check that:

1. x11vnc is still running on the VM.
2. x11vnc is listening on port 5900.
3. The SSH tunnel Terminal is still open.

## 3.3 Install TigerVNC on the Mac

Only needed once:

```bash
brew install --cask tigervnc
```

## 3.4 Open TigerVNC

On the **Mac**:

```bash
open -a TigerVNC
```

In the TigerVNC Server field enter:

```text
localhost:5900
```

Then click **Connect**.

### Do not enter a VNC password

x11vnc was started with:

```text
-nopw
```

so VNC authentication is disabled for this localhost-only tunnel.

The VM SSH password is used for the SSH tunnel, **not** for VNC.

### Do not run this on the VM

```bash
open "vnc://localhost:5900"
```

The VNC viewer runs on your Mac. The VM only hosts x11vnc.

---

# 4. Launch the Real Practice Fusion Chrome Profile

Once TigerVNC is connected, the Chrome window must run on the same X display:

```text
DISPLAY=:99
```

The persistent PF profile is:

```text
/home/ibrcmadmin/pf_rpa_chrome
```

## 4.1 Make sure PF is headed

On the VM:

```bash
grep PF_PLAYWRIGHT_HEADLESS .env
```

For the first login, it must be:

```dotenv
PF_PLAYWRIGHT_HEADLESS=false
```

## 4.2 Optional: manually verify Chrome visibility

If you want to verify the browser before running PF automation:

```bash
cd ~/intellibill-rpa-vm

DISPLAY=:99 google-chrome \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --user-data-dir="$HOME/pf_rpa_chrome" \
  --start-maximized
```

Chrome should appear inside TigerVNC.

**Do not use a temporary profile such as `pf_rpa_chrome_test` for the real PF login.**

---

# 5. Complete the First Practice Fusion Login

Once the real Chrome profile is visible in TigerVNC, run the actual PF login flow.

The shared Python environment is at:

```text
~/intellibill-rpa-vm/venv
```

From the repository root:

```bash
cd ~/intellibill-rpa-vm
```

Run:

```bash
DISPLAY=:99 ./venv/bin/python pf_sync_v5_6/pf_soap_sync_v5_16.py pull-report \
  --report-config-json pf_sync_v5_6/config/pf_appointment_report_config.json \
  --output-csv /tmp/pf_report_test.csv \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome"
```

Alternatively, from the PF directory:

```bash
cd ~/intellibill-rpa-vm/pf_sync_v5_6

DISPLAY=:99 ../venv/bin/python pf_soap_sync_v5_16.py pull-report \
  --report-config-json config/pf_appointment_report_config.json \
  --output-csv /tmp/pf_report_test.csv \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome"
```

Watch the TigerVNC window.

When Practice Fusion displays its OTP/security challenge:

1. Complete the login in the visible Chrome window.
2. Enter the OTP directly into Practice Fusion.
3. Complete any security/remember-device step.
4. Wait for the login to finish successfully.

Do **not** send the OTP through SSH or into this documentation.

The important part is that the successful login happens in:

```text
~/pf_rpa_chrome
```

so the persistent browser profile retains the device/session state.

---

# 6. After the First PF Login

Once the first login has succeeded, stop using the manual VNC workflow for normal unattended runs.

Change `.env`:

```dotenv
PF_PLAYWRIGHT_HEADLESS=true
```

Restart the application after changing `.env`:

```bash
sudo systemctl restart myops
```

The normal production PF process can then run headless using the same persistent profile.

If Practice Fusion later requires another OTP/security challenge:

1. Set:
   ```dotenv
   PF_PLAYWRIGHT_HEADLESS=false
   ```
2. Restart the service if it is already running.
3. Start Xvfb/Openbox/x11vnc.
4. Connect from the Mac with TigerVNC.
5. Run the PF login flow on `DISPLAY=:99`.
6. Complete the OTP.
7. Set:
   ```dotenv
   PF_PLAYWRIGHT_HEADLESS=true
   ```
8. Restart the service.

---

# 7. Install the Combined MyOps Systemd Service

Copy the checked-in service file:

```bash
sudo cp ~/intellibill-rpa-vm/myops/myops.service /etc/systemd/system/myops.service
```

Before starting it, verify these values in the copied unit:

```text
User=
WorkingDirectory=
ExecStart=
```

They must match the actual VM username and repository path.

Reload and enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable myops
sudo systemctl start myops
```

The combined service should use the repository-root `server.py`, not the old standalone `myops/server.py`.

The service depends on Xvfb and uses:

```text
DISPLAY=:99
```

for the application environment.

---

# 8. Update an Existing Deployment

Use this section when the VM already has the application deployed.

## 8.1 SSH into the VM

```bash
ssh ibrcmadmin@20.46.228.47
```

## 8.2 Go to the repository

```bash
cd ~/intellibill-rpa-vm
```

## 8.3 Pull the production code

```bash
git checkout prod
git fetch origin
git reset --hard origin/prod
```

## 8.4 Activate the shared venv

```bash
source venv/bin/activate
```

## 8.5 Install dependencies if needed

```bash
pip install -r requirements.txt
```

If Playwright/Chromium needs to be installed or updated:

```bash
python -m playwright install chromium
```

## 8.6 Check PF headless mode

For normal production operation after the first PF login:

```bash
grep PF_PLAYWRIGHT_HEADLESS .env
```

Expected:

```dotenv
PF_PLAYWRIGHT_HEADLESS=true
```

For a first login or OTP recovery:

```dotenv
PF_PLAYWRIGHT_HEADLESS=false
```

## 8.7 Make sure Xvfb exists

If this VM does not already have Xvfb:

```bash
sudo apt update
sudo apt install -y xvfb x11vnc openbox
sudo cp ~/intellibill-rpa-vm/myops/xvfb.service /etc/systemd/system/xvfb.service
sudo systemctl daemon-reload
sudo systemctl enable --now xvfb
```

For an OTP recovery, follow Sections 2–6 of this guide.

## 8.8 Reinstall the combined systemd unit

```bash
sudo cp ~/intellibill-rpa-vm/myops/myops.service /etc/systemd/system/myops.service
sudo systemctl daemon-reload
sudo systemctl restart myops
```

The combined service serves both:

```text
Tebra routes
Practice Fusion routes under /pf-sync
```

If a separate old PF systemd service is still running on port 8011, it should be retired.

---

# 9. Verify the Running Service

Check the service:

```bash
sudo systemctl status myops
```

Check recent logs:

```bash
sudo journalctl -u myops -n 50 --no-pager
```

Check the main health endpoint:

```bash
curl -sS http://127.0.0.1:8010/healthz
```

Expected:

```json
{"status":"ok"}
```

Check PF sync:

```bash
curl -sS http://127.0.0.1:8010/pf-sync/healthz
```

Expected:

```json
{"status":"ok"}
```

Check the retired PF port:

```bash
sudo ss -tulpn | grep 8011
```

Expected:

```text
no output
```

Check the active application port:

```bash
sudo ss -tulpn | grep 8010
```

---

# 10. Production Configuration

Keep Uvicorn bound to:

```text
127.0.0.1:8010
```

Put Nginx in front of the application for external HTTP/HTTPS access.

Production `.env`:

```dotenv
MYOPS_API_ENV=production
PF_SYNC_API_ENV=production
```

Development `.env`:

```dotenv
MYOPS_API_ENV=development
PF_SYNC_API_ENV=development
```

`.env` is the source of truth for these values.

After changing `.env`:

```bash
sudo systemctl restart myops
```

Changing `.env` alone does not change an already-running process.

---

# 11. Troubleshooting

## VNC: `Address already in use`

Run:

```bash
pkill -x x11vnc
sudo ss -ltnp | grep 5900
```

Start one clean x11vnc process again.

## Mac: `nc -vz localhost 5900` fails

Make sure the Mac SSH tunnel is still open:

```bash
ssh -L 5900:127.0.0.1:5900 ibrcmadmin@20.46.228.47
```

Then from another Mac Terminal:

```bash
nc -vz localhost 5900
```

## TigerVNC connects but screen is blank

On the VM verify:

```bash
systemctl status xvfb --no-pager
DISPLAY=:99 xdpyinfo >/dev/null && echo "DISPLAY OK"
```

Make sure Openbox is running:

```bash
DISPLAY=:99 openbox &
```

Make sure Chrome is running on the same display:

```bash
pgrep -a -f '/opt/google/chrome/chrome'
```

The Chrome window can be inspected with:

```bash
DISPLAY=:99 xwininfo -root -tree
```

A Chrome window should appear in the output.

## `xterm: command not found`

This is not a Chrome or VNC failure. `xterm` is not required.

## Chrome DBus / UPower warning

Messages mentioning:

```text
org.freedesktop.UPower
```

are not by themselves a PF login failure on this minimal VM.

## Chrome profile

The real persistent profile is:

```text
~/pf_rpa_chrome
```

Do not replace it with:

```text
~/pf_rpa_chrome_test
```

for the actual PF login.

## Python path

From:

```text
~/intellibill-rpa-vm
```

use:

```bash
./venv/bin/python
```

From:

```text
~/intellibill-rpa-vm/pf_sync_v5_6
```

use:

```bash
../venv/bin/python
```

Do not use:

```bash
../venv/bin/python
```

while you are already at the repository root.

---

# 12. Quick First-Time PF Login Checklist

For a first-time PF OTP login, the complete sequence is:

### VM — Terminal 1

```bash
sudo systemctl status xvfb --no-pager
```

### VM — Terminal 2

```bash
DISPLAY=:99 openbox &
```

### VM — Terminal 3

```bash
pkill -x x11vnc

x11vnc \
  -display :99 \
  -nopw \
  -localhost \
  -forever \
  -shared \
  -noxdamage \
  -noxfixes \
  -noxrecord \
  -nowf \
  -noscr \
  -wait 5 \
  -defer 5 \
  -rfbport 5900
```

### Mac — Terminal 1

```bash
ssh -L 5900:127.0.0.1:5900 ibrcmadmin@20.46.228.47
```

### Mac — Terminal 2

```bash
nc -vz localhost 5900
```

Expected:

```text
Connection to localhost port 5900 [tcp/rfb] succeeded!
```

### Mac — TigerVNC

Connect to:

```text
localhost:5900
```

### VM — PF login

```bash
cd ~/intellibill-rpa-vm

DISPLAY=:99 ./venv/bin/python pf_sync_v5_6/pf_soap_sync_v5_16.py pull-report \
  --report-config-json pf_sync_v5_6/config/pf_appointment_report_config.json \
  --output-csv /tmp/pf_report_test.csv \
  --chrome-user-data-dir "$HOME/pf_rpa_chrome"
```

Complete the OTP/security challenge in the visible Chrome window.

After successful first login:

```dotenv
PF_PLAYWRIGHT_HEADLESS=true
```

Then:

```bash
sudo systemctl restart myops
```

---

# 13. Useful Commands

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

Check port 8010:

```bash
sudo ss -tulpn | grep 8010
```

Check Xvfb:

```bash
systemctl status xvfb --no-pager
```

Check VNC:

```bash
sudo ss -ltnp | grep 5900
```
