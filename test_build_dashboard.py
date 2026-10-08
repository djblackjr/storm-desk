import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from build_dashboard import (
    advisory_changes, advisory_timing, build_snapshot, parse_buoy, parse_key_messages, parse_model_tracks,
    parse_surge_forecast, parse_watches_and_warnings, storm_features, track_time_iso, watch_warning_status,
)

FIXTURES = Path(__file__).parent / "tests" / "fixtures"
PUBLIC_KEYS = {
    "last_check", "fetch_status", "storm_id", "storm_name", "summary", "alerts", "sources",
    "track", "wind_probabilities", "products", "events", "position", "advisory", "alerts_status",
    "key_messages", "surge_forecast", "active_storms", "storm_active", "buoys", "local_zone",
    "layers_version", "changes",
}


def fixture(code: str) -> str:
    return (FIXTURES / f"al092026_adv5_{code}.txt").read_text()


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
        self.assertEqual(watch_warning_status("DISCUSSION AND OUTLOOK\ntext", []), "unknown")

    def test_explicit_nhc_statement_of_no_watches_is_distinct_from_unknown(self):
        product = """SUMMARY OF WATCHES AND WARNINGS IN EFFECT:

There are no coastal watches or warnings in effect.

DISCUSSION AND OUTLOOK
"""
        self.assertEqual(watch_warning_status(product, parse_watches_and_warnings(product)), "none_in_effect")

    def test_wrapped_area_lines_are_joined_and_definitions_ignored(self):
        product = """SUMMARY OF WATCHES AND WARNINGS IN EFFECT:

A Tropical Storm Warning is in effect for...
* East of the Mouth of the Mississippi River to the
Okaloosa/Walton County line
* Lake Pontchartrain

A Tropical Storm Warning means that tropical storm conditions are
expected somewhere within the warning area.

DISCUSSION AND OUTLOOK
"""
        self.assertEqual(parse_watches_and_warnings(product), [{
            "type": "Tropical Storm Warning",
            "areas": ["East of the Mouth of the Mississippi River to the Okaloosa/Walton County line",
                      "Lake Pontchartrain"],
        }])

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
        self.assertEqual(snapshot["sources"]["images"]["nhc_cone"],
                         "https://www.nhc.noaa.gov/storm_graphics/AT09/refresh/AL092026_5day_cone+png/071753_5day_cone.png")
        self.assertEqual(snapshot["sources"]["images"]["nhc_key_messages"],
                         "https://www.nhc.noaa.gov/storm_graphics/AT09/AL092026_key_messages.png")
        self.assertNotIn("property", snapshot)
        self.assertNotIn("latest_update", snapshot)
        self.assertLessEqual(set(snapshot), PUBLIC_KEYS)


