# Update-Roadmap

This utility fetches all **Epics** from a JIRA board (including metadata such as
labels, business objective, initiative, assignee, priority, fix versions and dates)
and publishes a styled `wd-roadmap.html` file to Google Drive once per day, so that
Google Sites always shows the latest roadmap.

---

## How it works

```
GitHub Actions (daily cron)
        │
        ▼
update_roadmap.py
  ├── JiraClient  →  JIRA REST API v3  →  fetch all Epics + metadata
  ├── normalise_epic()  →  clean / flatten each Epic
  ├── generate_html()  →  render roadmap_template.html with live data
  └── upload_to_drive()  →  overwrite wd-roadmap.html on Google Drive
```

The resulting HTML file is self-contained, includes client-side filters
(by Initiative, Status, Label) and a full-text search box.

---

## Repository layout

```
.
├── update_roadmap.py       # Main script
├── roadmap_template.html   # HTML / CSS / JS template (placeholders replaced at runtime)
├── requirements.txt        # Python dependencies
├── .env.example            # Environment variable reference (copy to .env locally)
├── .github/
│   └── workflows/
│       └── update-roadmap.yml  # GitHub Actions workflow (daily + manual trigger)
└── tests/
    └── test_update_roadmap.py  # Unit tests (32 tests, no network required)
```

---

## Setup

### 1 – Configure GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add the following secrets:

| Secret | Description |
|--------|-------------|
| `JIRA_URL` | Base URL of the JIRA instance, e.g. `https://mycompany.atlassian.net` |
| `JIRA_USER` | JIRA user e-mail / username |
| `JIRA_API_TOKEN` | JIRA API token ([generate here](https://id.atlassian.com/manage-profile/security/api-tokens)) |
| `JIRA_BOARD_ID` | Numeric ID of the JIRA board (visible in the board URL as `rapidView=<ID>`) |
| `GDRIVE_FILE_ID` | Google Drive file ID of `wd-roadmap.html` (the long string in the Drive URL) |
| `GOOGLE_CREDENTIALS_JSON` | Full contents of a Google service-account credentials JSON |

Optional secrets to override default custom-field IDs:

| Secret | Default | Description |
|--------|---------|-------------|
| `JIRA_EPIC_LINK_FIELD` | `customfield_10014` | Custom field ID for Epic Link |
| `JIRA_INITIATIVE_FIELD` | `customfield_10020` | Custom field ID for Initiative |
| `JIRA_BIZ_OBJ_FIELD` | `customfield_10021` | Custom field ID for Business Objective |

### 2 – Google service account

1. Create a service account in [Google Cloud Console](https://console.cloud.google.com/).
2. Enable the **Google Drive API** for the project.
3. Download the JSON key and store the full contents as the `GOOGLE_CREDENTIALS_JSON` secret.
4. Share the `wd-roadmap.html` Drive file with the service account e-mail as **Editor**.

### 3 – Local development

```bash
# Clone and install
pip install -r requirements.txt

# Copy and fill in environment variables
cp .env.example .env
# Edit .env with your real values

# Run once
python update_roadmap.py

# Run tests
python -m pytest tests/ -v
```

---

## Schedule

The workflow runs automatically every day at **06:00 UTC**.
It can also be triggered manually from the **Actions** tab using the _workflow_dispatch_ event.
