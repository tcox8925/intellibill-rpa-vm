"""
Combined RPA API entrypoint
===========================
Single FastAPI/uvicorn process serving both:
  - Tebra RPA API    (myops/server.py)       -- routes unprefixed at root,
                                                  exactly as they always were
                                                  (/healthz, /run-tebra, ...).
  - Practice Fusion sync API (pf_sync_v5_6/server.py) -- routes under /pf-sync
                                                  (/pf-sync/healthz, ...).

Both are loaded and mounted here, in this neutral top-level file, rather than
one being folded into the other's own server.py -- myops/server.py and
pf_sync_v5_6/server.py stay untouched, ordinary standalone-runnable modules;
this is the only file that knows about both of them at once.

Run it with (from the repo root, either app's requirements installed into the
active venv -- see myops/requirements.txt, which now covers both):
    python -m uvicorn server:app --host 0.0.0.0 --port 8010

Deliberately cwd-independent: both sub-apps' modules are loaded by absolute
file path with their own directory pushed onto sys.path (see _load_app
below), and each one resolves its own relative file/config defaults and its
own .env loading via __file__-relative paths internally -- so this can be run
from any working directory, not just the repo root.
"""

import importlib.util
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

REPO_ROOT = Path(__file__).resolve().parent
MYOPS_DIR = REPO_ROOT / "myops"
PF_SYNC_DIR = REPO_ROOT / "pf_sync_v5_6"
PATIENT_SYNC_DIR = REPO_ROOT / "tebra_patient_sync"


def _load_app(module_name: str, dir_path: Path, file_name: str = "server.py"):
    """Load a sub-app's server.py by explicit file path and return the loaded
    module (so callers can grab both `.app` and anything else off it).

    Not a plain `import server` -- myops/server.py and pf_sync_v5_6/server.py
    are both literally modules named `server`, so importing by name would
    have the second import collide with/shadow the first in sys.modules.
    dir_path is pushed onto sys.path first because each sub-app's internal
    imports (myops's `from ehr...`, `from otp_info...`; pf_sync's
    `from pf_sync_pkg...`) assume their own directory is directly importable,
    the same way it is when each is run standalone from inside its own dir.
    """
    sys.path.insert(0, str(dir_path))
    spec = importlib.util.spec_from_file_location(module_name, dir_path / file_name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


tebra_module = _load_app("tebra_server", MYOPS_DIR)
pf_sync_module = _load_app("pf_sync_server", PF_SYNC_DIR)

tebra_app = tebra_module.app
pf_sync_app = pf_sync_module.app

# Tebra patient-sync (SOAP GetPatients -> patient_header/patient_coverages) --
# separate from tebra_app above, which is the Playwright RPA facesheet
# puller. Loaded defensively: tebra_patient_sync/tebra/tebra_api.py builds a
# zeep SOAP client from TEBRA_WSDL_URL/TEBRA_CUSTOMER_KEY/TEBRA_USERNAME at
# *import time*, so a missing/bad env var there must not be able to crash
# the whole combined process (and take the working RPA/pf-sync APIs down
# with it) -- if it fails to load, log it and mount everything else anyway.
patient_sync_app = None
try:
    patient_sync_module = _load_app("patient_sync_server", PATIENT_SYNC_DIR, file_name="app_tebra.py")
    patient_sync_app = patient_sync_module.app
except Exception as e:
    print(f"[SERVER] Tebra patient-sync failed to load, mounting without it: {e!r}", flush=True)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Mounting an app under another one does NOT make Starlette forward the
    # ASGI lifespan protocol into it -- a sub-app's own lifespan/startup
    # handlers simply never fire just because it's mounted. tebra_module
    # exposes its startup logic (schema migration check) as a module-level
    # `lifespan` context manager for exactly this reason -- compose it here
    # so it still runs once at process start, without duplicating its logic.
    async with tebra_module.lifespan(tebra_app):
        yield


# docs/openapi/redoc disabled on this wrapper app on purpose: FastAPI adds
# those routes at __init__ time, before the mounts below are registered, so
# an enabled root doc route would intercept "/docs" itself (with an empty
# schema, since it can't see into opaque ASGI mounts) instead of letting the
# request pass through the "/" mount to Tebra's own real /docs. Leaving these
# off here means "/docs" and "/pf-sync/docs" each resolve to their own
# sub-app's real (env-gated) docs, unchanged from standalone behavior.
app = FastAPI(title="Combined RPA API", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

# Order matters: Starlette matches mounted routes in registration order, and
# a Mount("/") matches every path -- so more specific mounts ("/pf-sync",
# "/patient-sync") must be registered first, or they'd never be reached.
app.mount("/pf-sync", pf_sync_app)
if patient_sync_app is not None:
    app.mount("/patient-sync", patient_sync_app)
app.mount("/", tebra_app)
