#!/usr/bin/env python3
"""Fetch public NHC products and write a public-safe dashboard.json."""
import gzip
import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

PRODUCTS = {
    "TCP": "Public Advisory",
    "TCU": "Tropical Cyclone Update",
    "TCM": "Forecast Advisory",
    "TCD": "Forecast Discussion",
    "PWS": "Wind Speed Probabilities",
}
WMO_RE = re.compile(r"^\s*([A-Z]{4}\d{2} [A-Z]{4} \d{6})\s*$", re.M)
STORM_ID_RE = re.compile(r"\b(AL|EP|CP)\d{6}\b")
USER_AGENT = "Storm Desk NHC dashboard (github.com/djblackjr/storm-desk)"
TZ_OFFSETS = {"AST": -4, "EDT": -4, "EST": -5, "CDT": -5, "CST": -6, "MDT": -6, "MST": -7,
              "PDT": -7, "PST": -8, "HST": -10, "UTC": 0, "GMT": 0}
GIS_BASE = "https://mapservices.weather.noaa.gov/tropical/rest/services/tropical/NHC_tropical_weather/MapServer"
# layers.json key -> (NOAA layer name after the bin prefix, feature properties worth keeping)
GIS_LAYERS = {
    "cone": ("Forecast Cone", ("advisnum",)),
    "watch_warning": ("Watch-Warning", ("tcww",)),
    "past_track": ("Past Track", ("stormtype",)),
    "wind_radii": ("Forecast Wind Radii", ("radii", "tau", "validtime")),
    "arrival": ("Earliest Reasonable Arrival Time", ("arrival_time",)),
}
# ATCF guidance identifiers -> display names. These are model output, not the official forecast.
MODEL_NAMES = {
    "AVNI": "GFS", "AEMI": "GFS ensemble mean", "HFAI": "HAFS-A", "HFBI": "HAFS-B", "HWFI": "HWRF",
    "HMNI": "HMON", "CMCI": "Canadian", "UKXI": "UKMET", "NVGI": "NAVGEM", "CTCI": "COAMPS-TC",
    "TVCN": "Track consensus (TVCN)", "HCCA": "Corrected consensus (HCCA)",
}
BUOY_NAMES = {"42039": "Pensacola buoy, 115 nm SSE", "42012": "Orange Beach buoy, 44 nm SE of Mobile"}


def extract_pre(page: str) -> str:
    match = re.search(r"<pre[^>]*>(.*?)</pre>", page, re.S | re.I)
    if not match:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", match.group(1))).strip()


def product_id(text: str) -> str | None:
    match = WMO_RE.search(text[:400])
    return match.group(1) if match else None


def belongs_to_storm(text: str, storm_id: str) -> bool:
    ids = {match.group(0) for match in STORM_ID_RE.finditer(text)}
    return storm_id in ids or not ids


def fetch_products(storm_id: str, nhc_bin: str) -> dict[str, str]:
    products = {}
    for code in PRODUCTS:
        url = f"https://www.nhc.noaa.gov/text/MIA{code}{nhc_bin}.shtml"
        try:
            response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=25)
            if response.status_code != 200:
                continue
            text = extract_pre(response.text)
        except requests.RequestException as error:
            print(f"fetch {code} failed: {error}")
            continue
        if text and product_id(text) and belongs_to_storm(text, storm_id):
            products[code] = text
    return products


