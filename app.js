const $ = selector => document.querySelector(selector);
const SVG_NS = 'http://www.w3.org/2000/svg';
const IEM_TILES = 'https://mesonet.agron.iastate.edu/cache/tile.py/1.0.0';
const RADAR_FRAMES = ['-m50m', '-m45m', '-m40m', '-m35m', '-m30m', '-m25m', '-m20m', '-m15m', '-m10m', '-m05m', ''];
const TIDE_STATIONS = [{ id: '8729108', name: 'Panama City' }, { id: '8729840', name: 'Pensacola' }];
const CATEGORIES = [
  { min: 157, code: '5', label: 'Category 5 hurricane', color: '#d98bff' },
  { min: 130, code: '4', label: 'Category 4 hurricane', color: '#ff5c7a' },
  { min: 111, code: '3', label: 'Category 3 hurricane', color: '#ff8552' },
  { min: 96, code: '2', label: 'Category 2 hurricane', color: '#ffb347' },
  { min: 74, code: '1', label: 'Category 1 hurricane', color: '#ffe066' },
  { min: 39, code: 'TS', label: 'Tropical storm', color: '#5ee2c0' },
  { min: 0, code: 'TD', label: 'Tropical depression', color: '#6fb8ff' },
];
const COAST_ALERTS = {
  HWR: ['Hurricane Warning', '#ff3b3b'], HWA: ['Hurricane Watch', '#ff8ad8'],
  TWR: ['Tropical Storm Warning', '#4aa3ff'], TWA: ['Tropical Storm Watch', '#ffe066'],
};
const RADII_STYLE = { 34: ['#ffe066', .16], 50: ['#ff9f43', .24], 64: ['#ff4d6d', .32] };
const MODEL_COLORS = ['#7fd4ff', '#ffa8e2', '#b9f27c', '#ffcf70', '#c9a8ff', '#7ff0d0', '#ff9d8a', '#9db8ff', '#f2e37c', '#8ae6a2', '#e0b0ff', '#ffd1a8'];
const CLASSIFICATIONS = { HU: 'Hurricane', TS: 'Tropical Storm', TD: 'Tropical Depression', STS: 'Subtropical Storm', SD: 'Subtropical Depression', PTC: 'Potential Tropical Cyclone', PC: 'Post-Tropical Cyclone' };
const BASINS = { AL: 'Atlantic', EP: 'East Pacific', CP: 'Central Pacific' };
const SEVERITY_ORDER = ['Extreme', 'Severe', 'Moderate', 'Minor', 'Unknown'];
const LAYER_DEFAULTS = { cone: true, warnings: true, wind: true, satellite: true, radar: false, arrival: false, models: false };
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

let map;
let trackGroup;
let cursor;
let overlays = {};
let layerState = { ...LAYER_DEFAULTS };
let radarLayers = null;
let radarTimer = null;
let radarIndex = 0;
let playTimer = null;
let timelinePoints = [];
let radiiFeatures = [];
let mapKey = null;
let loadedLayersVersion = null;
let nextAdvisory = [];
let localAlertsAt = null;
let initialized = false;
let rendered = false;
let lastProductIds = [];
let registrationPromise = Promise.resolve(null);

if ('serviceWorker' in navigator) {
  registrationPromise = navigator.serviceWorker.register(new URL('./sw.js', document.baseURI))
    .catch(error => { console.warn('Offline app shell unavailable', error); return null; });
  if ('Notification' in window && Notification.permission === 'granted') $('#alerts').textContent = 'Alerts enabled';
}
try {
  Object.assign(layerState, JSON.parse(localStorage.getItem('stormDeskLayers') || '{}'));
} catch { layerState = { ...LAYER_DEFAULTS }; }

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function svg(tag, attributes) {
  const node = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes || {}).forEach(([name, value]) => node.setAttribute(name, value));
  return node;
}

function empty(text) {
  return el('div', 'empty', text);
}

function setText(selector, value, fallback = '—') {
  $(selector).textContent = value || fallback;
}

