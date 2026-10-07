#!/usr/bin/env python3
"""Fetch public NHC products and write a public-safe dashboard.json."""
import html
import json
import os
import re
from datetime import datetime, timezone
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
    for line in section.group(1).splitlines():
        heading = re.match(r"\s*A (.+?) is in effect for\.\.\.\s*$", line, re.I)
        if heading:
            current = {"type": heading.group(1).strip(), "areas": []}
            alerts.append(current)
        elif current and re.match(r"\s*\*\s+", line):
            area = re.sub(r"^\s*\*\s+", "", line).strip()
            if area:
                current["areas"].append(area)
        elif line.strip() and not line.lstrip().startswith("*"):
            current = None
    return [alert for alert in alerts if alert["areas"]]


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


def build_snapshot(storm_id: str, nhc_bin: str, products: dict[str, str], status: str) -> dict:
    tcp = products.get("TCP", "")
    summary = summary_fields(tcp)
    advisory_id = product_id(tcp) if tcp else None
    graphic_stamp = advisory_id.rsplit(" ", 1)[-1] if advisory_id else ""
    graphics_path = (
        f"https://www.nhc.noaa.gov/refresh/graphics_{nhc_bin.lower()}+shtml/{graphic_stamp}.shtml"
        if graphic_stamp
        else f"https://www.nhc.noaa.gov/graphics_{nhc_bin.lower()}.shtml"
    )
    snapshot = {
        "last_check": datetime.now(timezone.utc).isoformat(),
        "fetch_status": status,
        "storm_id": storm_id,
        "storm_name": summary.get("title", storm_id),
        "summary": summary,
        "alerts": parse_watches_and_warnings(tcp),
        "sources": {
            "nhc_home": "https://www.nhc.noaa.gov/",
            "nhc_advisory": "https://www.nhc.noaa.gov/text/MIATCP" + nhc_bin + ".shtml",
            "nhc_cone": f"{graphics_path}?wwCone#contents",
            "nhc_key_messages": f"{graphics_path}?key_messages#contents",
            "nhc_wind_probabilities": f"{graphics_path}?tswind120#contents",
            "nhc_arrival_time": f"{graphics_path}?mltoa34#contents",
        },
        "track": forecast_track_points(products.get("TCM", "")),
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
    return snapshot


def main() -> None:
    storm_id = os.environ.get("STORM_ID", "AL092026")
    nhc_bin = os.environ.get("NHC_BIN", "AT4")
    products = fetch_products(storm_id, nhc_bin)
    status = "Connected" if products else "No current NHC products found"
    destination = Path(os.environ.get("DASHBOARD_JSON", "dashboard.json"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(build_snapshot(storm_id, nhc_bin, products, status), indent=2) + "\n")
    print(f"Published {len(products)} public NHC products ({status})")


if __name__ == "__main__":
    main()