class RealAdvisoryTests(unittest.TestCase):
    """Parsers run against NHC text saved verbatim from Isaias advisory 5."""

    def setUp(self):
        self.products = {code: fixture(code) for code in ("TCP", "TCM", "TCD", "PWS")}
        self.snapshot = build_snapshot("AL092026", "AT4", self.products, "Connected")

    def test_watches_from_real_public_advisory(self):
        self.assertEqual(self.snapshot["alerts_status"], "parsed")
        self.assertEqual([alert["type"] for alert in self.snapshot["alerts"]],
                         ["Storm Surge Watch", "Hurricane Watch", "Tropical Storm Watch"])
        self.assertEqual(self.snapshot["alerts"][2]["areas"], [
            "Jefferson/Plaquemines Parish line to west of Bay St. Louis",
            "East of Indian Pass to Aucilla River",
        ])

    def test_issue_time_and_next_advisory_in_utc(self):
        advisory = self.snapshot["advisory"]
        self.assertEqual(advisory["number"], "5")
        self.assertEqual(advisory["issued_utc"], "2026-10-07T21:00:00+00:00")
        self.assertEqual([(item["kind"], item["utc"]) for item in advisory["next"]], [
            ("intermediate", "2026-10-08T00:00:00+00:00"),
            ("complete", "2026-10-08T03:00:00+00:00"),
        ])

    def test_next_advisory_after_midnight_rolls_to_next_day(self):
        timing = advisory_timing("Advisory Number 6\nNext complete advisory at 400 AM CDT.", "1000 PM CDT Wed Oct 07 2026")
        self.assertEqual(timing["next"][0]["utc"], "2026-10-08T09:00:00+00:00")

    def test_surge_ranges_and_key_messages(self):
        self.assertIn({"area": "Ocean Springs, MS to Indian Pass, FL", "low_ft": 5, "high_ft": 7},
                      self.snapshot["surge_forecast"])
        self.assertEqual(len(self.snapshot["surge_forecast"]), 6)
        self.assertGreaterEqual(len(self.snapshot["key_messages"]), 2)
        self.assertTrue(self.snapshot["key_messages"][0].startswith("Isaias is expected to strengthen"))
        self.assertFalse(any("\n" in message for message in self.snapshot["key_messages"]))

    def test_track_points_carry_full_timestamps(self):
        track = self.snapshot["track"]
        self.assertEqual(track[0]["time_iso"], "2026-10-07T21:00:00+00:00")
        self.assertEqual(track[-1]["time_iso"], "2026-10-11T18:00:00+00:00")
        self.assertEqual(self.snapshot["wind_probabilities"]["DESTIN EXEC AP"], {"34": 56, "50": 25, "64": 8})

    def test_track_time_rolls_into_next_month(self):
        reference = datetime(2026, 10, 30, 21, tzinfo=timezone.utc)
        self.assertEqual(track_time_iso("02/0600Z", reference), "2026-11-02T06:00:00+00:00")

    def test_newer_tropical_cyclone_update_supersedes_advisory_numbers(self):
        update = (FIXTURES / "al092026_adv6_TCU.txt").read_text()
        snapshot = build_snapshot("AL092026", "AT4", {**self.products, "TCU": update}, "Connected")
        self.assertEqual(snapshot["storm_name"], "Hurricane Isaias Advisory Number 5")
        self.assertEqual(snapshot["summary"]["winds"], "75 MPH")
        self.assertEqual(snapshot["summary"]["pressure"], "982 MB")
        self.assertEqual(snapshot["summary"]["headline"], "ISAIAS BECOMES A HURRICANE")
        self.assertEqual(snapshot["summary"]["update"]["issued_utc"], "2026-10-08T03:30:00+00:00")
        self.assertEqual(snapshot["summary"]["issued"], self.snapshot["summary"]["issued"])
        self.assertEqual(snapshot["position"], {"lat": 22.9, "lon": -91.9})

    def test_update_older_than_the_advisory_is_ignored(self):
        update = (FIXTURES / "al092026_adv6_TCU.txt").read_text().replace("Oct 07 2026", "Oct 06 2026")
        snapshot = build_snapshot("AL092026", "AT4", {**self.products, "TCU": update}, "Connected")
        self.assertEqual(snapshot["summary"], self.snapshot["summary"])
        self.assertEqual(snapshot["storm_name"], self.snapshot["storm_name"])

    def test_snapshot_stays_within_public_allowlist_and_is_serializable(self):
        self.assertLessEqual(set(self.snapshot), PUBLIC_KEYS)
        text = json.dumps(self.snapshot).lower()
        for word in ("property", "evacuation_decision", "home_lat", "ntfy"):
            self.assertNotIn(word, text)