def summary_fields(tcp: str) -> dict:
    summary = {}
    title = re.search(r"^(.*(?:Advisory Number|Update)\s*\S*)\s*$", tcp, re.M)
    if title:
        summary["title"] = re.sub(r"\s+", " ", title.group(1)).strip()
    issued = re.search(r"^(\d{3,4} (?:AM|PM) [A-Z]{3} \w{3} \w{3} \d{2} \d{4})\s*$", tcp, re.M)
    if issued:
        summary["issued"] = issued.group(1)
    headline = re.search(r"^\.\.\.(.+?)\.\.\.\s*$", tcp, re.M | re.S)
    if headline:
        summary["headline"] = re.sub(r"\s+", " ", headline.group(1)).strip()
    for key, label in (("location", "LOCATION"), ("winds", "MAXIMUM SUSTAINED WINDS"),
                       ("movement", "PRESENT MOVEMENT"), ("pressure", "MINIMUM CENTRAL PRESSURE")):
        found = re.search(rf"^{label}\.\.\.(.+)$", tcp, re.M)
        if found:
            summary[key] = found.group(1).split("...")[0].strip() if key != "location" else found.group(1).strip()
    return summary


def parse_watches_and_warnings(tcp: str) -> list[dict]:
    """Extract NHC's explicitly listed watches/warnings; never infer local orders."""
    section = re.search(
        r"SUMMARY OF WATCHES AND WARNINGS IN EFFECT:\s*(.*?)\s*DISCUSSION AND OUTLOOK",
        tcp,
        re.S | re.I,
    )
    if not section:
        return []

    alerts = []
    current = None
    in_area = False
    for line in section.group(1).splitlines():
        heading = re.match(r"\s*A (.+?) is in effect for\.\.\.\s*$", line, re.I)
        if heading:
            current = {"type": heading.group(1).strip(), "areas": []}
            alerts.append(current)
            in_area = False
        elif current and re.match(r"\s*\*\s+", line):
            area = re.sub(r"^\s*\*\s+", "", line).strip()
            if area:
                current["areas"].append(area)
                in_area = True
        elif not line.strip():
            in_area = False
        elif current and in_area:
            # NHC wraps long area names onto the next line.
            current["areas"][-1] += " " + line.strip()
        else:
            current = None
    return [alert for alert in alerts if alert["areas"]]


def watch_warning_status(tcp: str, alerts: list[dict]) -> str:
    """parsed, none_in_effect (NHC says so explicitly), or unknown. Unknown is never 'no alerts'."""
    if alerts:
        return "parsed"
    if re.search(r"There are no coastal watches or warnings in effect", tcp, re.I):
        return "none_in_effect"
    return "unknown"


def parse_clock(clock: str, meridiem: str, zone: str, day: datetime) -> datetime | None:
    """Turn NHC local clock text such as '400 PM CDT' on a given date into UTC."""
    if zone not in TZ_OFFSETS or not clock.isdigit():
        return None
    hour, minute = int(clock[:-2]) % 12, int(clock[-2:])
    if meridiem == "PM":
        hour += 12
    local = day.replace(hour=hour, minute=minute, second=0, microsecond=0, tzinfo=None)
    return (local - timedelta(hours=TZ_OFFSETS[zone])).replace(tzinfo=timezone.utc)


def issued_utc(issued: str) -> datetime | None:
    match = re.match(r"(\d{3,4}) (AM|PM) ([A-Z]{3}) \w{3} (\w{3}) (\d{2}) (\d{4})$", issued or "")
    if not match:
        return None
    try:
        day = datetime.strptime(" ".join(match.group(4, 5, 6)), "%b %d %Y")
    except ValueError:
        return None
    return parse_clock(match.group(1), match.group(2), match.group(3), day)


def apply_update(summary: dict, tcu: str) -> dict:
    """Fold in a Tropical Cyclone Update, which NHC issues between advisories when a storm changes quickly."""
    update = summary_fields(tcu)
    update_at, advisory_at = issued_utc(update.get("issued", "")), issued_utc(summary.get("issued", ""))
    if not update_at or not advisory_at or update_at <= advisory_at:
        return summary
    merged = dict(summary)
    for key in ("headline", "location", "winds", "movement", "pressure"):
        if key in update:
            merged[key] = update[key]
    # The update carries the storm's current classification; the advisory title keeps its number.
    name = re.sub(r"\s+Tropical Cyclone Update.*$", "", update.get("title", ""))
    number = re.search(r"(?:Intermediate |Special )?Advisory Number.*$", summary.get("title", ""))
    if name and number:
        merged["title"] = f"{name} {number.group(0)}"
    merged["update"] = {"issued": update["issued"], "issued_utc": update_at.isoformat()}
    return merged


