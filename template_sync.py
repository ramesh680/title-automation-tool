"""
template_sync.py
----------------
Keeps the Generator and Review on the LATEST Ops ingest templates in Google
Drive, so a template update needs no code change or redeploy.

For each category the newest Google Sheet in the templates folder whose NAME
matches the category is used (shortcuts are followed to their target), unless
a file is PINNED for that category (see TEMPLATE_PIN_* below):

    film    "Movies" or "Film"   -> pinned: "NEW Ingest Template - Film w/ Language"
    tv      "TV"                 -> "New Brand Definitions Ingest Template - TV"
    talent  "Talent"             -> "Ingest Template - Talent"
    game    "Video Game"         -> "New Video Game - Ingest Template"

The file is exported as .xlsx, parsed into fresh tables and checked (expected
sheets present, a sensible number of rows) BEFORE the live tables are switched.
Anything that fails keeps the last good copy -- the bundled reference/ files
at first -- so a bad edit or an outage can never break generation.

Setup (Render environment variables):
    GOOGLE_SERVICE_ACCOUNT_JSON   service-account key JSON (raw or base64);
                                  the account needs Viewer access to the
                                  folder AND to the files its shortcuts point to
    TEMPLATE_DRIVE_FOLDER_ID      defaults to the Ops templates folder
    TEMPLATE_REFRESH_HOURS        default 6
    TEMPLATE_PIN_FILM             exact file name to use for Movies / Film
                                  (default "NEW Ingest Template - Film w/ Language";
                                  set it to "NEW Ingest Template - Movies (2026)"
                                  to switch, or to "" to use the newest file)
    TEMPLATE_PIN_TV / _TALENT / _GAME   same, for the other categories (unset)
Without credentials the module stays idle and the bundled copies are used.
"""

import base64
import json
import logging
import os
import re
import tempfile
import threading
import time
from datetime import datetime, timezone

import reference_data as REF

log = logging.getLogger(__name__)

DRIVE = "https://www.googleapis.com/drive/v3"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
GSHEET = "application/vnd.google-apps.spreadsheet"
SHORTCUT = "application/vnd.google-apps.shortcut"

FOLDER_ID = os.getenv("TEMPLATE_DRIVE_FOLDER_ID", "1V1wm8iyHJTK2K0m6vdNKcu68yr_doSqo")
REFRESH_HOURS = float(os.getenv("TEMPLATE_REFRESH_HOURS", "6"))
CACHE_DIR = os.getenv("TEMPLATE_CACHE_DIR",
                      os.path.join(tempfile.gettempdir(), "titleforge_templates"))

# category -> (name patterns, required sheets, minimum rows per table)
CATEGORIES = {
    "film": ([r"\bmovies?\b", r"\bfilms?\b"],
             ("DropDown", "Media Conglomerate Brand Sets"),
             {"FILM_STUDIOS": 50}),
    "tv": ([r"\btv\b"],
           ("DropdownLists", "Conglomerate Brand Sets"),
           {"TV_NETWORKS": 50, "TV_CONGLOMERATES": 20}),
    "talent": ([r"\btalent\b"], ("DropDown",), {"TALENT_TYPES": 5}),
    "game": ([r"\bvideo\s*-?\s*games?\b", r"\bvideogames?\b"], ("DropDown",),
             {"GAME_PUBLISHERS": 50}),
}
LABELS = {"film": "Movies / Film", "tv": "TV", "talent": "Talent", "game": "Video Game"}
# Owner decision (Nov 2026): Movies / Film stays on the "Film w/ Language"
# template for the next couple of months, then moves to "Movies (2026)" --
# switched by changing TEMPLATE_PIN_FILM on Render, no code change.
_PIN_DEFAULTS = {"film": "NEW Ingest Template - Film w/ Language"}


def pins():
    """category -> pinned file name ('' = use the newest matching file)."""
    return {c: os.getenv(f"TEMPLATE_PIN_{c.upper()}", _PIN_DEFAULTS.get(c, "")).strip()
            for c in CATEGORIES}
# never pick an obvious archive / working copy
_SKIP = re.compile(r"\b(old|archive[d]?|backup|deprecated|copy of|do not use)\b", re.I)