class ChangeTrackingTests(unittest.TestCase):
    def snapshot(self, product_id, winds, alerts):
        return {"storm_id": "AL092026", "products": [{"code": "TCP", "id": product_id}],
                "summary": {"title": f"Advisory {product_id[-6:]}", "winds": winds, "pressure": "994 MB"},
                "alerts": alerts}

    def test_reports_wind_and_alert_changes_between_advisories(self):
        before = self.snapshot("WTNT34 KNHC 071500", "50 MPH", [{"type": "Hurricane Watch", "areas": ["A to B"]}])
        after = self.snapshot("WTNT34 KNHC 072049", "65 MPH", [{"type": "Hurricane Warning", "areas": ["A to B"]}])
        changes = advisory_changes(before, after)
        self.assertEqual(changes["items"], [{"label": "Max winds", "from": "50 MPH", "to": "65 MPH"}])
        self.assertEqual(changes["alerts_added"], ["Hurricane Warning: A to B"])
        self.assertEqual(changes["alerts_removed"], ["Hurricane Watch: A to B"])

    def test_same_advisory_keeps_previous_changes_and_missing_history_is_none(self):
        before = self.snapshot("WTNT34 KNHC 072049", "65 MPH", [])
        before["changes"] = {"since": "Advisory 4", "items": []}
        self.assertEqual(advisory_changes(before, self.snapshot("WTNT34 KNHC 072049", "65 MPH", [])), before["changes"])
        self.assertIsNone(advisory_changes(None, self.snapshot("WTNT34 KNHC 072049", "65 MPH", [])))


class SupplementalFeedTests(unittest.TestCase):
    def test_gis_features_for_other_storms_are_dropped(self):
        collection = {"features": [
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"idp_source": "al092026-005_ww_wwlin", "tcww": "HWA", "objectid": 1}},
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"idp_source": "al082026-031_ww_wwlin", "tcww": "TWR"}},
        ]}
        self.assertEqual(storm_features(collection, "AL092026", ("tcww",)),
                         [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {"tcww": "HWA"}}])

    def test_model_tracks_use_latest_run_and_known_models_only(self):
        adeck = "\n".join([
            "AL, 09, 2026100712, 03, AVNI,   0, 220N,  940W,  45, 1000, XX",
            "AL, 09, 2026100718, 03, AVNI,   0, 227N,  929W,  55,  994, XX,  34, NEQ",
            "AL, 09, 2026100718, 03, AVNI,   0, 227N,  929W,  55,  994, XX,  50, NEQ",
            "AL, 09, 2026100718, 03, AVNI,  12, 231N,  918W,  70,  985, XX",
            "AL, 09, 2026100718, 03, XTRP,  12, 240N,  910W,  55,    0, XX",
        ])
        tracks = parse_model_tracks(adeck)
        self.assertEqual(tracks["run"], "2026100718")
        self.assertEqual(len(tracks["features"]), 1)
        self.assertEqual(tracks["features"][0]["properties"], {"model": "GFS", "hours": 12})
        self.assertEqual(tracks["features"][0]["geometry"]["coordinates"], [[-92.9, 22.7], [-91.8, 23.1]])

    def test_buoy_reading_converts_units_and_skips_missing_values(self):
        text = """#YY  MM DD hh mm WDIR WSPD GST  WVHT   DPD   APD MWD   PRES  ATMP  WTMP  DEWP  VIS PTDY  TIDE
#yr  mo dy hr mn degT m/s  m/s     m   sec   sec degT   hPa  degC  degC  degC  nmi  hPa    ft
2026 10 07 22 00  80 11.0 13.0    MM    MM    MM  MM 1008.6  26.4    MM  25.5   MM -0.7    MM
2026 10 07 21 40  80 10.0 12.0   2.0     8   6.0 110 1008.9  26.4    MM  25.5   MM   MM    MM
"""
        reading = parse_buoy(text)
        self.assertEqual(reading["observed_utc"], "2026-10-07T22:00:00+00:00")
        self.assertEqual((reading["wind_mph"], reading["gust_mph"], reading["wave_ft"], reading["pressure_mb"]),
                         (24.6, 29.1, 6.6, 1008.6))
        self.assertIsNone(parse_buoy("not a buoy file"))


if __name__ == "__main__":
    unittest.main()
