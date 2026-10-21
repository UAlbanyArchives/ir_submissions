import logging
import os
import smtplib
import subprocess
import time
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
import yaml

API_KEY = os.environ["JOTFORM_API_KEY"]
FORM_ID = os.environ["JOTFORM_FORM_ID"]
API_URL = os.environ.get("JOTFORM_API_URL", "https://api.jotform.com")
OUT_DIR = Path(os.environ.get("OUTPUT_DIR", "/data"))
INTERVAL = int(os.environ.get("POLL_INTERVAL_SECONDS", "300"))

SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
MAIL_FROM = os.environ["MAIL_FROM"]
TICKET_EMAIL = os.environ["TICKET_EMAIL"]

log = logging.getLogger("ir_submissions")


def fetch_submissions():
    submission_url = f"{API_URL}/form/{FORM_ID}/submissions"
    r = requests.get(
        submission_url,
        params={"apiKey": API_KEY, "limit": 100, "orderby": "created_at"},
        timeout=60,
    )
    r.raise_for_status()
    print (r.json()["content"])
    return r.json()["content"]


def is_url(value):
    return isinstance(value, str) and value.startswith(("http://", "https://"))

def convert_docx(folder, name):
    """Make markdown and html copies with pandoc; returns names of the files created."""
    created = []
    for fmt, ext in (("gfm", ".md"), ("html", ".html")):
        out = Path(name).with_suffix(ext).name
        try:
            subprocess.run(
                ["pandoc", str(folder / name), "-f", "docx", "-t", fmt, "-o", str(folder / out)],
                check=True, capture_output=True, text=True, timeout=120,
            )
            created.append(out)
        except Exception as e:
            log.warning("pandoc failed for %s -> %s: %s", name, out, getattr(e, "stderr", e))
    return created


def save_submission(sub):
    """Write files and metadata.yml to disk"""
    folder = OUT_DIR / sub["id"]
    folder.mkdir(parents=True, exist_ok=True)
    fields, files, submitter = {}, [], {}
    for answer in sub.get("answers", {}).values():
        label = answer.get("text") or answer.get("name")
        value = answer.get("answer")
        if value in (None, "", []):
            continue
        if answer.get("type") == "control_fullname" and "name" not in submitter:
            submitter["name"] = answer.get("prettyFormat") or " ".join(
                str(v) for v in value.values() if v
            ) if isinstance(value, dict) else value
        elif answer.get("type") == "control_email" and "email" not in submitter:
            submitter["email"] = value
        if answer.get("type") == "control_fileupload":
            for url in value if isinstance(value, list) else [value]:
                if not is_url(url):
                    continue
                name = Path(unquote(urlparse(url).path)).name
                resp = requests.get(url, params={"apiKey": API_KEY}, timeout=300)
                resp.raise_for_status()
                (folder / name).write_bytes(resp.content)
                files.append(name)
                if name.lower().endswith(".docx"):
                    files.extend(convert_docx(folder, name))
        else:
            fields[label] = value
    metadata = {
        "id": sub["id"],
        "form_id": sub.get("form_id", FORM_ID),
        "status": sub.get("status"),
        "created_at": sub.get("created_at"),
        "updated_at": sub.get("updated_at"),
        "new": sub.get("new"),
        "flag": sub.get("flag"),
        "notes": sub.get("notes"),
        "submitter": submitter,
        "fields": fields,
        "files": files,
    }
    with open(folder / "metadata.yml", "w", encoding="utf-8") as f:
        yaml.safe_dump(metadata, f, allow_unicode=True, sort_keys=False)
    return metadata


def send_email(metadata):
    msg = EmailMessage()
    msg["From"] = MAIL_FROM
    msg["To"] = TICKET_EMAIL
    msg["Subject"] = f"New IR submission {metadata['id']}"
    msg.set_content(yaml.safe_dump(metadata, allow_unicode=True, sort_keys=False))
    with smtplib.SMTP(SMTP_HOST, 25, timeout=60) as s:
        s.send_message(msg)


def last_modified(sub):
    return sub.get("updated_at") or sub.get("created_at") or ""


def on_disk_version(sub_id):
    path = OUT_DIR / sub_id / "metadata.yml"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        existing = yaml.safe_load(f) or {}
    return existing.get("updated_at") or existing.get("created_at") or ""


def poll():
    for sub in fetch_submissions():
        if sub.get("status") == "DELETED":
            continue
        on_disk = on_disk_version(sub["id"])
        # Checks if existing submissions was actually updated before overwriting the one on disk.
        if on_disk is not None and last_modified(sub) <= on_disk:
            continue
        try:
            metadata = save_submission(sub)
            log.info("Saved submission %s", sub["id"])
            #send_email(metadata)
        except Exception:
            log.exception("Failed processing submission %s", sub["id"])



if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    while True:
        try:
            poll()
        except Exception:
            log.exception("Poll failed")
        time.sleep(INTERVAL)
