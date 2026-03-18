# services/scraper_service.py
"""
Scraper Service — Selenium-based course catalog scraper.

All functions that need a DB app context accept a `flask_app` parameter
and call `with flask_app.app_context(): ...` internally, making them
safe to call from background threads.

SECURITY:
  - No credentials are stored anywhere.
  - Chrome is launched / reused via remote-debugging on port 9222.
  - The admin must log in manually before using any probe / scrape feature.
"""
from __future__ import annotations

import json
import logging
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import traceback
from datetime import datetime
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from typing import List, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────
#  Global in-process scraper state
# ──────────────────────────────────────────────────────
_scraper_lock = threading.Lock()          # prevents concurrent runs
_scraper_is_running = False
_scraper_pause_event = threading.Event()
_scraper_stop_event = threading.Event()
_active_driver = None                     # reused WebDriver instance
_active_debugger_address = None
_active_driver_lock = threading.Lock()
_runtime_config_lock = threading.Lock()
_runtime_config: dict = {
    "popup_open_timeout": 10.0,
    "popup_close_delay": 0.35,
    "inter_row_delay": 0.0,
}

# Shared progress dict – written by worker thread, read by status endpoint
scraper_progress: dict = {
    "status": "idle",          # idle | running | completed | failed
    "dataset_id": None,
    "current_index": 0,
    "total_courses": 0,
    "last_course": "",
    "log": [],                 # list of log strings (capped at 200 entries)
    "error": None,
}
_progress_lock = threading.Lock()
_last_backend_error: dict = {
    "timestamp": None,
    "stage": None,
    "message": None,
    "traceback": None,
}

_probe_lock = threading.Lock()
probe_progress: dict = {
    "status": "idle",            # idle | running | completed | failed
    "job_id": None,
    "fields": [],
    "message": None,
    "error": None,
    "started_at": None,
    "finished_at": None,
}
_probe_progress_lock = threading.Lock()


def _log_progress(msg: str) -> None:
    """Thread-safe append to the in-memory log."""
    with _progress_lock:
        scraper_progress["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if len(scraper_progress["log"]) > 200:
            scraper_progress["log"] = scraper_progress["log"][-200:]


def is_scraper_running() -> bool:
    with _progress_lock:
        return bool(_scraper_is_running)


def is_scraper_paused() -> bool:
    return _scraper_pause_event.is_set()


def get_runtime_config() -> dict:
    with _runtime_config_lock:
        return dict(_runtime_config)


def update_runtime_config(
    *,
    popup_open_timeout: Optional[float] = None,
    popup_close_delay: Optional[float] = None,
    inter_row_delay: Optional[float] = None,
) -> dict:
    with _runtime_config_lock:
        if popup_open_timeout is not None:
            _runtime_config["popup_open_timeout"] = max(2.0, min(float(popup_open_timeout), 40.0))
        if popup_close_delay is not None:
            _runtime_config["popup_close_delay"] = max(0.0, min(float(popup_close_delay), 5.0))
        if inter_row_delay is not None:
            _runtime_config["inter_row_delay"] = max(0.0, min(float(inter_row_delay), 15.0))
        return dict(_runtime_config)


def request_stop_scraper() -> dict:
    should_log = False
    with _progress_lock:
        if not _scraper_is_running:
            return {
                "success": False,
                "message": "No active scraping job to stop.",
                "status": scraper_progress.get("status") or "idle",
            }
        _scraper_stop_event.set()
        if scraper_progress.get("status") not in {"stopping", "stopped"}:
            scraper_progress["status"] = "stopping"
            should_log = True
    if should_log:
        _log_progress("🛑 Stop requested by admin. Finishing current step…")
    return {"success": True, "message": "Stop requested.", "status": "stopping"}


def pause_scraper() -> dict:
    should_log = False
    with _progress_lock:
        if not _scraper_is_running:
            return {
                "success": False,
                "message": "No active scraping job to pause.",
                "status": scraper_progress.get("status") or "idle",
            }
        _scraper_pause_event.set()
        if scraper_progress.get("status") != "paused":
            scraper_progress["status"] = "paused"
            should_log = True
    if should_log:
        _log_progress("⏸️ Scraper paused by admin.")
    return {"success": True, "message": "Scraper paused.", "status": "paused"}


def resume_scraper() -> dict:
    should_log = False
    with _progress_lock:
        if not _scraper_is_running:
            return {
                "success": False,
                "message": "No active scraping job to resume.",
                "status": scraper_progress.get("status") or "idle",
            }
        _scraper_pause_event.clear()
        if scraper_progress.get("status") != "running":
            scraper_progress["status"] = "running"
            should_log = True
    if should_log:
        _log_progress("▶️ Scraper resumed by admin.")
    return {"success": True, "message": "Scraper resumed.", "status": "running"}


def scraper_state_label() -> str:
    with _progress_lock:
        status = str(scraper_progress.get("status") or "idle")
        if _scraper_is_running and _scraper_stop_event.is_set():
            return "stopping"
        if _scraper_is_running and _scraper_pause_event.is_set():
            return "paused"
        if _scraper_is_running and status not in {"running", "paused", "failed"}:
            return "running"
        return status


def _set_last_backend_error(stage: str, exc: Exception | str) -> None:
    message = str(exc)
    tb = None
    if isinstance(exc, Exception):
        tb = traceback.format_exc(limit=12)
    with _progress_lock:
        _last_backend_error.update(
            {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "stage": str(stage or "unknown"),
                "message": message,
                "traceback": tb,
            }
        )


def _compact_exc_text(exc: Exception | str) -> str:
    text = str(exc or "").strip()
    if not text:
        return "Unknown error"
    if "Stacktrace:" in text:
        text = text.split("Stacktrace:", 1)[0].strip()
    return text or "Unknown error"


def _clear_last_backend_error() -> None:
    with _progress_lock:
        _last_backend_error.update(
            {
                "timestamp": None,
                "stage": None,
                "message": None,
                "traceback": None,
            }
        )


def get_last_backend_error() -> dict:
    with _progress_lock:
        return dict(_last_backend_error)


def get_probe_progress() -> dict:
    with _probe_progress_lock:
        return dict(probe_progress)


def _set_probe_progress(**kwargs) -> None:
    with _probe_progress_lock:
        for key, value in kwargs.items():
            probe_progress[key] = value


def is_probe_running() -> bool:
    with _probe_progress_lock:
        return probe_progress.get("status") == "running"


def get_scraper_diagnostics(debugger_address: Optional[str] = None) -> dict:
    host, port, normalized = _split_debugger_address(debugger_address)
    debugger_http_ok, debugger_http_message, debugger_http_working_address = _debugger_http_health(normalized)
    selenium_available = True
    selenium_version = None
    selenium_error = None
    try:
        import selenium  # type: ignore
        selenium_version = getattr(selenium, "__version__", None)
    except Exception as exc:
        selenium_available = False
        selenium_error = str(exc)

    chrome_exec = None
    chrome_exec_error = None
    if _is_localhost(host):
        try:
            chrome_exec = _chrome_executable()
        except Exception as exc:
            chrome_exec_error = str(exc)

    return {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "hosted_environment": _is_hosted_environment(),
        "debugger_address": normalized,
        "debugger_host": host,
        "debugger_port": port,
        "debugger_reachable": _is_chrome_running(normalized),
        "debugger_http_ok": debugger_http_ok,
        "debugger_http_message": debugger_http_message,
        "debugger_http_working_address": debugger_http_working_address,
        "target_is_local": _is_localhost(host),
        "selenium_available": selenium_available,
        "selenium_version": selenium_version,
        "selenium_error": selenium_error,
        "chrome_executable": chrome_exec,
        "chrome_executable_error": chrome_exec_error,
        "active_driver_cached": _active_driver is not None,
        "active_debugger_address": _active_debugger_address,
        "last_backend_error": get_last_backend_error(),
        "progress_status": dict(scraper_progress),
    }


# ──────────────────────────────────────────────────────
#  Chrome / WebDriver helpers
# ──────────────────────────────────────────────────────
_DEBUGGING_PORT = 9222
_DEFAULT_DEBUGGER_ADDRESS = "localhost:9222"


def _is_hosted_environment() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("DYNO") or os.environ.get("K_SERVICE"))