def advisory_timing(tcp: str, issued: str) -> dict:
    """Issue time in UTC plus NHC's own statement of when the next advisory is due."""
    timing = {}
    issued_at = issued_utc(issued)
    if issued_at:
        timing["issued_utc"] = issued_at.isoformat()
    number = re.search(r"Advisory Number\s+(\S+)", tcp)
    if number:
        timing["number"] = number.group(1)
    upcoming = []
    for kind, clock, meridiem, zone in re.findall(
            r"Next (intermediate|complete) advisory at (\d{3,4}) (AM|PM) ([A-Z]{3})", tcp, re.I):
        entry = {"kind": kind.lower(), "text": f"{clock[:-2]}:{clock[-2:]} {meridiem} {zone}"}
        if issued_at and zone in TZ_OFFSETS:
            local_day = issued_at + timedelta(hours=TZ_OFFSETS[zone])
            due = parse_clock(clock, meridiem, zone, local_day)
            if due and due <= issued_at:
                due += timedelta(days=1)
            if due:
                entry["utc"] = due.isoformat()
        upcoming.append(entry)
    if upcoming:
        timing["next"] = upcoming
    return timing


def parse_surge_forecast(tcp: str) -> list[dict]:
    """NHC's peak storm surge ranges by coastline segment, exactly as listed in the advisory."""
    section = re.search(r"^STORM SURGE:(.*?)(?=^[A-Z][A-Z ]+:|\Z)", tcp, re.S | re.M)
    if not section:
        return []
    ranges = []
    for area, low, high in re.findall(r"^(.+?)\.\.\.(\d+)-(\d+) ft\s*$", section.group(1), re.M):
        ranges.append({"area": area.strip(), "low_ft": int(low), "high_ft": int(high)})
    return ranges


def parse_key_messages(tcd: str) -> list[str]:
    section = re.search(r"^Key Messages:\s*(.*?)(?=^FORECAST POSITIONS AND MAX WINDS|^\$\$|\Z)", tcd, re.S | re.M | re.I)
    if not section:
        return []
    messages = re.split(r"^\s*\d+\.\s+", section.group(1), flags=re.M)
    return [re.sub(r"\s+", " ", message).strip() for message in messages if message.strip()]


def track_time_iso(valid: str, reference: datetime | None) -> str | None:
    """Expand an NHC 'DD/HHMMZ' stamp using the advisory issue time for month and year."""
    match = re.match(r"(\d{2})/(\d{2})(\d{2})Z$", valid or "")
    if not match or not reference:
        return None
    day, hour, minute = (int(part) for part in match.groups())
    year, month = reference.year, reference.month
    for _ in range(2):
        try:
            moment = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        except ValueError:
            moment = None
        if moment and moment >= reference - timedelta(days=3):
            return moment.isoformat()
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return None


def coordinate_pair(text: str) -> tuple[float, float] | None:
    match = re.search(r"(\d{1,2}(?:\.\d+)?)([NS])\s+(\d{1,3}(?:\.\d+)?)([EW])", text)
    if not match:
        return None
    lat = float(match.group(1)) * (-1 if match.group(2) == "S" else 1)
    lon = float(match.group(3)) * (-1 if match.group(4) == "W" else 1)
    return lat, lon