function formatTime(value) {
  if (!value) return 'Waiting for first check';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function shortTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return null;
  return new Intl.DateTimeFormat(undefined, { weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(date);
}

function categoryFor(mph) {
  return Number.isFinite(mph) ? CATEGORIES.find(category => mph >= category.min) : null;
}

function compass(degrees) {
  if (!Number.isFinite(degrees)) return '';
  return ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE', 'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'][Math.round(degrees / 22.5) % 16];
}

function mapInit() {
  if (!window.L || map) return;
  map = L.map('map', { scrollWheelZoom: false, zoomSnap: .5 }).setView([26.5, -89], 5);
  map.createPane('imagery').style.zIndex = 250;
  map.createPane('labels').style.zIndex = 450;
  map.getPane('labels').style.pointerEvents = 'none';
  const esri = 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas';
  const attribution = 'Basemap: Esri, HERE, Garmin, &copy; OpenStreetMap contributors';
  L.tileLayer(`${esri}/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 10, attribution }).addTo(map);
  L.tileLayer(`${esri}/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, { maxZoom: 10, pane: 'labels' }).addTo(map);
  overlays = {
    satellite: L.tileLayer(`${IEM_TILES}/goes_east_fulldisk_ch13/{z}/{x}/{y}.png`, {
      pane: 'imagery', opacity: .55, maxZoom: 10, attribution: 'Imagery: <a href="https://mesonet.agron.iastate.edu/">Iowa Environmental Mesonet</a>',
    }),
    radar: L.layerGroup(), cone: L.layerGroup(), warnings: L.layerGroup(), wind: L.layerGroup(), arrival: L.layerGroup(), models: L.layerGroup(),
  };
  trackGroup = L.layerGroup().addTo(map);
  applyLayerState();
}

function applyLayerState() {
  document.querySelectorAll('.chip[data-layer]').forEach(button => {
    button.setAttribute('aria-pressed', String(Boolean(layerState[button.dataset.layer])));
  });
  if (!map) return;
  Object.entries(overlays).forEach(([name, layer]) => {
    if (layerState[name] && !map.hasLayer(layer)) layer.addTo(map);
    if (!layerState[name] && map.hasLayer(layer)) map.removeLayer(layer);
  });
  if (layerState.radar) startRadar(); else stopRadar();
  renderLegend();
}

function startRadar() {
  if (!radarLayers) {
    radarLayers = RADAR_FRAMES.map(suffix => L.tileLayer(`${IEM_TILES}/nexrad-n0q-900913${suffix}/{z}/{x}/{y}.png`, { pane: 'imagery', opacity: 0, maxZoom: 10 }));
    radarLayers.forEach(layer => layer.addTo(overlays.radar));
  }
  if (radarTimer) return;
  radarIndex = reducedMotion ? RADAR_FRAMES.length - 1 : 0;
  showRadarFrame();
  if (!reducedMotion) {
    let hold = 0;
    radarTimer = window.setInterval(() => {
      // Pause on the newest frame so the current picture is readable.
      if (radarIndex === RADAR_FRAMES.length - 1 && hold < 3) { hold += 1; return; }
      hold = 0;
      radarIndex = (radarIndex + 1) % RADAR_FRAMES.length;
      showRadarFrame();
    }, 450);
  }
}

function showRadarFrame() {
  radarLayers.forEach((layer, index) => layer.setOpacity(index === radarIndex ? .8 : 0));
  const minutesAgo = (RADAR_FRAMES.length - 1 - radarIndex) * 5;
  $('#radar-label').hidden = false;
  $('#radar-label').textContent = minutesAgo ? `Radar · ${minutesAgo} min ago` : 'Radar · latest';
}

function stopRadar() {
  window.clearInterval(radarTimer);
  radarTimer = null;
  $('#radar-label').hidden = true;
}

function makePopup(title, detail) {
  const wrapper = el('div');
  wrapper.append(el('strong', '', title), el('div', '', detail));
  return wrapper;
}

function pulseIcon(color) {
  // color comes from the fixed CATEGORIES table, never from fetched data.
  return L.divIcon({ className: 'storm-cursor', html: `<span style="--c:${color}"></span>`, iconSize: [26, 26], iconAnchor: [13, 13] });
}

function validKey(iso) {
  return iso ? iso.slice(0, 4) + iso.slice(5, 7) + iso.slice(8, 10) + iso.slice(11, 13) : '';
}

function pointTime(point) {
  return shortTime(point.time_iso) || point.time_utc;
}

function renderMap(data) {
  mapInit();
  if (!map) return;
  const key = (data.products || []).map(product => product.id).join('|');
  if (key === mapKey) return;
  mapKey = key;
  trackGroup.clearLayers();
  const track = (Array.isArray(data.track) ? data.track : []).filter(item => Number.isFinite(item.lat) && Number.isFinite(item.lon));
  timelinePoints = track;
  for (let index = 1; index < track.length; index += 1) {
    const color = (categoryFor(track[index].wind_mph) || CATEGORIES[5]).color;
    L.polyline([[track[index - 1].lat, track[index - 1].lon], [track[index].lat, track[index].lon]], { color, weight: 3.5, opacity: .95, className: 'track-line' }).addTo(trackGroup);
  }
  track.forEach(point => {
    const category = categoryFor(point.wind_mph);
    L.circleMarker([point.lat, point.lon], { radius: 5, color: '#061219', weight: 1.5, fillColor: category ? category.color : '#9db2b9', fillOpacity: 1 })
      .bindPopup(makePopup(`${point.label} · ${pointTime(point)}`, `${point.wind_mph ?? '—'} mph${category ? ` · ${category.label}` : ''}`)).addTo(trackGroup);
  });
  const coordinates = track.map(item => [item.lat, item.lon]);
  if (!coordinates.length && data.position && Number.isFinite(data.position.lat) && Number.isFinite(data.position.lon)) {
    coordinates.push([data.position.lat, data.position.lon]);
  }
  cursor = coordinates.length ? L.marker(coordinates[0], { icon: pulseIcon('#ff8d80'), interactive: false, zIndexOffset: 500 }).addTo(trackGroup) : null;
  const slider = $('#timeline');
  slider.max = Math.max(track.length - 1, 0);
  slider.value = 0;
  slider.disabled = track.length < 2;
  $('#play').disabled = track.length < 2;
  showTimelineStep(0);
  if (coordinates.length > 1) map.fitBounds(coordinates, { padding: [30, 30], maxZoom: 7 });
  else if (coordinates.length === 1) map.setView(coordinates[0], 5);
  setTimeout(() => map.invalidateSize(), 0);
}

function showTimelineStep(index) {
  const point = timelinePoints[index];
  if (!point || !map) {
    setText('#timeline-label', 'Forecast timeline unavailable');
    return;
  }
  const category = categoryFor(point.wind_mph);
  if (cursor) {
    cursor.setLatLng([point.lat, point.lon]);
    cursor.setIcon(pulseIcon(category ? category.color : '#ff8d80'));
  }
  overlays.wind.clearLayers();
  const matching = radiiFeatures.filter(feature => String(feature.properties?.validtime) === validKey(point.time_iso));
  matching.sort((a, b) => a.properties.radii - b.properties.radii).forEach(feature => {
    const [color, fillOpacity] = RADII_STYLE[feature.properties.radii] || ['#ffffff', .1];
    L.geoJSON(feature, { style: { color, weight: 1, fillColor: color, fillOpacity }, interactive: false }).addTo(overlays.wind);
  });
  const parts = [`${point.label} · ${pointTime(point)}`, `${point.wind_mph ?? '—'} mph`];
  if (category) parts.push(category.label);
  if (radiiFeatures.length && !matching.length) parts.push('no wind-field forecast this far out');
  setText('#timeline-label', parts.join(' · '));
}

function togglePlay() {
  if (playTimer) {
    window.clearInterval(playTimer);
    playTimer = null;
    $('#play').textContent = '▶';
    return;
  }
  $('#play').textContent = '❚❚';
  playTimer = window.setInterval(() => {
    const slider = $('#timeline');
    slider.value = (Number(slider.value) + 1) % (Number(slider.max) + 1);
    showTimelineStep(Number(slider.value));
  }, 1100);
}

function renderLayers(layers) {
  if (!map) return;
  ['cone', 'warnings', 'arrival', 'models'].forEach(name => overlays[name].clearLayers());
  if (layers.cone) {
    L.geoJSON(layers.cone, { style: { color: '#ffffff', weight: 1.5, dashArray: '5 5', fillColor: '#ffffff', fillOpacity: .12 }, interactive: false }).addTo(overlays.cone);
  }
  if (layers.past_track) {
    L.geoJSON(layers.past_track, { style: { color: '#8ea3ad', weight: 2, opacity: .8 }, interactive: false }).addTo(overlays.cone);
  }
  if (layers.watch_warning) {
    L.geoJSON(layers.watch_warning, {
      style: feature => ({ color: (COAST_ALERTS[feature.properties.tcww] || ['', '#ffffff'])[1], weight: 7, opacity: .95, lineCap: 'round' }),
      onEachFeature: (feature, layer) => layer.bindTooltip((COAST_ALERTS[feature.properties.tcww] || ['NHC coastal watch or warning'])[0], { sticky: true }),
    }).addTo(overlays.warnings);
  }
  if (layers.arrival) {
    L.geoJSON(layers.arrival, {
      style: { color: '#9fd6ff', weight: 1.5, dashArray: '2 6', opacity: .9 },
      onEachFeature: (feature, layer) => layer.bindTooltip(String(feature.properties.arrival_time || ''), { permanent: true, direction: 'center', className: 'arrival-label' }),
    }).addTo(overlays.arrival);
  }
  if (layers.models) {
    layers.models.features.forEach((feature, index) => {
      L.geoJSON(feature, { style: { color: MODEL_COLORS[index % MODEL_COLORS.length], weight: 1.6, opacity: .85 } })
        .bindTooltip(String(feature.properties.model || 'Model'), { sticky: true }).addTo(overlays.models);
    });
  }
  radiiFeatures = layers.wind_radii?.features || [];
  showTimelineStep(Number($('#timeline').value));
  const missing = [['cone', 'cone'], ['watch_warning', 'warnings'], ['wind_radii', 'wind'], ['arrival', 'arrival'], ['models', 'models']]
    .filter(([key]) => !layers[key]).map(([, name]) => name);
  document.querySelectorAll('.chip[data-layer]').forEach(button => {
    const unavailable = missing.includes(button.dataset.layer);
    button.disabled = unavailable;
    button.title = unavailable ? 'Not available from NOAA for this advisory' : '';
  });
  renderLegend(layers.models?.run);
}

function renderLegend(modelRun) {
  const legend = $('#map-legend');
  if (modelRun !== undefined) legend.dataset.modelRun = modelRun || '';
  legend.replaceChildren();
  const add = (color, text, shape = 'dot') => {
    const item = el('span');
    const swatch = el('i', shape);
    swatch.style.background = color;
    item.append(swatch, document.createTextNode(text));
    legend.append(item);
  };
  CATEGORIES.slice().reverse().forEach(category => add(category.color, category.code === 'TD' || category.code === 'TS' ? category.code : `Cat ${category.code}`));
  if (layerState.warnings) Object.values(COAST_ALERTS).forEach(([label, color]) => add(color, label, 'line'));
  if (layerState.wind) Object.entries(RADII_STYLE).forEach(([knots, [color]]) => add(color, `${Math.round(knots * 1.15078)}+ mph winds`, 'square'));
  if (layerState.models && legend.dataset.modelRun) {
    const run = legend.dataset.modelRun;
    legend.append(el('span', '', `Model run ${run.slice(4, 6)}/${run.slice(6, 8)} ${run.slice(8, 10)}Z · guidance only`));
  }
}

async function refreshLayers(data) {
  if (!data.layers_version || data.layers_version === loadedLayersVersion || !map) return;
  try {
    const response = await fetch(new URL(`./layers.json?t=${Date.now()}`, document.baseURI), { cache: 'no-store' });
    if (!response.ok) throw new Error(`layers returned ${response.status}`);
    renderLayers(await response.json());
    loadedLayersVersion = data.layers_version;
  } catch (error) {
    console.warn('Map overlays unavailable', error);
  }
}

function renderTrack(data) {
  const list = $('#track');
  list.replaceChildren();
  const track = Array.isArray(data.track) ? data.track : [];
  if (!track.length) {
    list.append(empty('Forecast positions appear when they are included in the active NHC advisory.'));
    return;
  }
  track.forEach((point, index) => {
    const category = categoryFor(point.wind_mph);
    const card = el('button', 'track-point');
    card.type = 'button';
    if (category) card.style.borderTopColor = category.color;
    const title = el('strong', '', pointTime(point));
    if (category) title.style.color = category.color;
    card.append(title, el('span', '', `${point.wind_mph ?? '—'} mph${category ? ` · ${category.code === 'TS' || category.code === 'TD' ? category.code : `Cat ${category.code}`}` : ''}`));
    card.addEventListener('click', () => {
      $('#timeline').value = index;
      showTimelineStep(index);
      if (map) map.panTo([point.lat, point.lon]);
    });
    list.append(card);
  });
}

function renderHero(data) {
  const windMph = Number((data.summary?.winds || '').match(/\d+(?:\.\d+)?/)?.[0]);
  const category = categoryFor(windMph);
  const hero = $('#hero');
  hero.dataset.cat = category ? category.code : 'unknown';
  hero.style.setProperty('--cat', category ? category.color : '#8198a1');
  setText('#cat-code', category ? category.code : '?');
  setText('#cat-label', category ? (category.min >= 74 ? 'CATEGORY' : category.label.toUpperCase()) : 'INTENSITY UNKNOWN');
  setText('#storm-id', data.storm_id);
  setText('#advisory-number', data.advisory?.number ? `ADVISORY ${data.advisory.number}` : 'ADVISORY —');
  setText('#storm-name', (data.storm_name || '').replace(/\s+Advisory Number.*$/i, '') || 'No current system data');
  setText('#headline', data.summary?.headline || 'Public NHC storm information will appear here.');
  const pos = data.position;
  setText('#position', pos ? `⌖ ${Math.abs(pos.lat).toFixed(1)}°${pos.lat < 0 ? 'S' : 'N'}, ${Math.abs(pos.lon).toFixed(1)}°${pos.lon < 0 ? 'W' : 'E'}` : null, 'Position unavailable');
  const status = $('#storm-status');
  status.hidden = data.storm_active !== false;
  status.textContent = 'NHC no longer lists this system as active — verify at nhc.noaa.gov';
  nextAdvisory = data.advisory?.next || [];
  renderCountdown();
  renderChanges(data.changes);
  renderIntensity(data.track || []);
}

function renderCountdown() {
  const pill = $('#next-advisory');
  if (!nextAdvisory.length) {
    pill.textContent = rendered ? 'Next advisory time not stated by NHC' : 'Next advisory time unavailable';
    return;
  }
  const now = Date.now();
  const upcoming = nextAdvisory.find(item => item.utc && new Date(item.utc).getTime() > now);
  if (!upcoming) {
    pill.textContent = `Advisory due since ${nextAdvisory[nextAdvisory.length - 1].text} — awaiting NHC`;
    return;
  }
  const minutes = Math.max(1, Math.round((new Date(upcoming.utc).getTime() - now) / 60000));
  const wait = minutes >= 60 ? `${Math.floor(minutes / 60)}h ${minutes % 60}m` : `${minutes}m`;
  pill.textContent = `◷ Next ${upcoming.kind === 'intermediate' ? 'intermediate ' : ''}advisory in ${wait} · ${upcoming.text}`;
}

function renderChanges(changes) {
  const line = $('#changes');
  line.hidden = !changes;
  if (!changes) return;
  const parts = (changes.items || []).map(item => `${item.label} ${item.from} → ${item.to}`);
  (changes.alerts_added || []).forEach(alert => parts.push(`New: ${alert}`));
  (changes.alerts_removed || []).forEach(alert => parts.push(`Ended: ${alert}`));
  line.textContent = parts.length
    ? `Since ${changes.since}: ${parts.join(' · ')}`
    : `No change in winds, pressure, movement or coastal watches since ${changes.since}.`;
}

function renderIntensity(track) {
  const chart = $('#intensity');
  chart.replaceChildren();
  const points = track.filter(point => Number.isFinite(point.wind_mph));
  if (points.length < 2) {
    setText('#peak', 'Forecast intensity unavailable');
    return;
  }
  const width = 320, height = 96, left = 8, right = 8, top = 14, bottom = 18;
  const ceiling = Math.max(120, ...points.map(point => point.wind_mph)) + 12;
  const x = index => left + index * (width - left - right) / (points.length - 1);
  const y = mph => top + (1 - mph / ceiling) * (height - top - bottom);
  [[74, 'Hurricane'], [111, 'Major']].filter(([mph]) => mph < ceiling).forEach(([mph, label]) => {
    chart.append(svg('line', { x1: left, x2: width - right, y1: y(mph), y2: y(mph), class: 'threshold' }));
    const text = svg('text', { x: width - right, y: y(mph) - 3, class: 'threshold-label', 'text-anchor': 'end' });
    text.textContent = label;
    chart.append(text);
  });
  const path = points.map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point.wind_mph).toFixed(1)}`).join(' ');
  chart.append(svg('path', { d: `${path} L${x(points.length - 1)},${height - bottom} L${x(0)},${height - bottom} Z`, class: 'area' }));
  chart.append(svg('path', { d: path, class: 'curve' }));
  points.forEach((point, index) => {
    chart.append(svg('circle', { cx: x(index), cy: y(point.wind_mph), r: index ? 3 : 4.5, fill: (categoryFor(point.wind_mph) || CATEGORIES[6]).color, class: 'dot-point' }));
  });
  const peak = points.reduce((best, point) => (point.wind_mph > best.wind_mph ? point : best));
  const peakCategory = categoryFor(peak.wind_mph);
  const label = svg('text', { x: Math.min(Math.max(x(points.indexOf(peak)), 26), width - 26), y: y(peak.wind_mph) - 7, class: 'peak-label', 'text-anchor': 'middle' });
  label.textContent = `${peak.wind_mph} mph`;
  chart.append(label);
  setText('#peak', `Forecast peak ${peak.wind_mph} mph${peakCategory ? ` · ${peakCategory.label}` : ''} · ${pointTime(peak)}`);
}

function renderSystems(data) {
  const strip = $('#systems');
  strip.replaceChildren();
  if (!Array.isArray(data.active_storms)) {
    strip.append(el('span', 'system muted-text', 'Other active systems: list unavailable from NHC'));
    return;
  }
  const others = data.active_storms.filter(storm => storm.id !== data.storm_id);
  if (!others.length) {
    strip.append(el('span', 'system muted-text', 'NHC lists no other active systems'));
    return;
  }
  strip.append(el('span', 'eyebrow', 'ALSO ACTIVE'));
  others.forEach(storm => {
    const chip = el('span', 'system');
    const dot = el('i');
    dot.style.background = (categoryFor(storm.wind_mph) || { color: '#8198a1' }).color;
    const basin = BASINS[String(storm.id).slice(0, 2)] || '';
    chip.append(dot, document.createTextNode(`${CLASSIFICATIONS[storm.classification] || 'System'} ${storm.name || storm.id} · ${storm.wind_mph ?? '—'} mph${basin ? ` · ${basin}` : ''}`));
    strip.append(chip);
  });
}

function renderProbabilities(data) {
  const container = $('#probability-table');
  container.replaceChildren();
  const rows = Object.entries(data.wind_probabilities || {});
  if (!rows.some(([, values]) => Object.keys(values || {}).length)) {
    container.append(empty('NHC wind probabilities unavailable.'));
    return;
  }
  const labels = { 34: 'Tropical-storm force (39+ mph)', 50: 'Strong (58+ mph)', 64: 'Hurricane force (74+ mph)' };
  rows.forEach(([place, probabilities]) => {
    const block = el('div', 'prob-block');
    block.append(el('strong', 'place', place));
    ['34', '50', '64'].forEach(threshold => {
      const value = probabilities?.[threshold];
      const row = el('div', 'prob-bar');
      const bar = el('div', 'bar');
      const fill = el('i');
      fill.style.width = `${Number.isFinite(value) ? value : 0}%`;
      fill.style.background = RADII_STYLE[threshold][0];
      bar.append(fill);
      row.append(el('span', '', labels[threshold]), bar, el('b', '', value === undefined ? '—' : `${value}%`));
      block.append(row);
    });
    container.append(block);
  });
}

function renderProducts(data) {
  const container = $('#products');
  container.replaceChildren();
  (data.products || []).forEach(product => {
    const row = el('div', 'product');
    const copy = el('div');
    copy.append(el('strong', '', product.name), el('small', '', product.id || 'ID unavailable'));
    row.append(copy, el('b', '', product.code));
    container.append(row);
  });
  if (!container.childElementCount) container.append(empty('No current NHC products found.'));
}

function renderWatchAlerts(data) {
  const list = $('#watch-list');
  list.replaceChildren();
  const alerts = Array.isArray(data.alerts) ? data.alerts : [];
  if (!alerts.length) {
    const hasAdvisory = data.products?.some(product => product.code === 'TCP');
    let message = 'NHC watch/warning information is not available in this snapshot.';
    if (hasAdvisory && data.alerts_status === 'none_in_effect') message = 'NHC states there are no coastal watches or warnings in effect for this system.';
    else if (hasAdvisory) message = 'No watch/warning entries were parsed from the latest NHC public advisory. Open the official product to verify.';
    list.append(empty(message));
    return;
  }
  alerts.forEach(alert => {
    const card = el('article', 'watch-item');
    card.dataset.kind = `${/surge/i.test(alert.type) ? 'surge' : /hurricane/i.test(alert.type) ? 'hurricane' : 'tropical'}-${/warning/i.test(alert.type) ? 'warning' : 'watch'}`;
    const areas = el('ul');
    (alert.areas || []).forEach(area => areas.append(el('li', '', area)));
    card.append(el('strong', '', alert.type), areas);
    list.append(card);
  });
}

function renderKeyMessages(data) {
  const list = $('#key-list');
  list.replaceChildren();
  const messages = Array.isArray(data.key_messages) ? data.key_messages : [];
  if (!messages.length) {
    list.append(el('li', 'empty', 'Key messages were not found in the latest NHC discussion. Open the official graphic to verify.'));
    return;
  }
  messages.forEach(message => list.append(el('li', '', message)));
}

function renderSurge(data) {
  const list = $('#surge-list');
  list.replaceChildren();
  const ranges = Array.isArray(data.surge_forecast) ? data.surge_forecast : [];
  if (!ranges.length) {
    list.append(empty('No storm surge height ranges were found in the latest NHC advisory. That does not mean no surge risk — check the official map.'));
    return;
  }
  const tallest = Math.max(...ranges.map(range => range.high_ft));
  ranges.forEach(range => {
    const row = el('div', 'surge-row');
    const bar = el('div', 'bar');
    const fill = el('i');
    fill.style.marginLeft = `${(range.low_ft / tallest) * 100}%`;
    fill.style.width = `${((range.high_ft - range.low_ft) / tallest) * 100}%`;
    bar.append(fill);
    row.append(el('span', '', range.area), bar, el('b', '', `${range.low_ft}–${range.high_ft} ft`));
    list.append(row);
  });
}

function renderBuoys(data) {
  const list = $('#buoy-list');
  list.replaceChildren();
  const buoys = Array.isArray(data.buoys) ? data.buoys : [];
  if (!buoys.length) {
    list.append(empty('Buoy observations unavailable in this snapshot.'));
    return;
  }
  buoys.forEach(buoy => {
    const card = el('article', 'obs-card');
    const ageHours = (Date.now() - new Date(buoy.observed_utc).getTime()) / 3600000;
    card.append(el('label', '', buoy.name.toUpperCase()));
    const value = el('strong', '', buoy.wind_mph === null ? '—' : `${Math.round(buoy.wind_mph)}`);
    value.append(el('small', '', `mph ${compass(buoy.wind_dir_deg)}`));
    card.append(value);
    const details = [];
    if (buoy.gust_mph !== null) details.push(`Gusts ${Math.round(buoy.gust_mph)} mph`);
    if (buoy.wave_ft !== null) details.push(`Waves ${buoy.wave_ft.toFixed(1)} ft`);
    if (buoy.pressure_mb !== null) details.push(`${buoy.pressure_mb} mb`);
    card.append(el('p', '', details.join(' · ') || 'No further readings'));
    card.append(el('p', ageHours > 2 ? 'stale' : '', `Observed ${shortTime(buoy.observed_utc) || 'time unknown'}${ageHours > 2 ? ' — may be out of date' : ''}`));
    list.append(card);
  });
}

function tideSparkline(series) {
  const width = 240, height = 54, pad = 4;
  const values = series.flatMap(row => [row.observed, row.predicted]).filter(Number.isFinite);
  const low = Math.min(...values), span = Math.max(Math.max(...values) - low, .5);
  const chart = svg('svg', { viewBox: `0 0 ${width} ${height}`, class: 'tide-chart', role: 'img', 'aria-label': 'Observed and predicted water level, last 24 hours' });
  const line = key => series.map((row, index) => [index, row[key]]).filter(([, value]) => Number.isFinite(value))
    .map(([index, value], order) => `${order ? 'L' : 'M'}${(pad + index * (width - 2 * pad) / (series.length - 1)).toFixed(1)},${(pad + (1 - (value - low) / span) * (height - 2 * pad)).toFixed(1)}`).join(' ');
  chart.append(svg('path', { d: line('predicted'), class: 'predicted' }), svg('path', { d: line('observed'), class: 'observed' }));
  return chart;
}

async function tideStation(station) {
  const url = product => `https://api.tidesandcurrents.noaa.gov/api/prod/datagetter?range=24&station=${station.id}&product=${product}&datum=MLLW&units=english&time_zone=gmt&format=json&application=StormDesk`;
  const [observedResponse, predictedResponse] = await Promise.all([fetch(url('water_level')), fetch(url('predictions'))]);
  if (!observedResponse.ok || !predictedResponse.ok) throw new Error('tide gauge unavailable');
  const observed = (await observedResponse.json()).data || [];
  const predicted = new Map(((await predictedResponse.json()).predictions || []).map(row => [row.t, Number(row.v)]));
  const series = observed.filter(row => row.v !== '').map(row => ({ time: row.t, observed: Number(row.v), predicted: predicted.get(row.t) }));
  if (series.length < 2) throw new Error('tide gauge returned no readings');
  return series;
}