def _normalize_debugger_address(debugger_address: Optional[str] = None) -> str:
    candidate = (debugger_address or os.environ.get("CHROME_DEBUGGER_ADDRESS") or _DEFAULT_DEBUGGER_ADDRESS).strip()
    if not candidate:
        return _DEFAULT_DEBUGGER_ADDRESS

    if candidate.startswith("http://") or candidate.startswith("https://"):
        parsed = urlparse(candidate)
        host = parsed.hostname or "localhost"
        port = parsed.port or _DEBUGGING_PORT
        return f"{host}:{port}"
    return candidate


def _split_debugger_address(debugger_address: Optional[str] = None) -> tuple[str, int, str]:
    normalized = _normalize_debugger_address(debugger_address)
    if ":" in normalized:
        host, raw_port = normalized.rsplit(":", 1)
    else:
        host, raw_port = normalized, str(_DEBUGGING_PORT)
    host = (host or "localhost").strip()
    try:
        port = int(raw_port)
    except Exception:
        port = _DEBUGGING_PORT
    return host, port, f"{host}:{port}"


def _is_localhost(host: str) -> bool:
    return str(host or "").strip().lower() in {"localhost", "127.0.0.1", "::1"}


def _is_chrome_running(debugger_address: Optional[str] = None) -> bool:
    """Return True if the remote debugging endpoint is reachable."""
    import socket
    host, port, _ = _split_debugger_address(debugger_address)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


def _resolve_host_ip(host: str) -> str | None:
    import socket

    try:
        resolved = socket.gethostbyname(host)
        return resolved.strip() if resolved else None
    except Exception:
        return None


def _debugger_http_health(debugger_address: Optional[str] = None) -> tuple[bool, str, str]:
    """Check DevTools endpoint quickly and fallback to resolved IP when needed."""
    host, port, normalized = _split_debugger_address(debugger_address)

    candidate_hosts: list[str] = [host]
    if not _is_localhost(host):
        resolved_ip = _resolve_host_ip(host)
        if resolved_ip and resolved_ip not in candidate_hosts:
            candidate_hosts.append(resolved_ip)

    last_error = ""
    for h in candidate_hosts:
        address = f"{h}:{port}"
        url = f"http://{h}:{port}/json/version"
        try:
            req = Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "acadex-scraper/1.0",
                },
            )
            with urlopen(req, timeout=2.0) as resp:
                payload = json.loads((resp.read() or b"{}").decode("utf-8", errors="replace"))
            ws_url = str(payload.get("webSocketDebuggerUrl") or "").strip()
            browser = str(payload.get("Browser") or "").strip()
            if not ws_url:
                last_error = f"{address} responded but missing webSocketDebuggerUrl"
                continue
            if not browser:
                last_error = f"{address} responded with unexpected DevTools payload"
                continue
            if address != normalized:
                return True, f"ok (using IP fallback {address})", address
            return True, "ok", address
        except Exception as exc:
            last_error = f"DevTools endpoint check failed at {address}: {exc}"

    return False, last_error or f"DevTools endpoint check failed at {normalized}", normalized


def _chrome_executable() -> str:
    """Best-effort path to the Chrome executable for the current OS."""
    configured = (os.environ.get("CHROME_EXECUTABLE") or "").strip()
    if configured:
        if os.path.isfile(configured) and os.access(configured, os.X_OK):
            return configured
        found = shutil.which(configured)
        if found:
            return found

    system = platform.system()
    candidates: List[str] = []
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif system == "Windows":
        candidates = [
            os.path.join(os.environ.get("PROGRAMFILES", "C:\\Program Files"),
                         "Google", "Chrome", "Application", "chrome.exe"),
            os.path.join(os.environ.get("PROGRAMFILES(X86)", "C:\\Program Files (x86)"),
                         "Google", "Chrome", "Application", "chrome.exe"),
        ]
    else:
        candidates = [
            "/usr/bin/google-chrome",
            "/usr/bin/google-chrome-stable",
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/opt/google/chrome/chrome",
            "google-chrome",
            "google-chrome-stable",
            "chromium-browser",
            "chromium",
        ]

    for c in candidates:
        if os.path.isfile(c) and os.access(c, os.X_OK):
            return c
        found = shutil.which(c)
        if found:
            return found

    raise FileNotFoundError(
        "Google Chrome executable not found. "
        "Please install Chrome or set the correct path."
    )


