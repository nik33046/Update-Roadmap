"""
update_roadmap.py
=================
Fetches all Epics from a JIRA board (including labels, business objective,
initiative and other custom fields) and updates the wd-roadmap.html file on
Google Drive.

Required environment variables
-------------------------------
JIRA_URL          – Base URL of the JIRA instance (e.g. https://mycompany.atlassian.net)
JIRA_USER         – JIRA user e-mail / username used for authentication
JIRA_API_TOKEN    – JIRA API token (or password for on-prem instances)
JIRA_BOARD_ID     – Numeric ID of the JIRA board to query
GDRIVE_FILE_ID    – Google Drive file ID of wd-roadmap.html
GOOGLE_CREDENTIALS_JSON – Contents of a Google service-account credentials JSON
                          (the whole JSON object as a string)

Optional environment variables
-------------------------------
JIRA_EPIC_LINK_FIELD  – Custom field name for Epic Link (default: customfield_10014)
JIRA_INITIATIVE_FIELD – Custom field name for Initiative   (default: customfield_10020)
JIRA_BIZ_OBJ_FIELD    – Custom field name for Business Objective (default: customfield_10021)
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from typing import Any

import requests
from dotenv import load_dotenv
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

GDRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]
MAX_RESULTS_PER_PAGE = 100

# Custom-field environment variable names (with sensible defaults).
FIELD_EPIC_LINK = os.environ.get("JIRA_EPIC_LINK_FIELD", "customfield_10014")
FIELD_INITIATIVE = os.environ.get("JIRA_INITIATIVE_FIELD", "customfield_10020")
FIELD_BIZ_OBJ = os.environ.get("JIRA_BIZ_OBJ_FIELD", "customfield_10021")

TEMPLATE_PATH = os.path.join(os.path.dirname(__file__), "roadmap_template.html")


# ── JIRA helpers ───────────────────────────────────────────────────────────────


class JiraClient:
    """Thin wrapper around the JIRA REST API v3."""

    def __init__(self, base_url: str, user: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.auth = (user, token)
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json"}
        )

    def _get(self, path: str, **params: Any) -> Any:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def get_board_project_key(self, board_id: int) -> str:
        """Return the project key associated with a board."""
        data = self._get(f"/rest/agile/1.0/board/{board_id}")
        return data["location"]["projectKey"]

    def get_custom_fields(self) -> dict[str, str]:
        """Return a mapping of field ID → display name."""
        fields = self._get("/rest/api/3/field")
        return {f["id"]: f["name"] for f in fields}

    def fetch_epics(self, project_key: str) -> list[dict]:
        """
        Fetch all issues of type Epic for the given project using the
        JIRA search API (paginated).
        """
        jql = f'project = "{project_key}" AND issuetype = Epic ORDER BY key ASC'
        extra_fields = [
            "summary",
            "status",
            "assignee",
            "priority",
            "labels",
            "fixVersions",
            "startDate",
            "duedate",
            "description",
            FIELD_INITIATIVE,
            FIELD_BIZ_OBJ,
            FIELD_EPIC_LINK,
            # Standard Atlassian epic-name custom field (may be absent in newer schemas)
            "customfield_10011",
        ]
        fields_param = ",".join(extra_fields)

        start_at = 0
        all_issues: list[dict] = []
        while True:
            data = self._get(
                "/rest/api/3/search",
                jql=jql,
                startAt=start_at,
                maxResults=MAX_RESULTS_PER_PAGE,
                fields=fields_param,
            )
            issues = data.get("issues", [])
            all_issues.extend(issues)
            log.info(
                "Fetched %d / %d epics", len(all_issues), data.get("total", "?")
            )
            if start_at + len(issues) >= data.get("total", 0):
                break
            start_at += len(issues)

        return all_issues


# ── Data normalisation ─────────────────────────────────────────────────────────


def _text_from_adf(node: Any) -> str:
    """Recursively extract plain text from an Atlassian Document Format node."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if node.get("type") == "text":
            return node.get("text", "")
        return " ".join(_text_from_adf(c) for c in node.get("content", []))
    if isinstance(node, list):
        return " ".join(_text_from_adf(c) for c in node)
    return ""


def _field_value(fields: dict, field_id: str) -> str:
    """Extract a human-readable string from a JIRA field value (handles various types)."""
    val = fields.get(field_id)
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, dict):
        # Sprint, Initiative, or similar objects that carry a 'name' or 'value'.
        return val.get("name") or val.get("value") or val.get("displayName") or ""
    if isinstance(val, list):
        parts: list[str] = []
        for item in val:
            if isinstance(item, dict):
                parts.append(
                    item.get("name") or item.get("value") or item.get("displayName") or ""
                )
            elif isinstance(item, str):
                parts.append(item)
        return ", ".join(filter(None, parts))
    return str(val)


def normalise_epic(issue: dict) -> dict:
    """Convert a raw JIRA issue dict into a simplified epic dict."""
    f = issue.get("fields", {})
    key = issue.get("key", "")
    base_url = os.environ.get("JIRA_URL", "").rstrip("/")

    status_obj = f.get("status") or {}
    status = status_obj.get("name", "")

    assignee_obj = f.get("assignee") or {}
    assignee = assignee_obj.get("displayName", "")

    priority_obj = f.get("priority") or {}
    priority = priority_obj.get("name", "")

    labels = f.get("labels") or []

    fix_versions = [v.get("name", "") for v in (f.get("fixVersions") or [])]

    initiative = _field_value(f, FIELD_INITIATIVE)
    biz_obj = _field_value(f, FIELD_BIZ_OBJ)

    # Description may be ADF (dict) or plain text
    raw_desc = f.get("description")
    description = _text_from_adf(raw_desc) if isinstance(raw_desc, dict) else (raw_desc or "")
    # Truncate long descriptions for the table cell
    if len(description) > 200:
        description = description[:197] + "…"

    return {
        "key": key,
        "url": f"{base_url}/browse/{key}" if base_url else "#",
        "summary": f.get("summary") or "",
        "status": status,
        "initiative": initiative,
        "business_objective": biz_obj,
        "labels": labels,
        "assignee": assignee,
        "priority": priority,
        "start_date": f.get("startDate") or "",
        "due_date": f.get("duedate") or "",
        "fix_versions": fix_versions,
        "description": description,
    }


