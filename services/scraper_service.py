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
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import List, Optional

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────
#  Global in-process scraper state
# ──────────────────────────────────────────────────────
_scraper_lock = threading.Lock()          # prevents concurrent runs
_scraper_is_running = False
_active_driver = None                     # reused WebDriver instance
_active_driver_lock = threading.Lock()

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


def _log_progress(msg: str) -> None:
    """Thread-safe append to the in-memory log."""
    with _progress_lock:
        scraper_progress["log"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
        if len(scraper_progress["log"]) > 200:
            scraper_progress["log"] = scraper_progress["log"][-200:]


def is_scraper_running() -> bool:
    with _progress_lock:
        return bool(_scraper_is_running)


# ──────────────────────────────────────────────────────
#  Chrome / WebDriver helpers
# ──────────────────────────────────────────────────────
_DEBUGGING_PORT = 9222


def _is_hosted_environment() -> bool:
    return bool(os.environ.get("RENDER") or os.environ.get("DYNO") or os.environ.get("K_SERVICE"))


def _is_chrome_running() -> bool:
    """Return True if something is already listening on the debugging port."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", _DEBUGGING_PORT))
            return True
        except OSError:
            return False


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
        import os
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


def start_browser_session() -> dict:
    """
    Launch Chrome with remote-debugging on port 9222 (if not already running).
    Returns {"success": bool, "message": str, "already_running": bool}
    """
    if _is_chrome_running():
        return {
            "success": True,
            "message": "Chrome is already running on port 9222 — session reused.",
            "already_running": True,
        }

    if _is_hosted_environment():
        return {
            "success": False,
            "message": (
                "Manual browser session is not supported on hosted servers (Render). "
                "Run this feature from a local desktop app instance with Chrome installed, "
                "or provide a reachable remote debugger endpoint."
            ),
            "already_running": False,
        }

    try:
        exe = _chrome_executable()
    except FileNotFoundError as exc:
        return {"success": False, "message": str(exc), "already_running": False}

    profile = _profile_dir()
    cmd = [
        exe,
        f"--remote-debugging-port={_DEBUGGING_PORT}",
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
            if _is_chrome_running():
                return {
                    "success": True,
                    "message": "Chrome launched successfully on port 9222.",
                    "already_running": False,
                }
        return {
            "success": False,
            "message": "Chrome launched but the debugging port did not respond in time.",
            "already_running": False,
        }
    except Exception as exc:
        return {
            "success": False,
            "message": f"Failed to launch Chrome: {exc}",
            "already_running": False,
        }


def get_driver():
    """
    Return a Selenium WebDriver connected to the running Chrome session via
    remote-debugging port 9222.  Raises RuntimeError if Chrome is not available.
    """
    global _active_driver
    with _active_driver_lock:
        # Try reusing the existing driver
        if _active_driver is not None:
            try:
                _ = _active_driver.title  # probe — will throw if session is gone
                return _active_driver
            except Exception:
                _active_driver = None

        if not _is_chrome_running():
            raise RuntimeError(
                "Chrome is not running on port 9222. "
                "Use 'Start Browser Session' first."
            )

        # Lazy-import selenium so the app starts even if selenium is not installed
        try:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options
        except ImportError:
            raise RuntimeError(
                "selenium is not installed. "
                "Run: pip install selenium"
            )

        options = Options()
        options.add_experimental_option("debuggerAddress", f"localhost:{_DEBUGGING_PORT}")
        driver = webdriver.Chrome(options=options)
        _active_driver = driver
        return driver


def release_driver() -> None:
    """Called after a scraping job completes to reset the cached driver reference."""
    global _active_driver
    with _active_driver_lock:
        _active_driver = None


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


def probe_available_fields(driver, syllabus_url: str = "") -> dict:
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

        # Click the first course link
        first_link = wait.until(
            EC.element_to_be_clickable((By.XPATH, '//*[@id="funcForm:table_data"]/tr[1]//td[2]/a'))
        )
        first_link.click()

        # Wait for popup
        try:
            popup = wait.until(EC.visibility_of_element_located((By.ID, "pkx02301:dialog")))
        except TimeoutException:
            return {
                "success": False,
                "fields": [],
                "message": "Popup (pkx02301:dialog) did not appear.",
            }

        # Extract all section headers in the popup
        headers = popup.find_elements(By.CSS_SELECTOR, "div.ui-widget-header")
        fields = [h.text.strip() for h in headers if h.text.strip()]

        # Close popup
        try:
            close_btn = popup.find_element(By.CLASS_NAME, "ui-dialog-titlebar-close")
            close_btn.click()
        except Exception:
            pass

        if not fields:
            return {
                "success": False,
                "fields": [],
                "message": "No field labels found in the popup.",
            }

        return {
            "success": True,
            "fields": fields,
            "message": f"Found {len(fields)} field(s).",
        }

    except Exception as exc:
        return {
            "success": False,
            "fields": [],
            "message": f"Probe error: {exc}",
        }


# ──────────────────────────────────────────────────────
#  Controlled scraper — runs in background thread
# ──────────────────────────────────────────────────────

def _worker(driver, selected_fields: List[str], dataset_id: int, flask_app, syllabus_url: str = "") -> None:
    """
    Background thread: iterates over every course row, extracts selected
    fields and persists them.  Updates scraper_progress in real time.
    """
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.common.exceptions import (
        TimeoutException, StaleElementReferenceException, NoSuchElementException,
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
        data: dict[str, str] = {}
        for header in popup.find_elements(By.CSS_SELECTOR, "div.ui-widget-header"):
            label = (header.text or "").strip()
            if not label:
                continue
            value = ""
            try:
                sibling = header.find_element(By.XPATH, "following-sibling::div[1]")
                rich = sibling.find_elements(By.CSS_SELECTOR, ".fr-view")
                if rich:
                    value = (rich[0].text or "").strip()
                else:
                    value = (sibling.text or "").strip()
            except Exception:
                value = ""
            data[label] = value
        return data

    def _close_popup(popup) -> None:
        try:
            popup.find_element(By.CLASS_NAME, "ui-dialog-titlebar-close").click()
            wait.until(EC.invisibility_of_element_located((By.ID, "pkx02301:dialog")))
            return
        except Exception:
            pass
        try:
            driver.execute_script("arguments[0].style.display='none';", popup)
        except Exception:
            pass

    try:
        if syllabus_url:
            driver.get(syllabus_url)
            _log_progress("Opened syllabus URL before scraping.")

        # Wait for table
        wait.until(EC.visibility_of_element_located((By.ID, "funcForm:table_data")))

        rows = driver.find_elements(By.XPATH, '//*[@id="funcForm:table_data"]/tr')
        total = len(rows)
        _log_progress(f"Found {total} course rows. Starting scrape…")

        with _progress_lock:
            scraper_progress["total_courses"] = total
        _update_db_progress(total_courses=total, status="running")

        for i in range(total):
            with _progress_lock:
                if scraper_progress["status"] == "failed":
                    break

            try:
                all_rows = wait.until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, '//*[@id="funcForm:table_data"]/tr')
                    )
                )
                row = all_rows[i]
                day_and_period = row.find_element(By.XPATH, ".//td[1]").text
                subject_link = row.find_element(By.XPATH, ".//td[2]/a")
                subject_link.click()

                popup = wait.until(
                    EC.visibility_of_element_located((By.ID, "pkx02301:dialog"))
                )

                # Always capture Day and Period
                course_data: dict = {"Day and Period": day_and_period}
                popup_values = _extract_popup_fields(popup)
                for field in selected_fields:
                    course_data[field] = popup_values.get(field, "")

                course_title = course_data.get("Course Title", f"Row {i+1}")
                _log_progress(f"[{i+1}/{total}] {course_title}")

                _close_popup(popup)

                # Persist the row
                with flask_app.app_context():
                    from models import db, ScrapedCourse, ScrapedDataset
                    sc = ScrapedCourse(
                        dataset_id=dataset_id,
                        data_json=json.dumps(course_data, ensure_ascii=False),
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

            except (TimeoutException, StaleElementReferenceException, NoSuchElementException) as exc:
                msg = f"[{i+1}/{total}] Skipped — {type(exc).__name__}: {exc}"
                _log_progress(msg)
                # Attempt page recovery
                try:
                    driver.refresh()
                    wait.until(
                        EC.visibility_of_element_located((By.ID, "funcForm:table_data"))
                    )
                except Exception:
                    pass

        # ── Completion ──
        with flask_app.app_context():
            from models import db, ScrapedDataset, CatalogStatus, Notification, Setting

            ds = db.session.get(ScrapedDataset, dataset_id)
            if ds:
                ds.status = "completed"
                ds.current_index = total
                db.session.commit()

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
        _log_progress(f"❌ Fatal error: {exc}")
        with _progress_lock:
            scraper_progress["status"] = "failed"
            scraper_progress["error"] = str(exc)
        try:
            with flask_app.app_context():
                from models import db, ScrapedDataset
                ds = db.session.get(ScrapedDataset, dataset_id)
                if ds:
                    ds.status = "failed"
                    db.session.commit()
        except Exception:
            pass

    finally:
        global _scraper_is_running
        with _progress_lock:
            _scraper_is_running = False
        if _scraper_lock.locked():
            _scraper_lock.release()
        release_driver()


def run_controlled_scraper(
    selected_fields: List[str],
    year: str,
    semester: str,
    flask_app,
    syllabus_url: str = "",
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

    try:
        driver = get_driver()
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
            slug_sem = semester.replace(" ", "_")
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
        args=(driver, selected_fields, dataset_id, flask_app, syllabus_url),
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