def _profile_dir() -> str:
    """Return a per-OS temp profile directory for the isolated debugging session."""
    import os, tempfile
    if platform.system() == "Windows":
        return r"C:\selenium-profile"
    return os.path.join(tempfile.gettempdir(), "acadex-selenium-profile")


def start_browser_session(debugger_address: Optional[str] = None) -> dict:
    """
    Launch Chrome with remote-debugging on port 9222 (if not already running).
    Returns {"success": bool, "message": str, "already_running": bool}
    """
    host, port, normalized = _split_debugger_address(debugger_address)
    if _is_chrome_running(normalized):
        _clear_last_backend_error()
        return {
            "success": True,
            "message": f"Chrome debugger already reachable at {normalized} — session reused.",
            "already_running": True,
            "debugger_address": normalized,
        }

    is_local_target = _is_localhost(host)

    if _is_hosted_environment() and is_local_target:
        _set_last_backend_error("start_browser_session", "Hosted environment cannot launch local manual Chrome session")
        return {
            "success": False,
            "message": (
                "Manual browser session is not supported on hosted servers (Render). "
                "Run this feature from a local desktop app instance with Chrome installed, "
                "or provide a reachable remote debugger endpoint."
            ),
            "already_running": False,
            "debugger_address": normalized,
        }

    if not is_local_target:
        _set_last_backend_error("start_browser_session", f"Debugger endpoint not reachable: {normalized}")
        return {
            "success": False,
            "message": (
                f"Could not reach remote debugger endpoint {normalized}. "
                "Start Chrome on that machine with remote debugging enabled and ensure it is reachable."
            ),
            "already_running": False,
            "debugger_address": normalized,
        }

    try:
        exe = _chrome_executable()
    except FileNotFoundError as exc:
        _set_last_backend_error("start_browser_session", exc)
        return {
            "success": False,
            "message": str(exc),
            "already_running": False,
            "debugger_address": normalized,
        }

    profile = _profile_dir()
    cmd = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    try:
        # Detach so the process outlives the Flask thread
        subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        # Wait briefly for Chrome to open its debugging endpoint
        for _ in range(15):
            time.sleep(0.5)
            if _is_chrome_running(normalized):
                _clear_last_backend_error()
                return {
                    "success": True,
                    "message": f"Chrome launched successfully with debugger at {normalized}.",
                    "already_running": False,
                    "debugger_address": normalized,
                }
        return {
            "success": False,
            "message": f"Chrome launched but debugger endpoint {normalized} did not respond in time.",
            "already_running": False,
            "debugger_address": normalized,
        }
    except Exception as exc:
        _set_last_backend_error("start_browser_session", exc)
        return {
            "success": False,
            "message": f"Failed to launch Chrome: {exc}",
            "already_running": False,
            "debugger_address": normalized,
        }


def get_driver(debugger_address: Optional[str] = None):
    """
    Return a Selenium WebDriver connected to the running Chrome session via
    remote-debugging port 9222.  Raises RuntimeError if Chrome is not available.
    """
    global _active_driver, _active_debugger_address
    host, port, normalized = _split_debugger_address(debugger_address)
    with _active_driver_lock:
        # Try reusing the existing driver
        if _active_driver is not None and _active_debugger_address == normalized:
            try:
                _ = _active_driver.title  # probe — will throw if session is gone
                return _active_driver
            except Exception:
                _active_driver = None
                _active_debugger_address = None
        elif _active_driver is not None and _active_debugger_address != normalized:
            _active_driver = None
            _active_debugger_address = None

        if not _is_chrome_running(normalized):
            _set_last_backend_error("get_driver", f"Debugger endpoint not reachable: {normalized}")
            raise RuntimeError(
                f"Chrome debugger is not reachable at {normalized}. "
                "Use 'Start Browser Session' first."
            )

        http_ok, http_message, http_working_address = _debugger_http_health(normalized)
        if not http_ok:
            _set_last_backend_error("get_driver", http_message)
            raise RuntimeError(http_message)

        # Lazy-import selenium so the app starts even if selenium is not installed
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            _set_last_backend_error("get_driver", "selenium package missing")
            raise RuntimeError(
                "selenium is not installed. "
                "Run: pip install selenium"
            )

        options = Options()
        options.add_experimental_option("debuggerAddress", http_working_address)
        driver = webdriver.Chrome(options=options)
        _active_driver = driver
        _active_debugger_address = normalized
        _clear_last_backend_error()
        return driver


def release_driver() -> None:
    """Called after a scraping job completes to reset the cached driver reference."""
    global _active_driver, _active_debugger_address
    with _active_driver_lock:
        _active_driver = None
        _active_debugger_address = None


# ──────────────────────────────────────────────────────
#  Login check
# ──────────────────────────────────────────────────────

def check_login_status(driver) -> dict:
    """
    Check whether the admin is logged into the university portal by looking
    for the main course-table element.

    Returns {"logged_in": bool, "message": str}
    """
    try:
        from selenium.webdriver.common.by import By
        driver.find_element(By.ID, "funcForm:table_data")
        return {"logged_in": True, "message": "Logged In — course table detected."}
    except Exception:
        return {
            "logged_in": False,
            "message": (
                "Not Logged In — course table not found. "
                "Please navigate to the syllabus page and log in."
            ),
        }


# ──────────────────────────────────────────────────────
#  Probe available fields
# ──────────────────────────────────────────────────────

_SYLLABUS_SETTING_KEY = "scraper_syllabus_url"
_DEFAULT_SYLLABUS_URL = ""


def _normalize_label(label: str) -> str:
    text = (label or "").strip().lower()
    text = text.replace("＆", "&")
    for ch in [":", "-", "_", "\u3000"]:
        text = text.replace(ch, " ")
    return " ".join(text.split())