def forecast_track_points(tcm: str) -> list[dict]:
    current_re = re.compile(
        r"^\s*TROPICAL (?:DEPRESSION|STORM|HURRICANE|CYCLONE) CENTER LOCATED NEAR\s+"
        r"(\d{1,2}(?:\.\d+)?)([NS])\s+(\d{1,3}(?:\.\d+)?)([EW]) AT (\d{2}/\d{4}Z)", re.M
    )
    valid_re = re.compile(
        r"^\s*(FORECAST|OUTLOOK) VALID\s+(\d{2}/\d{4}Z)\s+"
        r"(\d{1,2}(?:\.\d+)?)([NS])\s+(\d{1,3}(?:\.\d+)?)([EW])", re.M
    )
    entries = []
    for match in current_re.finditer(tcm):
        entries.append((match.start(), match.end(), "Current", match.group(5),
                        match.group(1), match.group(2), match.group(3), match.group(4)))
    for match in valid_re.finditer(tcm):
        label = "Forecast" if match.group(1) == "FORECAST" else "Outlook"
        entries.append((match.start(), match.end(), label, match.group(2),
                        match.group(3), match.group(4), match.group(5), match.group(6)))
    entries.sort(key=lambda entry: entry[0])
    points = []
    for index, entry in enumerate(entries):
        _, end, label, valid, lat, lat_hemi, lon, lon_hemi = entry
        next_start = entries[index + 1][0] if index + 1 < len(entries) else len(tcm)
        wind = re.search(r"MAX (?:SUSTAINED )?WIND(?:S)?\s+(\d+)\s+KT", tcm[end:next_start])
        wind_kt = int(wind.group(1)) if wind else None
        points.append({
            "label": label,
            "time_utc": valid,
            "lat": float(lat) * (-1 if lat_hemi == "S" else 1),
            "lon": float(lon) * (-1 if lon_hemi == "W" else 1),
            "wind_kt": wind_kt,
            "wind_mph": round(wind_kt * 1.15078) if wind_kt is not None else None,
        })
    return points


def pws_cumulative(pws: str, location: str) -> dict[str, int]:
    probabilities = {}
    for line in pws.splitlines():
        if not line.startswith(location):
            continue
        fields = line[len(location):].split()
        if not fields or fields[0] not in ("34", "50", "64"):
            continue
        cumulative = re.findall(r"\(\s*(\d+|X)\)", line)
        if cumulative:
            probabilities[fields[0]] = 0 if cumulative[-1] == "X" else int(cumulative[-1])
    return probabilities


def fetch_json(url: str):
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if response.status_code == 200:
            return response.json()
        print(f"fetch {url} returned {response.status_code}")
    except (requests.RequestException, ValueError) as error:
        print(f"fetch {url} failed: {error}")
    return None


def storm_features(collection, storm_id: str, keep: tuple[str, ...]) -> list[dict]:
    """Keep only features NOAA tags with this storm, and only the properties the map uses."""
    features = []
    for feature in (collection or {}).get("features") or []:
        properties = feature.get("properties") or {}
        source = f"{properties.get('idp_source', '')} {properties.get('stormid', '')}".lower()
        if storm_id.lower() not in source or not feature.get("geometry"):
            continue
        features.append({
            "type": "Feature",
            "geometry": feature["geometry"],
            "properties": {key: properties[key] for key in keep if properties.get(key) is not None},
        })
    return features


def fetch_gis_layers(storm_id: str, nhc_bin: str) -> dict:
    directory = fetch_json(f"{GIS_BASE}?f=json") or {}
    ids = {layer.get("name"): layer.get("id") for layer in directory.get("layers") or []}
    layers = {}
    for key, (suffix, keep) in GIS_LAYERS.items():
        layer_id = ids.get(f"{nhc_bin} {suffix}")
        if layer_id is None:
            continue
        collection = fetch_json(f"{GIS_BASE}/{layer_id}/query?where=1%3D1&outFields=*&geometryPrecision=3&f=geojson")
        features = storm_features(collection, storm_id, keep)
        if features:
            layers[key] = {"type": "FeatureCollection", "features": features}
    return layers


