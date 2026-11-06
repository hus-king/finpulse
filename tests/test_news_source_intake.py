import copy
import unittest

from experiments.news_processing.sources import select_announcement, assert_role_disjoint, class_group_gate
from experiments.news_processing.dataset import import_records
from tests.test_news_processing import record


class SourceIntakeTests(unittest.TestCase):
    def test_wrong_issuer_and_announcement_period_are_rejected(self):
        slot = {"code": "600031", "slot_id": "own_q1_600031", "params": {"seDate": "2026-04-01~2026-05-31"}}
        raw = [{"secCode": "600019", "announcementTitle": "2026年第一季度报告", "announcementTime": 1777478400000, "adjunctUrl": "x.pdf"},
               {"secCode": "600031", "announcementTitle": "2026年半年度报告", "announcementTime": 1777478400000, "adjunctUrl": "y.pdf"}]
        self.assertIsNone(select_announcement(slot, raw))
        raw.append({"secCode": "600031", "announcementTitle": "三一重工2026年第一季度报告", "announcementTime": 1777478400000, "adjunctUrl": "finalpage/2026-04-30/z.PDF"})
        self.assertEqual(select_announcement(slot, raw)["adjunctUrl"], "finalpage/2026-04-30/z.PDF")

    def test_timestamp_outside_fixed_window_is_rejected(self):
        slot = {"code": "600031", "slot_id": "own_q1_600031", "params": {"seDate": "2026-04-01~2026-05-31"}}
        raw = [{"secCode": "600031", "announcementTitle": "2026年第一季度报告", "announcementTime": 0, "adjunctUrl": "x.pdf"}]
        self.assertIsNone(select_announcement(slot, raw))

    def test_failed_quality_cannot_be_promoted_by_nonempty_metadata(self):
        r = record(); r["state"]["news"]["text"] *= 8
        r["quality"]["date"] = "pending"
        r["material_provenance"] = "new_full_source_with_pending_publication_date"
        imported = import_records([r], {"steel": "train"})[0]
        self.assertEqual(imported["quality"]["date"], "pending")
        self.assertEqual(imported["material_provenance"], r["material_provenance"])

    def test_source_url_variants_cannot_cross_roles(self):
        a = record(); a["split"] = "train"
        b = copy.deepcopy(a); b.update(id="other", group_id="other", split="validation")
        b["source"]["url"] += "#top"
        with self.assertRaises(ValueError): assert_role_disjoint([a, b])

    def test_same_event_group_cannot_cross_roles(self):
        a = record(); a["split"] = "train"
        b = copy.deepcopy(a); b.update(id="other", split="calibration")
        b["source"]["url"] = "https://other.org/source"
        with self.assertRaises(ValueError): assert_role_disjoint([a, b])

    def test_group_gate_does_not_count_multiple_companies_as_multiple_events(self):
        a = record(); a.update(split="calibration", expected={"own_event":"YES", "industry_relation":"POSSIBLE"})
        b = copy.deepcopy(a); b["id"] = "second-company"
        gate = class_group_gate({"train":[],"validation":[],"calibration":[a,b]}, minimum=2)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["group_counts"]["calibration"]["own_event"]["YES"], 1)


if __name__ == "__main__": unittest.main()
