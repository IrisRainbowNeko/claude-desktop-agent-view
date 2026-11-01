import json
from pathlib import Path
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import server
from session_catalog import Metadata, SessionCatalog, project_identity


class CatalogTests(unittest.TestCase):
    def test_project_keys_respect_host_posix_case_and_windows_normalization(self):
        self.assertEqual(project_identity("D:\\Code\\RA2\\platform\\", None), project_identity("d:/code/ra2/platform", None))
        self.assertNotEqual(project_identity("/repo/Example", "host"), project_identity("/repo/example", "host"))
        self.assertNotEqual(project_identity("/repo/example", "host-a"), project_identity("/repo/example", "host-b"))
        self.assertNotEqual(project_identity("D:/one/platform", None), project_identity("D:/two/platform", None))

    def test_incremental_metadata_tracks_old_titles_partial_appends_and_truncation(self):
        with tempfile.TemporaryDirectory() as temp:
            file = Path(temp) / "session.jsonl"
            file.write_text(json.dumps({"cwd": "D:/猫/project", "type": "custom-title", "customTitle": "旧标题"}) + "\n", encoding="utf8")
            entry = Metadata(); entry.refresh(file)
            first_offset = entry.offset
            with patch.object(Path, "open", side_effect=AssertionError("unchanged transcript was read")):
                entry.refresh(file)
            with file.open("ab") as stream:
                stream.write(json.dumps({"type": "custom-title", "customTitle": "新标题"}).encode())
            entry.refresh(file)
            self.assertEqual(entry.offset, first_offset)
            with file.open("ab") as stream: stream.write(b"\n")
            entry.refresh(file)
            self.assertEqual(entry.custom_title, "新标题")
            file.write_text('{"type":"ai-title","aiTitle":"replacement"}\n', encoding="utf8")
            entry.refresh(file)
            self.assertIsNone(entry.cwd)
            self.assertIsNone(entry.custom_title)
            self.assertEqual(entry.title, "replacement")

    def test_waiting_and_finished_status_without_retaining_tool_inputs(self):
        entry = Metadata()
        stamp = "2026-11-01T12:00:00Z"
        entry.feed({"type": "assistant", "timestamp": stamp, "message": {"content": [
            {"type": "tool_use", "id": "q", "name": "AskUserQuestion", "input": {"private": "do not retain"}}]}})
        now = entry.started + 10
        entry.mtime = now
        self.assertEqual(entry.status(now), "waiting")
        self.assertNotIn("input", entry.open_tools["q"])
        entry.feed({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": "q"}]}})
        entry.feed({"type": "assistant", "message": {"stop_reason": "end_turn", "content": []}})
        self.assertEqual(entry.status(now, is_sub=True), "done")

    def test_catalog_indexes_every_file_and_removes_deleted_sessions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = []
            for i in range(55):
                file = root / f"s{i}.jsonl"
                file.write_text(json.dumps({"type": "custom-title", "customTitle": f"会话{i}", "cwd": "D:/project"}) + "\n", encoding="utf8")
                files.append((i, file))
            catalog = SessionCatalog(lambda: files, lambda _: [], lambda _: None, SimpleNamespace(get=lambda *_: None))
            catalog.refresh()
            self.assertFalse(catalog.indexing)
            self.assertEqual(len(catalog.rows), 55)
            files.pop()[1].unlink()
            catalog.refresh()
            self.assertEqual(len(catalog.rows), 54)


class NavigationApiTests(unittest.TestCase):
    def setUp(self):
        rows = []
        for i in range(1000):
            cwd = f"D:/projects/project-{i // 25}"
            rows.append({"id": f"session-{i:04}", "title": f"历史任务 {i}", "cwd": cwd,
                "host": None, "project": "encoded", "project_id": project_identity(cwd, None),
                "status": "running" if i == 999 else "idle", "running_subagents": 0,
                "subagents": 0, "mtime": 1000 - i})
        self.rows = rows
        snapshot = {"rows": rows, "indexing": False, "indexed": 1000, "expected": 1000, "index_error": None}
        self.patcher = patch.object(server, "CATALOG", SimpleNamespace(snapshot=lambda: snapshot))
        self.patcher.start(); self.addCleanup(self.patcher.stop)

    def test_search_finds_session_beyond_recent_forty(self):
        result = server.api_sessions({"q": ["历史任务 999"]})
        self.assertEqual([s["id"] for s in result["sessions"]], ["session-0999"])
        result = server.api_sessions({"active": ["1"]})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["sessions"][0]["id"], "session-0999")

    def test_project_and_session_pages_do_not_drop_history(self):
        first = server.api_sessions({"view": ["projects"], "limit": ["20"]})
        second = server.api_sessions({"view": ["projects"], "limit": ["20"], "offset": ["20"]})
        self.assertEqual(first["total"], 40)
        self.assertEqual(len(set(p["id"] for p in first["projects"] + second["projects"])), 40)
        project = first["projects"][0]["id"]
        one = server.api_sessions({"project": [project], "limit": ["20"]})
        two = server.api_sessions({"project": [project], "limit": ["20"], "offset": ["20"]})
        self.assertEqual(one["total"], 25)
        self.assertEqual(len(one["sessions"] + two["sessions"]), 25)

    def test_current_session_is_returned_outside_filter_and_locates_project_page(self):
        filtered = server.api_sessions({"view": ["projects"], "q": ["no match"], "current": ["session-0999"]})
        self.assertEqual(filtered["current"]["id"], "session-0999")
        self.assertFalse(filtered["current_matches"])
        located = server.api_sessions({"view": ["projects"], "limit": ["20"], "current": ["session-0999"], "locate": ["1"]})
        self.assertEqual(located["offset"], 20)
        self.assertIn(self.rows[-1]["project_id"], [p["id"] for p in located["projects"]])
        sessions = server.api_sessions({"project": [self.rows[-1]["project_id"]],
            "limit": ["20"], "current": ["session-0999"], "locate": ["1"]})
        self.assertEqual(sessions["offset"], 20)
        self.assertIn("session-0999", [s["id"] for s in sessions["sessions"]])

    def test_pins_sort_before_paging_and_bad_page_arguments_are_rejected(self):
        result = server.api_sessions({"view": ["projects"], "pinned": [self.rows[-1]["project_id"]]})
        self.assertEqual(result["projects"][0]["id"], self.rows[-1]["project_id"])
        for key, value in [("limit", "0"), ("offset", "-1"), ("limit", "oops")]:
            with self.assertRaises(ValueError): server.api_sessions({key: [value]})

    def test_both_single_document_and_isolated_native_panes_get_navigation_code(self):
        page = server.index_html()
        self.assertIn(b"root.AgentNavigation = api", page)
        self.assertNotIn(b"<!--NAVIGATION-->", page)
        self.assertNotIn(b'<script src=', page)


if __name__ == "__main__":
    unittest.main()