def parse_model_tracks(adeck: str) -> dict | None:
    """Latest run of well-known track guidance from an ATCF a-deck, as GeoJSON lines."""
    rows = [[cell.strip() for cell in line.split(",")] for line in adeck.splitlines() if line.count(",") > 7]
    rows = [row for row in rows if row[4] in MODEL_NAMES]
    if not rows:
        return None
    latest = max(row[2] for row in rows)
    tracks: dict[str, dict[int, list[float]]] = {}
    for row in rows:
        if row[2] != latest:
            continue
        lat, lon = re.match(r"(\d+)([NS])$", row[6]), re.match(r"(\d+)([EW])$", row[7])
        if not lat or not lon or not int(lat.group(1)) or not row[5].lstrip("-").isdigit():
            continue
        point = [int(lon.group(1)) / 10 * (-1 if lon.group(2) == "W" else 1),
                 int(lat.group(1)) / 10 * (-1 if lat.group(2) == "S" else 1)]
        tracks.setdefault(row[4], {})[int(row[5])] = point
    features = [{
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [points[tau] for tau in sorted(points)]},
        "properties": {"model": MODEL_NAMES[tech], "hours": max(points)},
    } for tech, points in sorted(tracks.items()) if len(points) > 1]
    if not features:
        return None
    return {"type": "FeatureCollection", "run": latest, "features": features}


def fetch_model_tracks(storm_id: str) -> dict | None:
    url = f"https://ftp.nhc.noaa.gov/atcf/aid_public/a{storm_id.lower()}.dat.gz"
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
        if response.status_code != 200:
            return None
        return parse_model_tracks(gzip.decompress(response.content).decode("ascii", "replace"))
    except (requests.RequestException, OSError) as error:
        print(f"fetch model guidance failed: {error}")
        return None


def parse_buoy(text: str) -> dict | None:
    """Latest observation from an NDBC realtime2 standard meteorological file."""
    lines = [line.split() for line in text.splitlines() if line.strip()]
    if len(lines) < 3 or not lines[0][0].startswith("#"):
        return None
    names = [name.lstrip("#") for name in lines[0]]
    row = dict(zip(names, lines[2]))

    def number(key: str, scale: float = 1.0) -> float | None:
        # Waves report less often than wind, so look back up to about two hours for each value.
        for recent in lines[2:14]:
            try:
                return round(float(dict(zip(names, recent))[key]) * scale, 1)
            except (KeyError, ValueError):
                continue
        return None
    try:
        observed = datetime(*(int(row[key]) for key in ("YY", "MM", "DD", "hh", "mm")), tzinfo=timezone.utc)
    except (KeyError, ValueError):
        return None
    return {
        "observed_utc": observed.isoformat(),
        "wind_mph": number("WSPD", 2.23694),
        "gust_mph": number("GST", 2.23694),
        "wind_dir_deg": number("WDIR"),
        "wave_ft": number("WVHT", 3.28084),
        "pressure_mb": number("PRES"),
    }


def fetch_buoys(station_ids: list[str]) -> list[dict]:
    buoys = []
    for station in station_ids:
        try:
            response = requests.get(f"https://www.ndbc.noaa.gov/data/realtime2/{station}.txt",
                                    headers={"User-Agent": USER_AGENT}, timeout=25)
            reading = parse_buoy(response.text) if response.status_code == 200 else None
        except requests.RequestException as error:
            print(f"fetch buoy {station} failed: {error}")
            reading = None
        if reading:
            buoys.append({"id": station, "name": BUOY_NAMES.get(station, f"NDBC buoy {station}"), **reading})
    return buoys


def active_storms(current: dict | None) -> list[dict] | None:
    """NHC's own list of active systems; None means the list could not be read."""
    if not current or "activeStorms" not in current:
        return None
    storms = []
    for storm in current["activeStorms"]:
        try:
            wind_kt = int(storm.get("intensity"))
        except (TypeError, ValueError):
            wind_kt = None
        storms.append({
            "id": str(storm.get("id", "")).upper(),
            "name": storm.get("name"),
            "classification": storm.get("classification"),
            "wind_mph": round(wind_kt * 1.15078) if wind_kt is not None else None,
            "lat": storm.get("latitudeNumeric"),
            "lon": storm.get("longitudeNumeric"),
        })
    return storms