async function refreshTides() {
  const list = $('#tide-list');
  const cards = await Promise.all(TIDE_STATIONS.map(async station => {
    const card = el('article', 'obs-card');
    card.append(el('label', '', `${station.name.toUpperCase()} TIDE GAUGE`));
    try {
      const series = await tideStation(station);
      const latest = series[series.length - 1];
      const value = el('strong', '', latest.observed.toFixed(1));
      value.append(el('small', '', 'ft'));
      card.append(value);
      if (Number.isFinite(latest.predicted)) {
        const difference = latest.observed - latest.predicted;
        const note = el('p', difference >= 2 ? 'high' : difference >= 1 ? 'stale' : '', `${Math.abs(difference).toFixed(1)} ft ${difference >= 0 ? 'above' : 'below'} predicted tide`);
        card.append(note);
      } else {
        card.append(el('p', '', 'Predicted tide unavailable for comparison'));
      }
      card.append(tideSparkline(series), el('p', '', `Observed ${shortTime(`${latest.time.replace(' ', 'T')}Z`) || latest.time} · solid observed, dashed predicted`));
    } catch {
      card.append(el('strong', '', '—'), el('p', '', 'Water level unavailable from NOAA right now.'));
    }
    return card;
  }));
  list.replaceChildren(...cards);
}

