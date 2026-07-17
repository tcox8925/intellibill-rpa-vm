# MyOps FastAPI Deployment Guide

Deployment and update steps after initial server setup.

## 1) SSH into the VM

```bash
ssh ibrcmadmin@20.46.228.47
```

## 2) Navigate to the project

```bash
cd ~/intellibill-rpa-vm
```

## 3) Pull latest code

```bash
git checkout prod
git fetch origin
git reset --hard origin/prod
```

## 4) Activate virtual environment

```bash
source venv/bin/activate
```

## 5) Install dependencies (only if requirements changed)

```bash
pip install -r requirements.txt
```

## 6) Restart service

```bash
sudo systemctl daemon-reload
sudo systemctl restart myops
```

## 7) Verify service

```bash
sudo systemctl status myops
sudo journalctl -u myops -n 50 --no-pager
```

## 8) Verify locally on VM

```bash
curl -sS http://127.0.0.1:8010/healthz
```

Expected response:

```json
{"status":"ok"}
```

## 9) Production URL

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
- Disable FastAPI docs in production using environment variable:

```dotenv
MYOPS_API_ENV=production
```

- For development, use:

```dotenv
MYOPS_API_ENV=development
```

If docs are disabled in production, `/docs`, `/redoc`, and `/openapi.json` will not be available.
