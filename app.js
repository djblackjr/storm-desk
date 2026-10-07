const $ = selector => document.querySelector(selector);
let map;
let markers;
let line;
let initialized = false;
let lastProductIds = [];
let registrationPromise = Promise.resolve(null);

if ('serviceWorker' in navigator) {
  registrationPromise = navigator.serviceWorker.register(new URL('./sw.js', document.baseURI))
    .catch(error => { console.warn('Offline app shell unavailable', error); return null; });
  if ('Notification' in window && Notification.permission === 'granted') $('#alerts').textContent = 'Alerts enabled';
}

function setText(selector, value, fallback = '—') {
  $(selector).textContent = value || fallback;
}

function formatTime(value) {
  if (!value) return 'Waiting for first check';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(date);
}

function mapInit() {
  if (!window.L || map) return;
  map = L.map('map', { scrollWheelZoom: false }).setView([26.5, -89], 5);
  L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 12,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(map);
  markers = L.layerGroup().addTo(map);
  line = L.polyline([], { color: '#5ee2c0', weight: 3, opacity: .9, dashArray: '7 7' }).addTo(map);
}

function makePopup(title, detail) {
  const wrapper = document.createElement('div');
  const heading = document.createElement('strong');
  heading.textContent = title;
  const body = document.createElement('div');
  body.textContent = detail;
  wrapper.append(heading, body);
  return wrapper;
}

function renderMap(data) {
  mapInit();
  if (!map) return;
  markers.clearLayers();
  const track = Array.isArray(data.track) ? data.track : [];
  const points = track.filter(item => Number.isFinite(item.lat) && Number.isFinite(item.lon));
  const coordinates = points.map(item => [item.lat, item.lon]);
  line.setLatLngs(coordinates);
  if (data.position && Number.isFinite(data.position.lat) && Number.isFinite(data.position.lon)) {
    const current = [data.position.lat, data.position.lon];
    coordinates.push(current);
    L.circleMarker(current, { radius: 8, color: '#ffaaa0', weight: 2, fillColor: '#ff756a', fillOpacity: 1 })
      .bindPopup(makePopup(data.storm_name || 'Storm', 'Current reported NHC position')).addTo(markers);
  }
  points.forEach(point => {
    L.circleMarker([point.lat, point.lon], { radius: 4, color: '#84f0cf', weight: 1, fillColor: '#1ba88c', fillOpacity: 1 })
      .bindPopup(makePopup(`${point.label} · ${point.time_utc}`, `${point.wind_mph ?? '—'} mph forecast maximum`)).addTo(markers);
  });
  if (coordinates.length > 1) map.fitBounds(coordinates, { padding: [25, 25], maxZoom: 7 });
  else if (coordinates.length === 1) map.setView(coordinates[0], 5);
  setTimeout(() => map.invalidateSize(), 0);
}

function renderTrack(data) {
  const list = $('#track');
  list.replaceChildren();
  const track = Array.isArray(data.track) ? data.track : [];
  setText('#track-count', track.length ? `${track.length} advisory points` : 'Track unavailable');
  if (!track.length) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'Forecast positions appear when they are included in the active NHC advisory.';
    list.append(empty);
    return;
  }
  track.forEach(point => {
    const card = document.createElement('div');
    card.className = 'track-point';
    const title = document.createElement('strong');
    title.textContent = point.label;
    const detail = document.createElement('span');
    detail.textContent = `${point.time_utc} · ${point.wind_mph ?? '—'} mph`;
    card.append(title, detail);
    list.append(card);
  });
}

function renderProbabilities(data) {
  const container = $('#probabilities');
  container.replaceChildren();
  const heading = document.createElement('div');
  heading.className = 'prob-row header';
  ['LOCATION', '34 KT', '50 KT', '64 KT'].forEach(label => {
    const cell = document.createElement('span');
    cell.textContent = label;
    heading.append(cell);
  });
  container.append(heading);
  const rows = Object.entries(data.wind_probabilities || {});
  if (!rows.some(([, values]) => Object.keys(values || {}).length)) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'NHC wind probabilities unavailable.';
    container.append(empty);
    return;
  }
  rows.forEach(([place, probabilities]) => {
    const row = document.createElement('div');
    row.className = 'prob-row';
    const location = document.createElement('span');
    location.className = 'place';
    location.textContent = place;
    row.append(location);
    ['34', '50', '64'].forEach(threshold => {
      const cell = document.createElement('span');
      cell.className = 'value';
      cell.textContent = probabilities?.[threshold] === undefined ? '—' : `${probabilities[threshold]}%`;
      row.append(cell);
    });
    container.append(row);
  });
}

function renderProducts(data) {
  const container = $('#products');
  container.replaceChildren();
  (data.products || []).forEach(product => {
    const row = document.createElement('div');
    row.className = 'product';
    const copy = document.createElement('div');
    const name = document.createElement('strong');
    name.textContent = product.name;
    const id = document.createElement('small');
    id.textContent = product.id || 'ID unavailable';
    copy.append(name, id);
    const code = document.createElement('b');
    code.textContent = product.code;
    row.append(copy, code);
    container.append(row);
  });
  if (!container.childElementCount) {
    const empty = document.createElement('div');
    empty.className = 'empty';
    empty.textContent = 'No current NHC products found.';
    container.append(empty);
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
  const connection = $('#connection');
  const online = data.fetch_status === 'Connected';
  connection.classList.toggle('online', online);
  connection.classList.toggle('error', !online);
  connection.textContent = online ? 'NHC connected' : data.fetch_status || 'Waiting';
  setText('#checked', formatTime(data.last_check));
  const checkAge = data.last_check ? (Date.now() - new Date(data.last_check).getTime()) / 60000 : Infinity;
  setText('#stale', checkAge > 15 ? 'Snapshot may be stale — check NHC directly.' : 'Refreshes from NHC about every five minutes');
  setText('#storm-id', data.storm_id);
  setText('#storm-name', data.storm_name || 'No current system data');
  setText('#headline', data.summary?.headline || 'Public NHC storm information will appear here.');
  setText('#wind', (data.summary?.winds || '').match(/\d+(?:\.\d+)?/)?.[0]);
  setText('#wind-text', data.summary?.winds || 'NHC reported');
  setText('#pressure', (data.summary?.pressure || '').match(/\d+(?:\.\d+)?/)?.[0]);
  setText('#movement', data.summary?.movement);
  const destin = data.wind_probabilities?.['DESTIN EXEC AP'] || {};
  setText('#dest-prob', destin['34'] === undefined ? null : `${destin['34']}%`);
  const pos = data.position;
  setText('#position', pos ? `${pos.lat.toFixed(1)}°, ${Math.abs(pos.lon).toFixed(1)}° ${pos.lon < 0 ? 'W' : 'E'}` : null, 'Position unavailable');
  renderMap(data);
  renderTrack(data);
  renderProbabilities(data);
  renderProducts(data);
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
  } finally {
    button.disabled = false;
  }
}

$('#refresh').addEventListener('click', refresh);
$('#alerts').addEventListener('click', async () => {
  if (!('Notification' in window) || !('serviceWorker' in navigator)) {
    $('#alerts').textContent = 'Alerts unavailable';
    return;
  }
  const permission = await Notification.requestPermission();
  const registration = permission === 'granted' ? await registrationPromise : null;
  $('#alerts').textContent = registration ? 'Alerts enabled' : 'Alerts not enabled';
});
refresh();
window.setInterval(refresh, 60_000);