# ── HTML generation ────────────────────────────────────────────────────────────

_STATUS_CLASS = {
    "in progress": "badge-inprogress",
    "done": "badge-done",
    "closed": "badge-done",
    "blocked": "badge-blocked",
}


def _status_badge(status: str) -> str:
    css = _STATUS_CLASS.get(status.lower(), "badge-todo")
    return f'<span class="badge {css}">{html.escape(status)}</span>'


def _label_pills(labels: list[str]) -> str:
    return "".join(
        f'<span class="label-pill">{html.escape(lbl)}</span>' for lbl in labels
    )


def _fix_versions(versions: list[str]) -> str:
    return html.escape(", ".join(versions))


def build_table_row(epic: dict) -> str:
    labels_attr = ",".join(epic["labels"])
    return (
        f'<tr data-initiative="{html.escape(epic["initiative"])}" '
        f'data-status="{html.escape(epic["status"])}" '
        f'data-labels="{html.escape(labels_attr)}">'
        f'<td><a class="epic-key" href="{html.escape(epic["url"])}" '
        f'target="_blank" rel="noopener">{html.escape(epic["key"])}</a></td>'
        f'<td title="{html.escape(epic["description"])}">{html.escape(epic["summary"])}</td>'
        f'<td>{_status_badge(epic["status"])}</td>'
        f'<td>{html.escape(epic["initiative"])}</td>'
        f'<td>{html.escape(epic["business_objective"])}</td>'
        f'<td>{_label_pills(epic["labels"])}</td>'
        f'<td>{html.escape(epic["assignee"])}</td>'
        f'<td>{html.escape(epic["priority"])}</td>'
        f'<td>{html.escape(epic["start_date"])}</td>'
        f'<td>{html.escape(epic["due_date"])}</td>'
        f'<td>{_fix_versions(epic["fix_versions"])}</td>'
        f'</tr>'
    )


def generate_html(epics: list[dict]) -> str:
    with open(TEMPLATE_PATH, encoding="utf-8") as fh:
        template = fh.read()

    rows_html = "\n".join(build_table_row(e) for e in epics)
    epics_json = json.dumps(epics, ensure_ascii=False)
    last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    result = template
    result = result.replace("{{TABLE_ROWS}}", rows_html)
    result = result.replace("{{EPICS_JSON}}", epics_json)
    result = result.replace("{{LAST_UPDATED}}", last_updated)
    return result


# ── Google Drive helpers ───────────────────────────────────────────────────────


def get_drive_service():
    """Build and return an authorised Google Drive service client."""
    creds_json = os.environ.get("GOOGLE_CREDENTIALS_JSON", "")
    if not creds_json:
        raise EnvironmentError(
            "GOOGLE_CREDENTIALS_JSON environment variable is not set."
        )
    creds_info = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=GDRIVE_SCOPES
    )
    return build("drive", "v3", credentials=creds, cache_discovery=False)


def upload_to_drive(service, file_id: str, html_content: str) -> None:
    """Overwrite the Google Drive file identified by *file_id* with *html_content*."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(html_content)
        tmp_path = tmp.name

    try:
        media = MediaFileUpload(tmp_path, mimetype="text/html", resumable=False)
        service.files().update(fileId=file_id, media_body=media).execute()
        log.info("Successfully updated Google Drive file: %s", file_id)
    finally:
        os.unlink(tmp_path)


# ── Entrypoint ─────────────────────────────────────────────────────────────────


def _require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        log.error("Required environment variable '%s' is not set.", name)
        sys.exit(1)
    return value


def main() -> None:
    log.info("=== WD Roadmap Updater starting ===")

    jira_url = _require_env("JIRA_URL")
    jira_user = _require_env("JIRA_USER")
    jira_token = _require_env("JIRA_API_TOKEN")
    board_id_str = _require_env("JIRA_BOARD_ID")
    gdrive_file_id = _require_env("GDRIVE_FILE_ID")

    if not re.match(r"^\d+$", board_id_str):
        log.error("JIRA_BOARD_ID must be a numeric value, got: %s", board_id_str)
        sys.exit(1)
    board_id = int(board_id_str)

    # 1. Connect to JIRA and fetch epics
    client = JiraClient(jira_url, jira_user, jira_token)

    log.info("Resolving project key for board %d …", board_id)
    project_key = client.get_board_project_key(board_id)
    log.info("Project key: %s", project_key)

    log.info("Fetching epics …")
    raw_issues = client.fetch_epics(project_key)
    log.info("Total epics fetched: %d", len(raw_issues))

    # 2. Normalise
    epics = [normalise_epic(issue) for issue in raw_issues]

    # 3. Generate HTML
    log.info("Generating HTML …")
    html_content = generate_html(epics)

    # 4. Upload to Google Drive
    log.info("Uploading to Google Drive (file ID: %s) …", gdrive_file_id)
    drive_service = get_drive_service()
    upload_to_drive(drive_service, gdrive_file_id, html_content)

    log.info("=== Done. %d epics published to the roadmap. ===", len(epics))


if __name__ == "__main__":
    main()
