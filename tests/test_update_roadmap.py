"""
tests/test_update_roadmap.py
============================
Unit tests for update_roadmap.py (no network or Google Drive calls required).
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure the parent directory is on the path so the module can be imported
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import update_roadmap as urm


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_issue(
    key="PROJ-1",
    summary="Test Epic",
    status="In Progress",
    labels=None,
    initiative=None,
    biz_obj=None,
    assignee="Alice",
    priority="High",
    start_date="2024-01-01",
    due_date="2024-06-30",
    fix_versions=None,
    description=None,
):
    fields = {
        "summary": summary,
        "status": {"name": status},
        "labels": labels or [],
        "assignee": {"displayName": assignee},
        "priority": {"name": priority},
        "startDate": start_date,
        "duedate": due_date,
        "fixVersions": [{"name": v} for v in (fix_versions or [])],
        "description": description,
        urm.FIELD_INITIATIVE: {"name": initiative} if initiative else None,
        urm.FIELD_BIZ_OBJ: biz_obj,
    }
    return {"key": key, "fields": fields}


# ── _text_from_adf ─────────────────────────────────────────────────────────────

class TestTextFromAdf(unittest.TestCase):

    def test_none_returns_empty(self):
        self.assertEqual(urm._text_from_adf(None), "")

    def test_plain_string(self):
        self.assertEqual(urm._text_from_adf("hello"), "hello")

    def test_text_node(self):
        node = {"type": "text", "text": "Hello World"}
        self.assertEqual(urm._text_from_adf(node), "Hello World")

    def test_nested_doc(self):
        node = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "First"},
                        {"type": "text", "text": " Second"},
                    ],
                }
            ],
        }
        result = urm._text_from_adf(node)
        self.assertIn("First", result)
        self.assertIn("Second", result)

    def test_list_of_nodes(self):
        nodes = [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]
        result = urm._text_from_adf(nodes)
        self.assertIn("A", result)
        self.assertIn("B", result)


# ── _field_value ───────────────────────────────────────────────────────────────

class TestFieldValue(unittest.TestCase):

    def test_none_returns_empty(self):
        self.assertEqual(urm._field_value({}, "missing"), "")

    def test_string_field(self):
        self.assertEqual(urm._field_value({"f": "hello"}, "f"), "hello")

    def test_dict_with_name(self):
        self.assertEqual(urm._field_value({"f": {"name": "Sprint 1"}}, "f"), "Sprint 1")

    def test_dict_with_value(self):
        self.assertEqual(urm._field_value({"f": {"value": "Q1"}}, "f"), "Q1")

    def test_list_of_dicts(self):
        val = [{"name": "A"}, {"name": "B"}]
        result = urm._field_value({"f": val}, "f")
        self.assertIn("A", result)
        self.assertIn("B", result)

    def test_list_of_strings(self):
        val = ["foo", "bar"]
        result = urm._field_value({"f": val}, "f")
        self.assertIn("foo", result)
        self.assertIn("bar", result)


# ── normalise_epic ─────────────────────────────────────────────────────────────

class TestNormaliseEpic(unittest.TestCase):

    def setUp(self):
        os.environ["JIRA_URL"] = "https://example.atlassian.net"

    def tearDown(self):
        os.environ.pop("JIRA_URL", None)

    def test_basic_fields(self):
        issue = _make_issue(
            key="PROJ-42",
            summary="My Epic",
            status="Done",
            labels=["alpha", "beta"],
            initiative="Initiative X",
            biz_obj="Grow Revenue",
            assignee="Bob",
            priority="Medium",
            start_date="2024-03-01",
            due_date="2024-09-30",
            fix_versions=["v1.0", "v2.0"],
        )
        epic = urm.normalise_epic(issue)
        self.assertEqual(epic["key"], "PROJ-42")
        self.assertEqual(epic["summary"], "My Epic")
        self.assertEqual(epic["status"], "Done")
        self.assertEqual(epic["labels"], ["alpha", "beta"])
        self.assertEqual(epic["initiative"], "Initiative X")
        self.assertEqual(epic["business_objective"], "Grow Revenue")
        self.assertEqual(epic["assignee"], "Bob")
        self.assertEqual(epic["priority"], "Medium")
        self.assertEqual(epic["start_date"], "2024-03-01")
        self.assertEqual(epic["due_date"], "2024-09-30")
        self.assertEqual(epic["fix_versions"], ["v1.0", "v2.0"])
        self.assertIn("https://example.atlassian.net/browse/PROJ-42", epic["url"])

    def test_adf_description_extracted(self):
        desc = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Epic description text"}],
                }
            ],
        }
        issue = _make_issue(description=desc)
        epic = urm.normalise_epic(issue)
        self.assertIn("Epic description text", epic["description"])

    def test_long_description_truncated(self):
        long_desc = "x" * 300
        issue = _make_issue(description=long_desc)
        epic = urm.normalise_epic(issue)
        self.assertLessEqual(len(epic["description"]), 201)  # 200 chars + single "…" character

    def test_missing_optional_fields(self):
        issue = _make_issue()
        issue["fields"]["assignee"] = None
        issue["fields"]["priority"] = None
        epic = urm.normalise_epic(issue)
        self.assertEqual(epic["assignee"], "")
        self.assertEqual(epic["priority"], "")


# ── HTML generation ────────────────────────────────────────────────────────────

class TestBuildTableRow(unittest.TestCase):

    def _sample_epic(self, **kwargs):
        defaults = dict(
            key="PROJ-1",
            url="https://example.atlassian.net/browse/PROJ-1",
            summary="Sample Epic",
            status="In Progress",
            initiative="Grow",
            business_objective="Revenue",
            labels=["tag1", "tag2"],
            assignee="Alice",
            priority="High",
            start_date="2024-01-01",
            due_date="2024-12-31",
            fix_versions=["v1.0"],
            description="A short description",
        )
        defaults.update(kwargs)
        return defaults

    def test_key_appears_in_row(self):
        row = urm.build_table_row(self._sample_epic())
        self.assertIn("PROJ-1", row)

    def test_status_badge_in_progress(self):
        row = urm.build_table_row(self._sample_epic(status="In Progress"))
        self.assertIn("badge-inprogress", row)

    def test_status_badge_done(self):
        row = urm.build_table_row(self._sample_epic(status="Done"))
        self.assertIn("badge-done", row)

    def test_label_pills_rendered(self):
        row = urm.build_table_row(self._sample_epic(labels=["my-label"]))
        self.assertIn("label-pill", row)
        self.assertIn("my-label", row)

    def test_xss_in_summary_escaped(self):
        row = urm.build_table_row(self._sample_epic(summary="<script>alert(1)</script>"))
        self.assertNotIn("<script>", row)
        self.assertIn("&lt;script&gt;", row)

    def test_xss_in_label_escaped(self):
        row = urm.build_table_row(self._sample_epic(labels=['<img src=x onerror="alert(1)">']))
        self.assertNotIn("<img", row)

    def test_fix_versions_rendered(self):
        row = urm.build_table_row(self._sample_epic(fix_versions=["v1.0", "v2.0"]))
        self.assertIn("v1.0", row)
        self.assertIn("v2.0", row)


class TestGenerateHtml(unittest.TestCase):

    def _sample_epic(self):
        return {
            "key": "TEST-1",
            "url": "#",
            "summary": "Test Epic",
            "status": "To Do",
            "initiative": "Platform",
            "business_objective": "Cost Reduction",
            "labels": ["core"],
            "assignee": "Charlie",
            "priority": "Low",
            "start_date": "",
            "due_date": "",
            "fix_versions": [],
            "description": "",
        }

    def test_html_contains_epic_key(self):
        result = urm.generate_html([self._sample_epic()])
        self.assertIn("TEST-1", result)

    def test_html_contains_last_updated(self):
        result = urm.generate_html([self._sample_epic()])
        self.assertIn("UTC", result)

    def test_html_contains_epics_json(self):
        result = urm.generate_html([self._sample_epic()])
        self.assertIn('"key": "TEST-1"', result)

    def test_no_unfilled_placeholders(self):
        result = urm.generate_html([self._sample_epic()])
        self.assertNotIn("{{", result)
        self.assertNotIn("}}", result)

    def test_empty_epics_list(self):
        result = urm.generate_html([])
        self.assertIn("[]", result)  # epics_json should be an empty JSON array


# ── JiraClient ─────────────────────────────────────────────────────────────────

class TestJiraClient(unittest.TestCase):

    def _make_client(self):
        return urm.JiraClient("https://example.atlassian.net", "user@example.com", "token123")

    def test_get_board_project_key(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"location": {"projectKey": "MYPROJ"}}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client.session, "get", return_value=mock_resp):
            key = client.get_board_project_key(42)
        self.assertEqual(key, "MYPROJ")

    def test_fetch_epics_single_page(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "issues": [_make_issue("PROJ-1"), _make_issue("PROJ-2")],
            "total": 2,
        }
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client.session, "get", return_value=mock_resp):
            issues = client.fetch_epics("PROJ")
        self.assertEqual(len(issues), 2)

    def test_fetch_epics_pagination(self):
        client = self._make_client()

        page1 = {"issues": [_make_issue(f"PROJ-{i}") for i in range(100)], "total": 150}
        page2 = {"issues": [_make_issue(f"PROJ-{i}") for i in range(100, 150)], "total": 150}

        responses = [page1, page2]
        call_count = 0

        def fake_get(url, **kwargs):
            nonlocal call_count
            mock = MagicMock()
            mock.raise_for_status = MagicMock()
            mock.json.return_value = responses[call_count]
            call_count += 1
            return mock

        with patch.object(client.session, "get", side_effect=fake_get):
            issues = client.fetch_epics("PROJ")
        self.assertEqual(len(issues), 150)
        self.assertEqual(call_count, 2)

    def test_http_error_propagates(self):
        client = self._make_client()
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP 401")
        with patch.object(client.session, "get", return_value=mock_resp):
            with self.assertRaises(Exception):
                client.get_board_project_key(1)


# ── upload_to_drive ────────────────────────────────────────────────────────────

class TestUploadToDrive(unittest.TestCase):

    def test_drive_update_called(self):
        mock_service = MagicMock()
        mock_files = MagicMock()
        mock_service.files.return_value = mock_files
        mock_files.update.return_value.execute.return_value = {}

        with patch("update_roadmap.MediaFileUpload") as mock_media:
            urm.upload_to_drive(mock_service, "file-id-123", "<html></html>")

        mock_files.update.assert_called_once()
        call_kwargs = mock_files.update.call_args[1]
        self.assertEqual(call_kwargs["fileId"], "file-id-123")


if __name__ == "__main__":
    unittest.main()