async function refreshLocalAlerts(zone) {
  const list = $('#local-list');
  setText('#local-zone', `ZONE ${zone}`);
  $('#local-link').href = `https://forecast.weather.gov/MapClick.php?zoneid=${encodeURIComponent(zone)}`;
  try {
    const response = await fetch(`https://api.weather.gov/alerts/active/zone/${encodeURIComponent(zone)}`, { headers: { Accept: 'application/geo+json' } });
    if (!response.ok) throw new Error(`NWS returned ${response.status}`);
    const latest = new Map();
    ((await response.json()).features || []).map(feature => feature.properties || {}).forEach(alert => {
      const known = latest.get(alert.event);
      if (!known || new Date(alert.sent) > new Date(known.sent)) latest.set(alert.event, alert);
    });
    const alerts = [...latest.values()].sort((a, b) => SEVERITY_ORDER.indexOf(a.severity) - SEVERITY_ORDER.indexOf(b.severity));
    localAlertsAt = new Date();
    list.replaceChildren();
    setText('#local-status', `Checked ${shortTime(localAlertsAt)} · ${alerts.length} active ${alerts.length === 1 ? 'alert' : 'alerts'}`);
    if (!alerts.length) {
      list.append(empty('NWS lists no active alerts for this zone right now.'));
      return;
    }
    alerts.forEach(alert => {
      const card = el('article', 'local-item');
      card.dataset.severity = alert.severity || 'Unknown';
      const until = shortTime(alert.ends || alert.expires);
      card.append(el('strong', '', alert.event || 'NWS alert'), el('small', '', `${alert.senderName || 'National Weather Service'}${until ? ` · until ${until}` : ''}`));
      if (alert.headline) card.append(el('p', '', alert.headline));
      const body = [alert.description, alert.instruction].filter(Boolean).join('\n\n');
      if (body) {
        const details = el('details');
        details.append(el('summary', '', 'Full text'), el('pre', '', body));
        card.append(details);
      }
      list.append(card);
    });
  } catch (error) {
    console.warn('NWS alerts unavailable', error);
    if (localAlertsAt) {
      setText('#local-status', `Could not refresh — showing alerts as of ${shortTime(localAlertsAt)}`);
    } else {
      setText('#local-status', 'Local alert status unknown');
      list.replaceChildren(empty('NWS alerts could not be loaded. This does not mean there are no alerts — check weather.gov.'));
    }
  }
}

