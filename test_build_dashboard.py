import unittest

from build_dashboard import build_snapshot, parse_watches_and_warnings

PUBLIC_KEYS = {
    "last_check", "fetch_status", "storm_id", "storm_name", "summary", "alerts", "sources",
    "track", "wind_probabilities", "products", "events", "position",
}


class WatchWarningParsingTests(unittest.TestCase):
    def test_groups_official_watch_areas(self):
        product = """SUMMARY OF WATCHES AND WARNINGS IN EFFECT:

A Storm Surge Watch is in effect for...
* Mouth of the Mississippi River to Yankeetown

A Hurricane Watch is in effect for...
* Bay St. Louis to Indian Pass

DISCUSSION AND OUTLOOK
"""
        self.assertEqual(
            parse_watches_and_warnings(product),
            [
                {"type": "Storm Surge Watch", "areas": ["Mouth of the Mississippi River to Yankeetown"]},
                {"type": "Hurricane Watch", "areas": ["Bay St. Louis to Indian Pass"]},
            ],
        )

    def test_missing_watch_section_is_unknown_not_no_alerts(self):
        self.assertEqual(parse_watches_and_warnings("DISCUSSION AND OUTLOOK\ntext"), [])

    def test_snapshot_includes_issue_time_and_only_public_sources(self):
        tcp = """WTNT34 KNHC 071753
Tropical Storm Example Advisory Number 5
NWS National Hurricane Center Miami FL AL092026
400 PM CDT Wed Oct 07 2026
...EXAMPLE HEADLINE...
LOCATION...22.7N 92.9W
MAXIMUM SUSTAINED WINDS...65 MPH
PRESENT MOVEMENT...ENE OR 65 DEGREES AT 8 MPH
MINIMUM CENTRAL PRESSURE...994 MB
SUMMARY OF WATCHES AND WARNINGS IN EFFECT:
A Hurricane Watch is in effect for...
* Public coastline segment
DISCUSSION AND OUTLOOK
"""
        snapshot = build_snapshot("AL092026", "AT4", {"TCP": tcp}, "Connected")
        self.assertEqual(snapshot["summary"]["issued"], "400 PM CDT Wed Oct 07 2026")
        self.assertEqual(snapshot["alerts"][0]["type"], "Hurricane Watch")
        self.assertTrue(snapshot["sources"]["nhc_cone"].endswith("graphics_at4+shtml/071753.shtml?wwCone#contents"))
        self.assertTrue(snapshot["sources"]["nhc_arrival_time"].endswith("?mltoa34#contents"))
        self.assertNotIn("property", snapshot)
        self.assertNotIn("latest_update", snapshot)
        self.assertLessEqual(set(snapshot), PUBLIC_KEYS)


if __name__ == "__main__":
    unittest.main()
