# Storm Desk

A public-safe, installable iPhone web app for NHC storm tracking. This repository contains only the app UI, its NHC product-fetch/build script, and public storm data produced during deployment. It labels snapshot generation and NHC advisory issue times separately, extracts watch/warning areas explicitly listed in the NHC public advisory, and links to issuance-specific NHC cone, key-message, wind-probability, and earliest-arrival graphics.

The app intentionally does **not** contain or publish the private hurricane monitor's home coordinates, household address, config, local logs/state, evacuation decision, or property-specific analysis reports. Its map shows only public NOAA data for the cyclone. Wind probabilities are for Destin Executive Airport and Panama City; local alerts are for the public NWS forecast zone `FLZ108` (South Walton). The watch/warning panel summarizes NHC cyclone-area notices, not county evacuation orders. Missing or unparsed information is shown as unknown with an official source link, never interpreted as no alerts.

## What it shows

| Feature | Source | Fetched |
|---|---|---|
| Advisory summary, watches/warnings, key messages, surge ranges, next-advisory time | NHC text products | at build |
| Forecast cone, coastal watch/warning segments, wind radii, wind arrival times | NOAA `NHC_tropical_weather` MapServer | at build, into `layers.json` |
| Model track guidance (not the official forecast) | NHC ATCF a-deck | at build |
| Other active systems | NHC `CurrentStorms.json` | at build |
| Offshore buoy wind, waves, pressure | NOAA NDBC (`BUOYS` variable) | at build |
| Local alerts for the NWS zone (`NWS_ZONE` variable) | api.weather.gov | live in the browser |
| Tide gauge water level versus prediction | NOAA CO-OPS | live in the browser |
| Satellite and radar loop | Iowa Environmental Mesonet tiles | live in the browser |

If NHC cannot be reached during a build, the previous snapshot is republished and labelled, so good data is never replaced by an empty page. Each build also records what changed since the previous advisory.

## GitHub Pages deployment

The GitHub Actions workflow fetches current public NHC text products and deploys the static app every 15 minutes, on a push to `main`, or when manually run. GitHub Pages must be set to **Settings → Pages → Build and deployment → GitHub Actions**.

Change the repository variables `STORM_ID` and `NHC_BIN` to follow another cyclone (defaults: `AL092026` and `AT4`). A GitHub scheduled workflow may be delayed; this is a public informational display, not an emergency alert service. Keep official NHC and local-authority sources open for decisions.

## iPhone install

After Pages is enabled and the first workflow deploys, open <https://djblackjr.github.io/storm-desk/> in Safari. Tap **Share → Add to Home Screen** and launch Storm Desk from the Home Screen. Browser notifications can alert about newly published products while the app is open. The existing ntfy service is still the background phone-alert route.

The page reloads its static snapshot every minute, but the snapshot changes only when the scheduled GitHub workflow runs; the generation and advisory times help identify stale data. Each deploy stamps the commit SHA into the script and stylesheet URLs, so a new version never runs against a stale cached shell. The app shell is cached for offline launch; storm data itself is not cached and may be unavailable without a network connection. Use the Ready.gov, FEMA, Walton County, NWS, and NHC resources linked in the app. It is not an evacuation decision or emergency alert service.