def tcp_id(snapshot: dict | None) -> str | None:
    for product in (snapshot or {}).get("products") or []:
        if product.get("code") == "TCP":
            return product.get("id")
    return None


def advisory_changes(previous: dict | None, snapshot: dict) -> dict | None:
    """What moved since the previously published advisory. None when there is nothing to compare."""
    before, after = tcp_id(previous), tcp_id(snapshot)
    if not before or not after or previous.get("storm_id") != snapshot["storm_id"]:
        return None
    if before == after:
        return previous.get("changes")
    items = []
    for key, label in (("winds", "Max winds"), ("pressure", "Pressure"), ("movement", "Movement")):
        old, new = previous.get("summary", {}).get(key), snapshot["summary"].get(key)
        if old and new and old != new:
            items.append({"label": label, "from": old, "to": new})

    def flat(alerts) -> set[str]:
        return {f"{alert['type']}: {area}" for alert in alerts or [] for area in alert.get("areas", [])}
    old_alerts, new_alerts = flat(previous.get("alerts")), flat(snapshot["alerts"])
    return {
        "since": previous.get("summary", {}).get("title") or before,
        "items": items,
        "alerts_added": sorted(new_alerts - old_alerts),
        "alerts_removed": sorted(old_alerts - new_alerts),
    }


def build_snapshot(storm_id: str, nhc_bin: str, products: dict[str, str], status: str,
                   previous: dict | None = None, extras: dict | None = None) -> dict:
    tcp = products.get("TCP", "")
    summary = summary_fields(tcp)
    advisory_id = product_id(tcp) if tcp else None
    graphic_stamp = advisory_id.rsplit(" ", 1)[-1] if advisory_id else ""
    graphics_path = (
        f"https://www.nhc.noaa.gov/refresh/graphics_{nhc_bin.lower()}+shtml/{graphic_stamp}.shtml"
        if graphic_stamp
        else f"https://www.nhc.noaa.gov/graphics_{nhc_bin.lower()}.shtml"
    )
    # NHC serves each graphic as a plain image; the app shows these in its own viewer.
    images = {}
    if graphic_stamp and storm_id[:2] in ("AL", "EP", "CP"):
        folder = f"https://www.nhc.noaa.gov/storm_graphics/{'AT' if storm_id[:2] == 'AL' else storm_id[:2]}{storm_id[2:4]}"
        for key, name in (("nhc_cone", "5day_cone"), ("nhc_arrival_time", "earliest_reasonable_toa_34"),
                          ("nhc_peak_surge", "peak_surge")):
            images[key] = f"{folder}/refresh/{storm_id}_{name}+png/{graphic_stamp}_{name}.png"
        images["nhc_wind_probabilities"] = f"{folder}/refresh/{storm_id}_wind_probs_34_F120+png/{graphic_stamp}.png"
        images["nhc_key_messages"] = f"{folder}/{storm_id}_key_messages.png"
    advisory = advisory_timing(tcp, summary.get("issued", ""))
    reference = datetime.fromisoformat(advisory["issued_utc"]) if "issued_utc" in advisory else None
    track = forecast_track_points(products.get("TCM", ""))
    for point in track:
        point["time_iso"] = track_time_iso(point["time_utc"], reference)
    alerts = parse_watches_and_warnings(tcp)
    summary = apply_update(summary, products.get("TCU", ""))
    snapshot = {
        "last_check": datetime.now(timezone.utc).isoformat(),
        "fetch_status": status,
        "storm_id": storm_id,
        "storm_name": summary.get("title", storm_id),
        "summary": summary,
        "advisory": advisory,
        "alerts": alerts,
        "alerts_status": watch_warning_status(tcp, alerts),
        "key_messages": parse_key_messages(products.get("TCD", "")),
        "surge_forecast": parse_surge_forecast(tcp),
        "sources": {
            "nhc_home": "https://www.nhc.noaa.gov/",
            "nhc_advisory": "https://www.nhc.noaa.gov/text/MIATCP" + nhc_bin + ".shtml",
            "nhc_cone": f"{graphics_path}?wwCone#contents",
            "nhc_key_messages": f"{graphics_path}?key_messages#contents",
            "nhc_wind_probabilities": f"{graphics_path}?tswind120#contents",
            "nhc_arrival_time": f"{graphics_path}?mltoa34#contents",
            "nhc_peak_surge": f"{graphics_path}?peakSurge#contents",
            "images": images,
        },
        "track": track,
        "wind_probabilities": {
            location: pws_cumulative(products.get("PWS", ""), location)
            for location in ("DESTIN EXEC AP", "PANAMA CITY FL")
        },
        "products": [
            {"code": code, "name": PRODUCTS[code], "id": product_id(text)}
            for code, text in products.items()
        ],
        "events": [],
    }
    position = coordinate_pair(summary.get("location", ""))
    if position:
        snapshot["position"] = {"lat": position[0], "lon": position[1]}
    snapshot.update(extras or {})
    storms = snapshot.get("active_storms")
    if storms is not None:
        snapshot["storm_active"] = any(storm["id"] == storm_id.upper() for storm in storms)
    changes = advisory_changes(previous, snapshot)
    if changes:
        snapshot["changes"] = changes
    return snapshot