_lock = threading.Lock()
_thread = None
STATUS = {c: {"source": "bundled", "file": "", "file_id": "", "modified": "",
              "pinned": False, "checked": "", "error": ""} for c in CATEGORIES}
STATE = {"enabled": False, "last_run": "", "last_error": ""}


# ---------------- auth ----------------
def _credentials_info():
    raw = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return json.loads(base64.b64decode(raw).decode("utf-8"))


def _session():
    """Authorized requests session, or None when no credentials are set."""
    info = _credentials_info()
    if not info:
        return None
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    creds = service_account.Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/drive.readonly"])
    return AuthorizedSession(creds)


# ---------------- selection ----------------
def category_for(name):
    """The category a file NAME belongs to, or None (archives are skipped)."""
    n = str(name or "")
    if _SKIP.search(n):
        return None
    for cat, (pats, _sheets, _mins) in CATEGORIES.items():
        if any(re.search(p, n, re.I) for p in pats):
            return cat
    return None


def list_folder(sess, folder_id=FOLDER_ID):
    """Every file in the folder, shortcuts resolved to their target's
    id / mimeType / modifiedTime (the name stays the one shown in the folder)."""
    files, token = [], None
    while True:
        r = sess.get(f"{DRIVE}/files", params={
            "q": f"'{folder_id}' in parents and trashed=false",
            "fields": "nextPageToken,files(id,name,mimeType,modifiedTime,shortcutDetails)",
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
            "corpora": "allDrives", "pageSize": 200, **({"pageToken": token} if token else {})},
            timeout=30)
        r.raise_for_status()
        data = r.json()
        files += data.get("files", [])
        token = data.get("nextPageToken")
        if not token:
            break
    out = []
    for f in files:
        if f.get("mimeType") == SHORTCUT:
            tid = (f.get("shortcutDetails") or {}).get("targetId")
            if not tid:
                continue
            t = sess.get(f"{DRIVE}/files/{tid}", params={
                "fields": "id,mimeType,modifiedTime", "supportsAllDrives": "true"}, timeout=30)
            if t.status_code != 200:
                out.append(dict(name=f["name"], id=tid, mimeType="", modifiedTime="",
                                error=f"shortcut target not accessible ({t.status_code})"))
                continue
            t = t.json()
            out.append(dict(name=f["name"], id=t["id"], mimeType=t.get("mimeType", ""),
                            modifiedTime=t.get("modifiedTime", "")))
        else:
            out.append(dict(name=f["name"], id=f["id"], mimeType=f.get("mimeType", ""),
                            modifiedTime=f.get("modifiedTime", "")))
    return out


def pick_latest(files, pinned=None):
    """category -> the spreadsheet to use: the PINNED file name when one is set
    and present in the folder, otherwise the newest file whose name matches the
    category. A pinned name that is missing falls back to the newest match and
    is reported as f['pin_missing']."""
    pinned = pins() if pinned is None else pinned
    newest, pinned_hit = {}, {}
    for f in files:
        cat = category_for(f["name"])
        if not cat or f.get("error") or f.get("mimeType") not in (GSHEET, XLSX):
            continue
        if cat not in newest or f.get("modifiedTime", "") > newest[cat].get("modifiedTime", ""):
            newest[cat] = f
        want = pinned.get(cat, "")
        if want and f["name"].strip().lower() == want.lower():
            if cat not in pinned_hit or \
                    f.get("modifiedTime", "") > pinned_hit[cat].get("modifiedTime", ""):
                pinned_hit[cat] = f
    best = {}
    for cat, f in newest.items():
        if cat in pinned_hit:
            best[cat] = dict(pinned_hit[cat], pinned=True)
        elif pinned.get(cat):
            best[cat] = dict(f, pin_missing=pinned[cat])
        else:
            best[cat] = f
    return best


# ---------------- download + validate ----------------
def download(sess, f):
    if f["mimeType"] == GSHEET:
        r = sess.get(f"{DRIVE}/files/{f['id']}/export", params={"mimeType": XLSX}, timeout=120)
    else:
        r = sess.get(f"{DRIVE}/files/{f['id']}",
                     params={"alt": "media", "supportsAllDrives": "true"}, timeout=120)
    r.raise_for_status()
    return r.content


