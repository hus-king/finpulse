import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.cb_research import publish_snapshot, read_research, record_execution
from backend.cb_research_api import router
from tests.test_cb_research_rules import bond, comparable_pool
from backend.news_cleaning import SHANGHAI


TEST_NOW = datetime(2026, 11, 11, 16, tzinfo=SHANGHAI)


class ConvertibleBondResearchStoreTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "research.sqlite3"
        self.first = {"code": "123001.SZ", "name": "甲转债", "rank": 1}
        self.second = {"code": "123002.SZ", "name": "乙转债", "rank": 1}

    def publish(self, as_of, candidate, action="enter", *, preview=False):
        entries = [candidate]
        if candidate == self.second:
            entries.append({**self.first, "rank": 2})
        with patch("backend.cb_research.evaluate_rows", return_value=entries), patch(
            "backend.cb_research.decide",
            return_value={"action": action, "reason": "研究信号", "candidate_code": candidate["code"],
                          "thesis_failures": 0},
        ):
            return publish_snapshot(
                {"as_of": as_of, "close_confirmed": True, "sources": ["test source"], "warnings": [], "rows": [candidate]},
                self.path, preview=preview, now=TEST_NOW,
            )

    def test_public_api_serves_the_evaluated_snapshot_and_confirmed_position(self):
        candidate = bond(self.first["code"], price=113, stock_return_20d=0.07)
        peers = [{**row, "code": f"127{index:03d}.SZ"} for index, row in enumerate(comparable_pool())]
        app = FastAPI()
        app.include_router(router)
        with patch.dict("os.environ", {"FINPULSE_CB_RESEARCH_PATH": str(self.path)}):
            client = TestClient(app)
            self.assertEqual(client.get("/api/convertible-bonds/research").json()["status"], "no_data")
            publish_snapshot({"as_of": "2026-11-06", "close_confirmed": True, "rows": [candidate, *peers]},
                             self.path, now=TEST_NOW)
            pending = client.get("/api/convertible-bonds/research").json()
            self.assertEqual(pending["decision"]["action"], "enter")
            self.assertEqual(pending["entries"][0]["code"], candidate["code"])
            self.assertEqual(pending["entries"][0]["peer_count"], 5)
            self.assertIsNone(pending["position_code"])
            record_execution("2026-11-06", "2026-11-09", self.path, buy_price=114, cost=0.002, now=TEST_NOW)
            filled = client.get("/api/convertible-bonds/research").json()
            self.assertEqual(filled["position_code"], candidate["code"])
            self.assertEqual(filled["execution"]["status"], "filled")

    def test_preview_has_no_trade_signal_or_history(self):
        self.assertEqual(read_research(self.path)["status"], "no_data")
        result = self.publish("2026-11-10", self.first, preview=True)
        self.assertTrue(result["preview"])
        self.assertEqual(result["entries"], [self.first])
        self.assertIsNone(result["decision"])
        self.assertEqual(result["history"], [])

    def test_pending_and_blocked_switch_preserve_confirmed_position(self):
        self.publish("2026-10-30", self.first)
        self.assertIsNone(read_research(self.path)["position_code"])
        record_execution("2026-10-30", "2026-11-02", self.path, buy_price=110, cost=0.001, now=TEST_NOW)
        pending = self.publish("2026-11-06", self.second, "switch")
        self.assertEqual(pending["position_code"], self.first["code"])
        self.assertEqual(pending["decision"]["position_code"], self.first["code"])
        self.assertEqual(pending["execution"]["status"], "pending")
        blocked = record_execution("2026-11-06", "2026-11-09", self.path, blocked="原券停牌", now=TEST_NOW)
        self.assertEqual(blocked["position_code"], self.first["code"])
        self.assertEqual(blocked["history"][0]["execution"]["status"], "blocked")
        self.assertEqual(blocked["history"][1]["execution"]["status"], "filled")

    def test_switch_updates_position_only_after_both_execution_prices(self):
        self.publish("2026-10-30", self.first)
        record_execution("2026-10-30", "2026-11-02", self.path, buy_price=110, cost=0.001, now=TEST_NOW)
        self.publish("2026-11-06", self.second, "switch")
        with self.assertRaises(ValueError):
            record_execution("2026-11-06", "2026-11-09", self.path, buy_price=113, cost=0.004, now=TEST_NOW)
        self.assertEqual(read_research(self.path)["position_code"], self.first["code"])
        filled = record_execution("2026-11-06", "2026-11-09", self.path, sell_price=114, buy_price=113, cost=0.004, now=TEST_NOW)
        self.assertEqual(filled["position_code"], self.second["code"])
        self.assertEqual(filled["history"][0]["position"]["code"], self.first["code"])
        self.assertEqual(filled["history"][0]["candidate"]["code"], self.second["code"])
        with self.assertRaises(ValueError):
            record_execution("2026-11-06", "2026-11-09", self.path, sell_price=114, buy_price=113, cost=0.004, now=TEST_NOW)

    def test_risk_exit_overrides_pending_switch_without_changing_position(self):
        self.publish("2026-10-30", self.first)
        record_execution("2026-10-30", "2026-11-02", self.path, buy_price=110, cost=0.001, now=TEST_NOW)
        self.publish("2026-11-06", self.second, "switch")
        risk = self.publish("2026-11-10", self.second, "exit")
        self.assertEqual(risk["position_code"], self.first["code"])
        self.assertEqual(risk["history"][1]["execution"]["status"], "blocked")
        self.assertEqual(risk["execution"]["status"], "pending")
        filled = record_execution("2026-11-10", "2026-11-11", self.path, sell_price=108, cost=0.002, now=TEST_NOW)
        self.assertIsNone(filled["position_code"])

    def test_rejects_unconfirmed_close_and_unverified_execution_date(self):
        with self.assertRaises(ValueError):
            publish_snapshot({"as_of": "2026-11-06", "rows": [self.first]}, self.path)
        self.publish("2026-11-06", self.first)
        with patch("backend.cb_research._next_session", return_value=None), self.assertRaises(ValueError):
            record_execution("2026-11-06", "2026-11-10", self.path, buy_price=110, cost=0, now=TEST_NOW)
        self.assertIsNone(read_research(self.path)["position_code"])

    def test_rejects_non_weekly_signal_and_duplicate_dates(self):
        with self.assertRaises(ValueError):
            self.publish("2026-11-10", self.first)
        self.publish("2026-11-06", self.first)
        with self.assertRaises(ValueError):
            self.publish("2026-11-06", self.second)
        self.assertEqual(len(read_research(self.path)["history"]), 1)


if __name__ == "__main__":
    unittest.main()