function renderOfficialLinks(data) {
  const links = data.sources || {};
  for (const [selector, key] of [
    ['#cone-link', 'nhc_cone'],
    ['#key-messages-link', 'nhc_key_messages'],
    ['#wind-link', 'nhc_wind_probabilities'],
    ['#arrival-link', 'nhc_arrival_time'],
    ['#surge-link', 'nhc_peak_surge'],
  ]) {
    if (links[key]) $(selector).href = links[key];
  }
  if (links.nhc_advisory) {
    const advisoryLink = el('a', '', 'NHC ↗');
    advisoryLink.href = links.nhc_advisory;
    advisoryLink.target = '_blank';
    advisoryLink.rel = 'noreferrer';
    $('#watch-panel .tag').replaceChildren(advisoryLink);
  }
}

async function notifyNewProducts(products) {
  const ids = (products || []).map(product => product.id).filter(Boolean);
  let known = [];
  try {
    known = JSON.parse(localStorage.getItem('stormDeskPublicProducts') || '[]');
    if (!Array.isArray(known)) known = [];
  } catch { known = []; }
  if (!initialized) {
    initialized = true;
    localStorage.setItem('stormDeskPublicProducts', JSON.stringify(ids));
    lastProductIds = ids;
    return;
  }
  const knownSet = new Set([...known, ...lastProductIds]);
  const added = (products || []).filter(product => product.id && !knownSet.has(product.id));
  if (added.length && 'Notification' in window && Notification.permission === 'granted') {
    const registration = await registrationPromise;
    if (registration) {
      registration.showNotification('New NHC advisory product', {
        body: added.map(product => `${product.name}: ${product.id}`).join('\n').slice(0, 240),
        icon: new URL('./app-icon.svg', document.baseURI).href,
        tag: added.map(product => product.id).join('|'),
      });
    }
  }
  localStorage.setItem('stormDeskPublicProducts', JSON.stringify(ids));
  lastProductIds = ids;
}