def main() -> None:
    storm_id = os.environ.get("STORM_ID", "AL092026")
    nhc_bin = os.environ.get("NHC_BIN", "AT4")
    destination = Path(os.environ.get("DASHBOARD_JSON", "dashboard.json"))
    layers_path = destination.with_name("layers.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    previous_url = os.environ.get("PREVIOUS_SNAPSHOT_URL", "")
    previous = fetch_json(previous_url) if previous_url else None
    previous_layers = fetch_json(previous_url.rsplit("/", 1)[0] + "/layers.json") if previous_url else None

    products = fetch_products(storm_id, nhc_bin)
    if "TCP" not in products and tcp_id(previous) and previous.get("storm_id") == storm_id:
        # Never replace good data with an empty page: republish the last snapshot and say so.
        previous["fetch_status"] = "NHC unreachable - showing last good snapshot"
        destination.write_text(json.dumps(previous, indent=2) + "\n")
        layers_path.write_text(json.dumps(previous_layers or {}) + "\n")
        print("NHC public advisory unavailable; republished the previous snapshot")
        return

    layers = fetch_gis_layers(storm_id, nhc_bin)
    models = fetch_model_tracks(storm_id)
    if models:
        layers["models"] = models
    advisory_id = product_id(products.get("TCP", "")) or ""
    if previous_layers and previous_layers.get("advisory_id") == advisory_id:
        for key, value in previous_layers.items():
            layers.setdefault(key, value)
    layers["advisory_id"] = advisory_id
    extras = {
        "active_storms": active_storms(fetch_json("https://www.nhc.noaa.gov/CurrentStorms.json")),
        "buoys": fetch_buoys([item for item in os.environ.get("BUOYS", "42039,42012").split(",") if item]),
        "local_zone": os.environ.get("NWS_ZONE", "FLZ108"),
        "layers_version": f"{advisory_id}|{(models or {}).get('run', '')}|{','.join(sorted(layers))}",
    }
    status = "Connected" if products else "No current NHC products found"
    snapshot = build_snapshot(storm_id, nhc_bin, products, status, previous, extras)
    destination.write_text(json.dumps(snapshot, indent=2) + "\n")
    layers_path.write_text(json.dumps(layers, separators=(",", ":")) + "\n")
    print(f"Published {len(products)} public NHC products ({status}); map layers: {', '.join(sorted(layers))}")


if __name__ == "__main__":
    main()