def _match_value_by_label(values: dict[str, str], target_label: str) -> str:
    if target_label in values:
        return values[target_label]

    wanted = _normalize_label(target_label)
    for key, value in values.items():
        if _normalize_label(key) == wanted:
            return value

    aliases = {
        "subject": {"course", "course title", "subject", "jugyo kmk name"},
        "day and period": {"day and period", "day period", "day/time", "day and time"},
        "faculty": {"faculty", "instructor", "teacher", "lecturer"},
        "class frequency": {"class frequency", "frequency"},
        "year and semester": {"year and semester", "semester", "term"},
        "credits": {"credits", "credit"},
    }

    target_aliases = aliases.get(wanted, set())
    if target_aliases:
        for key, value in values.items():
            normalized_key = _normalize_label(key)
            if normalized_key in target_aliases:
                return value

    return ""


def _coerce_float(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.replace(',', '')
    match = re.search(r'-?\d+(?:\.\d+)?', s)
    if not match:
        return None
    try:
        return float(match.group(0))
    except Exception:
        return None


def _build_canonical_scraped_fields(course_data: dict[str, str]) -> tuple[dict[str, object], dict[str, str]]:
    canonical = {
        "course_number": _match_value_by_label(course_data, "Course Number") or _match_value_by_label(course_data, "Course"),
        "course_title": _match_value_by_label(course_data, "Course Title") or _match_value_by_label(course_data, "Subject"),
        "credits": _coerce_float(_match_value_by_label(course_data, "Credits")),
        "instructor": _match_value_by_label(course_data, "Instructor") or _match_value_by_label(course_data, "Faculty") or _match_value_by_label(course_data, "Course Instructor"),
        "day_and_period": _match_value_by_label(course_data, "Day and Period"),
        "area_of_study": _match_value_by_label(course_data, "Area of Study") or _match_value_by_label(course_data, "Subject Area"),
        "prerequisite": _match_value_by_label(course_data, "Prerequisite") or _match_value_by_label(course_data, "Prerequisites"),
    }

    canonical_labels = {
        "course number", "course", "course title", "subject", "credits",
        "instructor", "faculty", "course instructor", "day and period",
        "area of study", "subject area", "prerequisite", "prerequisites",
    }

    additional: dict[str, str] = {}
    for key, value in (course_data or {}).items():
        normalized_key = _normalize_label(str(key or ""))
        if not normalized_key or normalized_key in canonical_labels:
            continue
        v = str(value or "").strip()
        if v:
            additional[str(key)] = v

    return canonical, additional


def _extract_table_headers(driver) -> list[str]:
    from selenium.webdriver.common.by import By

    headers: list[str] = []
    try:
        title_nodes = driver.find_elements(By.CSS_SELECTOR, "#funcForm\\:table_head .ui-column-title")
        headers = [(n.text or "").strip() for n in title_nodes if (n.text or "").strip()]
    except Exception:
        headers = []

    if headers:
        return headers

    try:
        th_nodes = driver.find_elements(By.CSS_SELECTOR, "#funcForm\\:table_head th")
        headers = [(n.text or "").strip() for n in th_nodes if (n.text or "").strip()]
    except Exception:
        headers = []

    return headers


def _extract_row_table_values(row, headers: list[str]) -> dict[str, str]:
    from selenium.webdriver.common.by import By

    values: dict[str, str] = {}
    cells = row.find_elements(By.XPATH, "./td")
    for idx, header in enumerate(headers):
        if idx >= len(cells):
            break
        text = (cells[idx].text or "").strip()
        values[header] = text

    # Fallback if header extraction failed
    if not values and cells:
        defaults = ["Day and period", "Subject", "Faculty", "Class frequency", "Year and semester", "Credits"]
        for idx, cell in enumerate(cells[: len(defaults)]):
            values[defaults[idx]] = (cell.text or "").strip()

    # Ensure Subject uses the link text when present
    try:
        link = row.find_element(By.CSS_SELECTOR, "td:nth-child(2) a[id$=':jugyoKmkName']")
        link_text = (link.text or "").strip()
        if link_text:
            values["Subject"] = link_text
            if "Course" not in values:
                values["Course"] = link_text
            if "Course Title" not in values:
                values["Course Title"] = link_text

            # Common format in this syllabus page: "8001 Course Name"
            parts = link_text.split(" ", 1)
            if parts and parts[0] and any(ch.isdigit() for ch in parts[0]):
                values.setdefault("Course Number", parts[0].strip())
            if len(parts) == 2 and parts[1].strip():
                values.setdefault("Course Title", parts[1].strip())
    except Exception:
        pass

    # Normalize credits like "3Credits" from list rows
    credits_raw = values.get("Credits") or values.get("Number of Credits") or ""
    if credits_raw:
        values["Credits"] = credits_raw.replace(" ", "")

    return values


def _safe_click(driver, element) -> None:
    """Click an element robustly when fixed headers/overlays intercept native clicks."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', inline:'center'});",
            element,
        )
        time.sleep(0.12)
    except Exception:
        pass

    try:
        element.click()
        return
    except Exception:
        pass

    try:
        driver.execute_script("arguments[0].click();", element)
        return
    except Exception:
        pass

    # Final attempt: slightly offset scroll then JS click
    driver.execute_script(
        "window.scrollBy(0, -120); arguments[0].click();",
        element,
    )


def _find_visible_popup(driver, popup_open_timeout: float = 10.0):
    from selenium.webdriver.common.by import By

    candidates = [
        (By.ID, "pkx02301:dialog"),
        (By.CSS_SELECTOR, "div[id='pkx02301:dialog']"),
        (By.ID, "pkx02301:dialog"),
        (
            By.XPATH,
            "//div[contains(@class,'ui-dialog') and not(contains(@style,'display: none')) and not(contains(@class,'ui-helper-hidden'))]",
        ),
        (
            By.XPATH,
            "//*[contains(@id,':dialog') and not(contains(@style,'display: none')) and not(contains(@class,'ui-helper-hidden'))]",
        ),
    ]

    timeout = max(1.0, float(popup_open_timeout or 10.0))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for locator in candidates:
            try:
                nodes = driver.find_elements(*locator)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    if node.is_displayed():
                        return node
                except Exception:
                    continue
        time.sleep(0.2)

    raise RuntimeError(f"Course detail popup did not appear within {timeout:.1f}s")


def _wait_for_popup_closed(driver, timeout: float = 6.0) -> bool:
    from selenium.webdriver.common.by import By

    deadline = time.time() + max(1.0, float(timeout or 6.0))
    selectors = [
        "div.ui-dialog:not(.ui-helper-hidden)",
        "div[id*=':dialog']:not(.ui-helper-hidden)",
        ".ui-widget-overlay",
    ]
    while time.time() < deadline:
        visible = 0
        for sel in selectors:
            try:
                nodes = driver.find_elements(By.CSS_SELECTOR, sel)
            except Exception:
                nodes = []
            for node in nodes:
                try:
                    styles = (node.get_attribute("style") or "").lower()
                    classes = (node.get_attribute("class") or "").lower()
                    if "display: none" in styles or "visibility: hidden" in styles:
                        continue
                    if "ui-helper-hidden" in classes:
                        continue
                    if node.is_displayed():
                        visible += 1
                except Exception:
                    continue
        if visible == 0:
            return True
        time.sleep(0.12)
    return False


def probe_available_fields(
    driver,
    syllabus_url: str = "",
    popup_open_timeout: float = 10.0,
    popup_close_delay: float = 0.35,
) -> dict:
    """
    Navigate to the syllabus page, open the first course popup and extract
    all available field labels.

    Returns {"success": bool, "fields": list[str], "message": str}
    """
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.common.exceptions import TimeoutException

        wait = WebDriverWait(driver, 15)

        # Navigate if a URL is given
        if syllabus_url:
            driver.get(syllabus_url)

        # Wait for the course table
        try:
            wait.until(EC.visibility_of_element_located((By.ID, "funcForm:table_data")))
        except TimeoutException:
            return {
                "success": False,
                "fields": [],
                "message": (
                    "Timed out waiting for course table (funcForm:table_data). "
                    "Ensure the syllabus page is open and you are logged in."
                ),
            }

        fields: list[str] = []
        seen: set[str] = set()

        # Include visible table headers as probe options
        table_headers = _extract_table_headers(driver)
        for header in table_headers:
            if header not in seen:
                fields.append(header)
                seen.add(header)

        # Click first course row to inspect popup fields, if available
        first_link = wait.until(
            EC.element_to_be_clickable((By.CSS_SELECTOR, "#funcForm\\:table_data tr[data-ri] td:nth-child(2) a[id$=':jugyoKmkName']"))
        )
        _safe_click(driver, first_link)

        # Read row values before opening popup
        first_row = driver.find_element(By.CSS_SELECTOR, "#funcForm\\:table_data tr[data-ri]")
        row_values = _extract_row_table_values(first_row, table_headers)
        for key in row_values.keys():
            key_text = (key or "").strip()
            if key_text and key_text not in seen:
                fields.append(key_text)
                seen.add(key_text)

        popup = _find_visible_popup(driver, popup_open_timeout=popup_open_timeout)

        popup_headers = popup.find_elements(By.CSS_SELECTOR, "#pkx02301\\:ch\\:table .rowStyle .ui-widget-header, div.ui-widget-header")
        for header in popup_headers:
            text = (header.text or "").strip()
            if text and text not in seen:
                fields.append(text)
                seen.add(text)

        # Additional label extraction patterns used by PrimeFaces detail dialogs
        for node in popup.find_elements(By.CSS_SELECTOR, "#pkx02301\\:ch\\:table .rowStyle .ui-widget-header, dt, th, .ui-outputlabel"):
            text = (node.text or "").strip().rstrip(":")
            if text and text not in seen:
                fields.append(text)
                seen.add(text)

        # Close popup
        try:
            close_btn = popup.find_element(By.CSS_SELECTOR, ".ui-dialog-titlebar-close")
            _safe_click(driver, close_btn)
            time.sleep(max(0.0, float(popup_close_delay or 0.0)))
        except Exception:
            pass

        if not fields:
            return {
                "success": False,
                "fields": [],
                "message": "No field labels found from table headers or popup.",
            }

        return {
            "success": True,
            "fields": fields,
            "message": f"Found {len(fields)} field(s) from table and popup.",
        }

    except Exception as exc:
        _set_last_backend_error("probe_available_fields", exc)
        return {
            "success": False,
            "fields": [],
            "message": f"Probe error: {exc}",
        }


def _probe_worker(
    debugger_address: str,
    popup_open_timeout: float,
    popup_close_delay: float,
) -> None:
    try:
        _set_probe_progress(
            status="running",
            fields=[],
            message="Probe started.",
            error=None,
            finished_at=None,
        )
        driver = get_driver(debugger_address=debugger_address)
        result = probe_available_fields(
            driver,
            popup_open_timeout=popup_open_timeout,
            popup_close_delay=popup_close_delay,
        )
        if result.get("success"):
            _set_probe_progress(
                status="completed",
                fields=list(result.get("fields") or []),
                message=str(result.get("message") or "Probe completed."),
                error=None,
                finished_at=datetime.utcnow().isoformat() + "Z",
            )
        else:
            _set_probe_progress(
                status="failed",
                fields=[],
                message=str(result.get("message") or "Probe failed."),
                error=str(result.get("message") or "Probe failed."),
                finished_at=datetime.utcnow().isoformat() + "Z",
            )
    except Exception as exc:
        _set_last_backend_error("probe_worker", exc)
        _set_probe_progress(
            status="failed",
            fields=[],
            message=f"Probe failed: {exc}",
            error=str(exc),
            finished_at=datetime.utcnow().isoformat() + "Z",
        )
    finally:
        if _probe_lock.locked():
            _probe_lock.release()


def start_probe_job(
    debugger_address: str,
    popup_open_timeout: float = 10.0,
    popup_close_delay: float = 0.35,
) -> dict:
    if not _probe_lock.acquire(blocking=False):
        return {
            "success": False,
            "started": False,
            "message": "A probe job is already running.",
            "job_id": probe_progress.get("job_id"),
        }

    job_id = datetime.utcnow().strftime("probe-%Y%m%d%H%M%S%f")
    _set_probe_progress(
        status="running",
        job_id=job_id,
        fields=[],
        message="Probe queued.",
        error=None,
        started_at=datetime.utcnow().isoformat() + "Z",
        finished_at=None,
    )

    t = threading.Thread(
        target=_probe_worker,
        args=(debugger_address, float(popup_open_timeout or 10.0), float(popup_close_delay or 0.35)),
        daemon=True,
        name="ProbeWorker",
    )
    try:
        t.start()
    except Exception as exc:
        if _probe_lock.locked():
            _probe_lock.release()
        _set_last_backend_error("start_probe_job", exc)
        _set_probe_progress(
            status="failed",
            message=f"Failed to start probe worker: {exc}",
            error=str(exc),
            finished_at=datetime.utcnow().isoformat() + "Z",
        )
        return {
            "success": False,
            "started": False,
            "message": f"Failed to start probe worker: {exc}",
            "job_id": job_id,
        }

    return {
        "success": True,
        "started": True,
        "message": "Probe started. Poll /admin/probe-status for results.",
        "job_id": job_id,
    }


# ──────────────────────────────────────────────────────
#  Controlled scraper — runs in background thread
# ──────────────────────────────────────────────────────

def _worker(
    driver,
    selected_fields: List[str],
    dataset_id: int,
    flask_app,
    syllabus_url: str = "",
    popup_open_timeout: float = 10.0,
    popup_close_delay: float = 0.35,
    inter_row_delay: float = 0.0,
) -> None:
    """
    Background thread: iterates over every course row, extracts selected
    fields and persists them.  Updates scraper_progress in real time.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, StaleElementReferenceException, NoSuchElementException, WebDriverException,
    )

    wait = WebDriverWait(driver, 12)

    def _update_db_progress(**kwargs) -> None:
        """Persist current progress counters to the DB."""
        try:
            with flask_app.app_context():
                from models import db, ScrapedDataset
                ds = db.session.get(ScrapedDataset, dataset_id)
                if ds:
                    for k, v in kwargs.items():
                        setattr(ds, k, v)
                    db.session.commit()
        except Exception as exc:
            _log_progress(f"[DB] progress update failed: {exc}")

    def _extract_popup_fields(popup) -> dict[str, str]:
        # Fast path: perform all extraction in browser context in one round trip.
        try:
            payload = driver.execute_script(
                """
                const root = arguments[0];
                const out = {};
                const clean = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                const put = (k, v) => {
                  const key = clean(k).replace(/:$/, '');
                  if (!key || Object.prototype.hasOwnProperty.call(out, key)) return;
                  out[key] = clean(v);
                };

                const scopedRows = root.querySelectorAll('#pkx02301\\:ch\\:table .rowStyle');
                scopedRows.forEach((row) => {
                  const headers = row.querySelectorAll('div.ui-widget-header');
                  headers.forEach((header) => {
                    let sibling = header.nextElementSibling;
                    while (sibling && !sibling.classList.contains('ui-widget-content')) {
                      sibling = sibling.nextElementSibling;
                    }
                    const valueNode = sibling ? (sibling.querySelector('.fr-box.fr-view, .fr-view') || sibling) : null;
                    put(header.textContent, valueNode ? valueNode.textContent : '');
                  });
                });

                root.querySelectorAll('dt').forEach((dt) => {
                  let sib = dt.nextElementSibling;
                  while (sib && sib.tagName !== 'DD') sib = sib.nextElementSibling;
                  put(dt.textContent, sib ? sib.textContent : '');
                });

                root.querySelectorAll('tr').forEach((tr) => {
                  const th = tr.querySelector('th');
                  const td = tr.querySelector('td');
                  if (th && td) put(th.textContent, td.textContent);
                });

                root.querySelectorAll('div.ui-widget-header').forEach((header) => {
                  let sibling = header.nextElementSibling;
                  const valueNode = sibling ? (sibling.querySelector('.fr-box.fr-view, .fr-view') || sibling) : null;
                  put(header.textContent, valueNode ? valueNode.textContent : '');
                });

                return out;
                """,
                popup,
            )
            if isinstance(payload, dict) and payload:
                return {str(k): str(v or '').strip() for k, v in payload.items()}
        except Exception:
            pass

        # Slow fallback for unexpected DOM/script restrictions.
        data: dict[str, str] = {}
        try:
            for header in popup.find_elements(By.CSS_SELECTOR, "div.ui-widget-header"):
                label = (header.text or "").strip().rstrip(":")
                if not label:
                    continue
                value = ""
                try:
                    sibling = header.find_element(By.XPATH, "following-sibling::div[1]")
                    rich = sibling.find_elements(By.CSS_SELECTOR, ".fr-view")
                    value = (rich[0].text if rich else sibling.text or "").strip()
                except Exception:
                    value = ""
                if label not in data:
                    data[label] = value
        except Exception:
            pass
        return data

    def _close_popup(popup, close_delay: float) -> None:
        closed = False
        try:
            close_btn = popup.find_element(By.CSS_SELECTOR, ".ui-dialog-titlebar-close")
            _safe_click(driver, close_btn)
            closed = _wait_for_popup_closed(driver, timeout=max(2.0, float(popup_open_timeout or 10.0)))
        except Exception:
            closed = False

        if not closed:
            try:
                from selenium.webdriver.common.keys import Keys

                body = driver.find_element(By.TAG_NAME, "body")
                body.send_keys(Keys.ESCAPE)
                closed = _wait_for_popup_closed(driver, timeout=2.5)
            except Exception:
                closed = False

        if not closed:
            try:
                driver.execute_script(
                    "document.querySelectorAll('.ui-widget-overlay,.ui-dialog,[id*=\\':dialog\\']').forEach(function(n){n.style.display='none'; n.classList.add('ui-helper-hidden');});"
                )
                closed = _wait_for_popup_closed(driver, timeout=1.8)
            except Exception:
                closed = False

        time.sleep(max(0.0, float(close_delay or 0.0)))

        if not closed:
            raise RuntimeError("Popup did not close cleanly before next iteration")

    def _wait_if_paused() -> bool:
        announced = False
        while _scraper_pause_event.is_set():
            if _scraper_stop_event.is_set():
                return False
            with _progress_lock:
                scraper_progress["status"] = "paused"
            _update_db_progress(status="paused")
            if not announced:
                _log_progress("⏸️ Paused — waiting for resume.")
                announced = True
            time.sleep(0.3)
        if _scraper_stop_event.is_set():
            return False
        with _progress_lock:
            if _scraper_is_running:
                scraper_progress["status"] = "running"
        _update_db_progress(status="running")
        return True

    was_stopped = False

    try:
        if syllabus_url:
            driver.get(syllabus_url)
            _log_progress("Opened syllabus URL before scraping.")

        # Wait for table
        wait.until(EC.visibility_of_element_located((By.ID, "funcForm:table_data")))
        table_headers = _extract_table_headers(driver)

        rows = driver.find_elements(By.XPATH, '//*[@id="funcForm:table_data"]/tr')
        total = len(rows)
        _log_progress(f"Found {total} course rows. Starting scrape…")

        with _progress_lock:
            scraper_progress["total_courses"] = total
        _update_db_progress(total_courses=total, status="running")

        for i in range(total):
            if _scraper_stop_event.is_set():
                was_stopped = True
                _log_progress("🛑 Stop acknowledged. Ending scrape loop.")
                break

            if not _wait_if_paused():
                was_stopped = True
                _log_progress("🛑 Stop acknowledged while paused. Ending scrape loop.")
                break

            with _progress_lock:
                if scraper_progress["status"] == "failed":
                    break

            try:
                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass
                all_rows = wait.until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, '//*[@id="funcForm:table_data"]/tr')
                    )
                )
                row = all_rows[i]
                row_values = _extract_row_table_values(row, table_headers)
                day_and_period = row_values.get("Day and period", "")
                subject_link = row.find_element(By.XPATH, ".//td[2]/a")
                _safe_click(driver, subject_link)

                runtime_cfg = get_runtime_config()
                effective_popup_open_timeout = max(1.0, float(runtime_cfg.get("popup_open_timeout", popup_open_timeout) or popup_open_timeout))
                effective_popup_close_delay = max(0.0, float(runtime_cfg.get("popup_close_delay", popup_close_delay) or popup_close_delay))
                effective_inter_row_delay = max(0.0, float(runtime_cfg.get("inter_row_delay", inter_row_delay) or inter_row_delay))

                popup = _find_visible_popup(driver, popup_open_timeout=effective_popup_open_timeout)

                # Always capture Day and Period
                course_data: dict = dict(row_values)
                if day_and_period and "Day and Period" not in course_data:
                    course_data["Day and Period"] = day_and_period
                popup_values = _extract_popup_fields(popup)
                for field in selected_fields:
                    if field in course_data and course_data[field]:
                        continue
                    value = _match_value_by_label(course_data, field)
                    if not value:
                        value = _match_value_by_label(popup_values, field)
                    course_data[field] = value

                course_title = (
                    _match_value_by_label(course_data, "Course Title")
                    or _match_value_by_label(course_data, "Subject")
                    or f"Row {i+1}"
                )
                _log_progress(f"[{i+1}/{total}] {course_title}")

                _close_popup(popup, effective_popup_close_delay)

                try:
                    driver.switch_to.default_content()
                except Exception:
                    pass

                transition_delay = max(0.0, float(effective_inter_row_delay or 0.0))
                if transition_delay > 0:
                    time.sleep(transition_delay)

                # Persist the row
                with flask_app.app_context():
                    from models import db, ScrapedCourse, ScrapedDataset
                    canonical, additional = _build_canonical_scraped_fields(course_data)
                    sc = ScrapedCourse(
                        dataset_id=dataset_id,
                        data_json=json.dumps(course_data, ensure_ascii=False),
                        course_number=str(canonical.get("course_number") or "").strip() or None,
                        course_title=str(canonical.get("course_title") or "").strip() or None,
                        credits=canonical.get("credits"),
                        instructor=str(canonical.get("instructor") or "").strip() or None,
                        day_and_period=str(canonical.get("day_and_period") or "").strip() or None,
                        area_of_study=str(canonical.get("area_of_study") or "").strip() or None,
                        prerequisite=str(canonical.get("prerequisite") or "").strip() or None,
                        additional_attributes_json=json.dumps(additional, ensure_ascii=False) if additional else None,
                    )
                    db.session.add(sc)
                    ds = db.session.get(ScrapedDataset, dataset_id)
                    if ds:
                        ds.current_index = i + 1
                        ds.last_course = course_title
                    db.session.commit()

                with _progress_lock:
                    scraper_progress["current_index"] = i + 1
                    scraper_progress["last_course"] = course_title

            except (TimeoutException, StaleElementReferenceException, NoSuchElementException, WebDriverException) as exc:
                msg = f"[{i+1}/{total}] Skipped — {type(exc).__name__}: {_compact_exc_text(exc)}"
                _log_progress(msg)
                # Attempt page recovery
                try:
                    driver.refresh()
                    wait.until(
                        EC.visibility_of_element_located((By.ID, "funcForm:table_data"))
                    )
                except Exception:
                    pass

        if was_stopped:
            with flask_app.app_context():
                from models import db, ScrapedDataset

                ds = db.session.get(ScrapedDataset, dataset_id)
                if ds:
                    ds.status = "stopped"
                    db.session.commit()

            with _progress_lock:
                scraper_progress["status"] = "stopped"
                scraper_progress["error"] = None
            _log_progress("🛑 Scraping stopped by admin.")
            return

        # ── Completion ──
        with flask_app.app_context():
            from models import db, ScrapedDataset, CatalogStatus, Notification, Setting, DatasetRegistry

            ds = db.session.get(ScrapedDataset, dataset_id)
            if ds:
                ds.status = "completed"
                ds.current_index = total
                db.session.commit()

                try:
                    reg = DatasetRegistry.query.filter_by(name=str(ds.name or '').strip(), source='scraped').order_by(DatasetRegistry.created_at.desc(), DatasetRegistry.id.desc()).first()
                    table_name = f"scraped_dataset_{ds.id}"
                    if not reg:
                        reg = DatasetRegistry(name=ds.name, source='scraped', table_name=table_name)
                        db.session.add(reg)
                    else:
                        reg.table_name = table_name

                    active_exists = DatasetRegistry.query.filter(DatasetRegistry.is_active.is_(True)).first()
                    if not active_exists:
                        DatasetRegistry.query.update({'is_active': False})
                        reg.is_active = True

                    setting = Setting.query.get('active_dataset_name')
                    if not setting:
                        setting = Setting(key='active_dataset_name', value=str(ds.name or '').strip())
                        db.session.add(setting)
                    elif not str(setting.value or '').strip():
                        setting.value = str(ds.name or '').strip()

                    db.session.commit()
                except Exception:
                    db.session.rollback()

            # Update / create CatalogStatus
            year_val = ds.year if ds else ""
            sem_val = ds.semester if ds else ""
            term_key = f"{sem_val} {year_val}".strip()
            if term_key:
                cs = CatalogStatus.query.filter_by(term=term_key).first()
                if cs is None:
                    cs = CatalogStatus(term=term_key, status="available")
                    db.session.add(cs)
                else:
                    cs.status = "available"
                    cs.updated_at = datetime.utcnow()
                try:
                    cs.dataset = ds.name if ds else None
                except Exception:
                    pass
                db.session.commit()

            # Broadcast notification to all users
            notif = Notification(
                user_id=None,
                session_id=None,
                notif_type="info",
                title="New Course Catalog Available",
                body=f"New course catalog for {year_val} {sem_val} is now available.",
            )
            db.session.add(notif)
            db.session.commit()

        with _progress_lock:
            scraper_progress["status"] = "completed"
        _log_progress("✅ Scraping completed successfully.")

    except Exception as exc:
        fatal_message = f"{type(exc).__name__}: {_compact_exc_text(exc)}"
        _log_progress(f"❌ Fatal error: {fatal_message}")
        with _progress_lock:
            scraper_progress["status"] = "failed"
            scraper_progress["error"] = fatal_message
        _set_last_backend_error("scraper.worker.fatal", exc)
        try:
            with flask_app.app_context():
                from models import db, ScrapedDataset, DatasetRegistry
                ds = db.session.get(ScrapedDataset, dataset_id)
                if ds:
                    ds.status = "failed"
                    db.session.commit()
                    try:
                        stale = DatasetRegistry.query.filter_by(name=str(ds.name or '').strip(), source='scraped').all()
                        for row in stale:
                            db.session.delete(row)
                        db.session.commit()
                    except Exception:
                        db.session.rollback()
        except Exception:
            pass

    finally:
        global _scraper_is_running
        with _progress_lock:
            _scraper_is_running = False
        _scraper_pause_event.clear()
        _scraper_stop_event.clear()
        if _scraper_lock.locked():
            _scraper_lock.release()
        release_driver()