function render(data) {
  const firstRender = !rendered;
  rendered = true;
  const connection = $('#connection');
  const online = data.fetch_status === 'Connected';
  connection.classList.toggle('online', online);
  connection.classList.toggle('error', !online);
  connection.textContent = online ? 'Snapshot fetched' : data.fetch_status || 'Waiting';
  setText('#checked', formatTime(data.last_check));
  const checkAge = data.last_check ? (Date.now() - new Date(data.last_check).getTime()) / 60000 : Infinity;
  const stale = checkAge > 20;
  setText('#stale', stale ? 'Snapshot may be stale — verify NHC directly.' : 'Scheduled fetch; GitHub timing can be delayed');
  $('#stale').classList.toggle('stale', stale);
  const issued = data.summary?.issued;
  setText('#advisory-issued', issued ? `Public advisory issued: ${issued}` : 'NHC advisory issue time unavailable; verify the official product.');
  setText('#wind', (data.summary?.winds || '').match(/\d+(?:\.\d+)?/)?.[0]);
  setText('#wind-text', data.summary?.winds || 'NHC reported');
  setText('#pressure', (data.summary?.pressure || '').match(/\d+(?:\.\d+)?/)?.[0]);
  setText('#movement', data.summary?.movement);
  const destin = data.wind_probabilities?.['DESTIN EXEC AP'] || {};
  setText('#dest-prob', destin['34'] === undefined ? null : `${destin['34']}%`);
  renderHero(data);
  renderSystems(data);
  renderMap(data);
  renderTrack(data);
  renderProbabilities(data);
  renderWatchAlerts(data);
  renderKeyMessages(data);
  renderSurge(data);
  renderBuoys(data);
  renderProducts(data);
  renderOfficialLinks(data);
  refreshLayers(data);
  if (firstRender) {
    const zone = /^[A-Z]{2}[CZ]\d{3}$/.test(data.local_zone || '') ? data.local_zone : 'FLZ108';
    refreshLocalAlerts(zone);
    window.setInterval(() => refreshLocalAlerts(zone), 120_000);
  }
  notifyNewProducts(data.products || []).catch(error => console.warn('Notification failed', error));
}