def validate(category, path):
    """Parsed tables for a downloaded template, or raise ValueError saying why
    it was rejected (missing sheet, too few rows)."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True)
    names = set(wb.sheetnames)
    wb.close()
    missing = [s for s in CATEGORIES[category][1] if s not in names]
    if missing:
        raise ValueError("missing sheet(s): " + ", ".join(missing))
    tables = REF.parse_category(category, path)
    for table, minimum in CATEGORIES[category][2].items():
        if len(tables[table]) < minimum:
            raise ValueError(f"{table} has only {len(tables[table])} rows (expected >= {minimum})")
    return tables


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _after_install():
    """Drop caches built from the previous templates."""
    try:
        import network_youtube
        getattr(network_youtube, "_CACHE", {}).clear()
    except Exception:  # noqa: BLE001
        pass


def sync(force=False, sess=None):
    """One pass: check the folder and switch any category whose newest template
    changed. Returns STATUS. Never raises."""
    with _lock:
        STATE["last_run"] = _now()
        try:
            sess = sess or _session()
            if sess is None:
                STATE.update(enabled=False,
                             last_error="GOOGLE_SERVICE_ACCOUNT_JSON is not set; "
                                        "using the bundled template copies")
                return STATUS
            STATE["enabled"] = True
            latest = pick_latest(list_folder(sess))
            os.makedirs(CACHE_DIR, exist_ok=True)
            for cat in CATEGORIES:
                st = STATUS[cat]
                st["checked"] = _now()
                f = latest.get(cat)
                if not f:
                    st["error"] = "no template in the folder matches this category"
                    continue
                if (not force and st["source"] == "drive" and st["file_id"] == f["id"]
                        and st["modified"] == f.get("modifiedTime", "")):
                    continue                      # unchanged since the last pass
                try:
                    path = os.path.join(CACHE_DIR, f"{cat}.xlsx")
                    tmp = os.path.join(CACHE_DIR, f"{cat}.incoming.xlsx")
                    with open(tmp, "wb") as fh:
                        fh.write(download(sess, f))
                    tables = validate(cat, tmp)
                    os.replace(tmp, path)
                    REF.install(cat, tables, path)
                    _after_install()
                    st.update(source="drive", file=f["name"], file_id=f["id"],
                              modified=f.get("modifiedTime", ""), pinned=bool(f.get("pinned")),
                              error=(f"pinned file '{f['pin_missing']}' is not in the folder; "
                                     "using the newest matching file") if f.get("pin_missing") else "")
                    log.info("template %s -> %s (%s)", cat, f["name"], f.get("modifiedTime"))
                except Exception as e:  # noqa: BLE001 - keep the last good copy
                    st["error"] = f"kept previous copy: {e}"
                    log.warning("template %s rejected (%s): %s", cat, f.get("name"), e)
            STATE["last_error"] = ""
        except Exception as e:  # noqa: BLE001
            STATE["last_error"] = str(e)
            log.warning("template sync failed: %s", e)
        return STATUS


def status():
    c = REF.counts()
    rows = {
        "film": {"studios": c["FILM_STUDIOS"], "roll_ups": c["FILM_ROLLUPS"]},
        "tv": {"networks": c["TV_NETWORKS"], "conglomerates": c["TV_CONGLOMERATES"]},
        "talent": {"talent_types": c["TALENT_TYPES"], "subtypes": c["TALENT_SUBTYPES"]},
        "game": {"publishers": c["GAME_PUBLISHERS"], "developers": c["GAME_DEVELOPERS"]},
    }
    return {
        "enabled": STATE["enabled"], "folder_id": FOLDER_ID,
        "last_run": STATE["last_run"], "last_error": STATE["last_error"],
        "refresh_hours": REFRESH_HOURS, "pins": pins(),
        "categories": {cat: dict(STATUS[cat], label=LABELS[cat], loaded=rows[cat])
                       for cat in CATEGORIES},
    }


def _loop():
    while True:
        sync()
        time.sleep(max(REFRESH_HOURS, 0.25) * 3600)


def start():
    """Start the background sync once (no-op without credentials)."""
    global _thread
    if _thread is not None or not os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return False
    _thread = threading.Thread(target=_loop, name="template-sync", daemon=True)
    _thread.start()
    return True