def run_controlled_scraper(
    selected_fields: List[str],
    year: str,
    semester: str,
    flask_app,
    syllabus_url: str = "",
    debugger_address: Optional[str] = None,
    popup_open_timeout: float = 10.0,
    popup_close_delay: float = 0.35,
    inter_row_delay: float = 0.0,
) -> dict:
    """
    Spawn a background thread that runs the scraper.

    Returns {"success": bool, "message": str, "dataset_id": int | None}
    """
    global _scraper_is_running
    if not _scraper_lock.acquire(blocking=False):
        return {
            "success": False,
            "message": "A scraping job is already running.",
            "dataset_id": None,
        }
    with _progress_lock:
        if _scraper_is_running:
            _scraper_lock.release()
            return {
                "success": False,
                "message": "A scraping job is already running.",
                "dataset_id": None,
            }
        _scraper_is_running = True
        _scraper_pause_event.clear()
        _scraper_stop_event.clear()

    update_runtime_config(
        popup_open_timeout=popup_open_timeout,
        popup_close_delay=popup_close_delay,
        inter_row_delay=inter_row_delay,
    )

    try:
        driver = get_driver(debugger_address=debugger_address)
    except RuntimeError as exc:
        with _progress_lock:
            _scraper_is_running = False
        _scraper_lock.release()
        return {"success": False, "message": str(exc), "dataset_id": None}

    # Create a ScrapedDataset record to track this job
    dataset_id: Optional[int] = None
    try:
        with flask_app.app_context():
            from models import db, ScrapedDataset
            slug_sem = re.sub(r'[^A-Za-z0-9]+', '_', str(semester or '').strip()).strip('_') or str(semester or '').strip()
            ds = ScrapedDataset(
                name=f"Scraped_{year}_{slug_sem}",
                year=year,
                semester=semester,
                status="running",
                selected_fields_json=json.dumps(selected_fields),
            )
            db.session.add(ds)
            db.session.commit()
            dataset_id = ds.id
    except Exception as exc:
        with _progress_lock:
            _scraper_is_running = False
        _scraper_lock.release()
        return {
            "success": False,
            "message": f"Failed to create dataset record: {exc}",
            "dataset_id": None,
        }

    # Reset progress state
    with _progress_lock:
        scraper_progress.update(
            {
                "status": "running",
                "dataset_id": dataset_id,
                "current_index": 0,
                "total_courses": 0,
                "last_course": "",
                "log": [],
                "error": None,
            }
        )

    _log_progress(f"Starting scrape for {year} {semester} with fields: {selected_fields}")

    t = threading.Thread(
        target=_worker,
        args=(
            driver,
            selected_fields,
            dataset_id,
            flask_app,
            syllabus_url,
            popup_open_timeout,
            popup_close_delay,
            inter_row_delay,
        ),
        daemon=True,
        name="ScraperWorker",
    )
    try:
        t.start()
    except Exception as exc:
        with _progress_lock:
            _scraper_is_running = False
        if _scraper_lock.locked():
            _scraper_lock.release()
        return {
            "success": False,
            "message": f"Failed to start scraper worker: {exc}",
            "dataset_id": None,
        }

    return {
        "success": True,
        "message": f"Scraper started for {year} {semester}.",
        "dataset_id": dataset_id,
    }
