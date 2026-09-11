import json
import unittest
from pathlib import Path

from pipeline import accuracy
from pipeline import model_accuracy as A


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_HISTORY_FIELDS = {
    "book", "edge", "line", "pick", "price", "probability", "selected",
    "side", "tier", "units",
}


def projection(**kwargs):
    row = {
        "kind": "prop",
        "league": "NFL",
        "event_id": "1",
        "start": "2026-09-10T18:00:00+00:00",
        "matchup": "Away at Home",
        "player": "Example Player",
        "market": "Receiving yards",
        "projection": 72.5,
        "stat_key": "example|home|receiving_yards",
        "season": 2026,
        "season_type": 2,
        "captured_at": "2026-09-01T12:00:00+00:00",
        "result": "Pending",
        "version": "test",
    }
    row.update(kwargs)
    row["id"] = A.key(row)
    return row


class AccuracyPrivacyTests(unittest.TestCase):
    def test_projection_log_drops_calls_other_seasons_and_wager_fields(self):
        current = projection(price=-110, book="Example", units=2, tier="BEST")
        old = projection(event_id="old", season=2025)
        call = {**current, "id": "call", "kind": "call"}
        clean = accuracy.current_projection_log(
            {"records": {"current": current, "old": old, "call": call}}, 2026
        )
        self.assertTrue(clean["projection_only"])
        self.assertEqual(len(clean["records"]), 1)
        saved = next(iter(clean["records"].values()))
        self.assertEqual(saved["kind"], "prop")
        self.assertFalse(FORBIDDEN_HISTORY_FIELDS & saved.keys())

    def test_projection_report_has_no_pick_or_unit_sections(self):
        current = projection(price=-110, book="Example", units=2, tier="BEST")
        report = A.report(
            {"records": {
                current["id"]: current,
                "second": projection(event_id="2", player="Second Player"),
            }},
            season=2026,
            season_type=2,
            projection_only=True,
            record_limit=1,
        )
        self.assertEqual(report["schema"], 3)
        self.assertTrue(report["projection_only"])
        for key in ("overall", "all_calls", "by_tier", "calibration", "games"):
            self.assertNotIn(key, report)
        self.assertFalse(FORBIDDEN_HISTORY_FIELDS & report["records"][0].keys())
        self.assertEqual(report["history"], {
            "total": 2, "displayed": 1, "truncated": True,
        })

    def test_committed_accuracy_files_are_projection_only(self):
        state = json.loads((ROOT / "state/model_accuracy.json").read_text())
        public = json.loads((ROOT / "site/data/accuracy.json").read_text())
        self.assertTrue(state.get("projection_only"))
        self.assertTrue(public.get("projection_only"))
        for payload in (state, public):
            rows = (payload.get("records") or {}).values() if isinstance(
                payload.get("records"), dict
            ) else payload.get("records", [])
            rows = list(rows)
            self.assertTrue(rows)
            self.assertEqual({row.get("kind") for row in rows}, {"prop"})
            self.assertTrue(all(not (FORBIDDEN_HISTORY_FIELDS & row.keys()) for row in rows))


if __name__ == "__main__":
    unittest.main()