async function refresh() {
  const button = $('#refresh');
  button.disabled = true;
  try {
    const response = await fetch(new URL(`./dashboard.json?t=${Date.now()}`, document.baseURI), { cache: 'no-store' });
    if (!response.ok) throw new Error(`NHC snapshot returned ${response.status}`);
    render(await response.json());
  } catch (error) {
    $('#connection').classList.add('error');
    $('#connection').textContent = 'Snapshot unavailable';
    setText('#stale', error.message);
    if (!rendered) {
      setText('#advisory-issued', 'NHC advisory issue time unavailable; verify the official product.');
      $('#watch-list').replaceChildren(empty('NHC watch/warning information is not available in this snapshot.'));
      refreshLocalAlerts('FLZ108');
    }
  } finally {
    button.disabled = false;
  }
}

$('#refresh').addEventListener('click', () => { refresh(); refreshTides(); });
$('#alerts').addEventListener('click', async () => {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    $('#alerts').textContent = 'Alerts unavailable';
    return;
  }
  const permission = await Notification.requestPermission();
  const registration = permission === 'granted' ? await registrationPromise : null;
  $('#alerts').textContent = registration ? 'Alerts enabled' : 'Alerts not enabled';
});
document.querySelectorAll('.chip[data-layer]').forEach(button => button.addEventListener('click', () => {
  layerState[button.dataset.layer] = !layerState[button.dataset.layer];
  try { localStorage.setItem('stormDeskLayers', JSON.stringify(layerState)); } catch { /* preference only */ }
  applyLayerState();
}));
$('#timeline').addEventListener('input', event => showTimelineStep(Number(event.target.value)));
$('#play').addEventListener('click', togglePlay);
applyLayerState();
refresh();
refreshTides();
window.setInterval(refresh, 60_000);
window.setInterval(refreshTides, 360_000);
window.setInterval(renderCountdown, 30_000);
