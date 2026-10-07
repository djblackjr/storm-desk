# Storm Desk

A public-safe, installable iPhone web app for NHC storm tracking. This repository contains only the app UI, its NHC product-fetch/build script, and public storm data produced during deployment.

The app intentionally does **not** contain or publish the private hurricane monitor's home coordinates, household address, config, local logs/state, evacuation decision, or property-specific analysis reports. Its map shows only the cyclone's public NHC position and forecast points. Wind probabilities are for Destin Executive Airport and Panama City.

## GitHub Pages deployment

The GitHub Actions workflow fetches current public NHC text products and deploys the static app about every five minutes, on a push to `main`, or when manually run. GitHub Pages must be set to **Settings → Pages → Build and deployment → GitHub Actions**.

Change the repository variables `STORM_ID` and `NHC_BIN` to follow another cyclone (defaults: `AL092026` and `AT4`). A GitHub scheduled workflow may be delayed; this is a public informational display, not an emergency alert service. Keep official NHC and local-authority sources open for decisions.

## iPhone install

After Pages is enabled and the first workflow deploys, open <https://djblackjr.github.io/storm-desk/> in Safari. Tap **Share → Add to Home Screen** and launch Storm Desk from the Home Screen. Browser notifications can alert about newly published products while the app is open. The existing ntfy service is still the background phone-alert route.

The map uses Leaflet and OpenStreetMap tiles. The app shell is cached for offline launch; storm data itself is not cached and may be unavailable without a network connection.
