const VERSION = 'v1.0.0';

// ── OneSignal push notifications ────────────────────────────────────
const NOTIF_LANG_KEY = 'webquake_notif_lang';

function getNotificationLanguage() {
    return localStorage.getItem(NOTIF_LANG_KEY) || currentLanguage;
}

function updateNotifLanguageUI(lang) {
    const enBtn = document.getElementById('notif-lang-en');
    const jaBtn = document.getElementById('notif-lang-ja');
    if (enBtn) enBtn.classList.toggle('active', lang === 'en');
    if (jaBtn) jaBtn.classList.toggle('active', lang === 'ja');
}

function setNotificationLanguage(lang) {
    localStorage.setItem(NOTIF_LANG_KEY, lang);
    updateNotifLanguageUI(lang);
    if (window.OneSignal) {
        window.OneSignal.User.setLanguage(lang);
    }
    updateNtfyUI();
}

async function refreshNotificationUI() {
    const OneSignal = window.OneSignal;
    if (!OneSignal) return;
    const optedIn = OneSignal.User.PushSubscription.optedIn;
    const enabledToggle = document.getElementById('tog-notif-enabled');
    const alertsToggle = document.getElementById('tog-notif-alerts');
    const forecastsToggle = document.getElementById('tog-notif-forecasts');
    if (enabledToggle) enabledToggle.checked = !!optedIn;
    const tagToggles = [alertsToggle, forecastsToggle].filter(Boolean);
    for (const toggle of tagToggles) toggle.disabled = !optedIn;
    if (optedIn) {
        const tags = await OneSignal.User.getTags();
        if (alertsToggle) alertsToggle.checked = tags['alerts'] === 'subscribed';
        if (forecastsToggle) forecastsToggle.checked = tags['forecasts'] === 'subscribed';
    } else {
        for (const toggle of tagToggles) toggle.checked = false;
    }
    updateNtfyUI();
}

// ── ntfy push notifications (self-hosted, parallel to OneSignal) ───────
// Replace with your self-hosted ntfy host (no scheme) and topic names —
// these must match the WEBQUAKE_NTFY_TOPIC_*_EN / *_JA vars configured on
// the backend. Topics are split by language since ntfy has no per-subscriber
// language preference. Topics are public-read, so none of this is a secret.
const NTFY_SERVER = 'SERVER_URL';
const NTFY_TOPICS = {
    en: { alerts: 'webquake-alerts-en', forecasts: 'webquake-forecasts-en' },
    ja: { alerts: 'webquake-alerts-ja', forecasts: 'webquake-forecasts-ja' },
};
const NTFY_ENABLED_KEY = 'webquake_ntfy_enabled';
const NTFY_APP_LINKS = {
    ios: 'https://apps.apple.com/us/app/ntfy/id1625396347',
    android: 'https://play.google.com/store/apps/details?id=io.heckel.ntfy',
};

function detectPlatform() {
    const ua = navigator.userAgent || '';
    if (/iPad|iPhone|iPod/.test(ua)) return 'ios';
    if (/Android/.test(ua)) return 'android';
    return 'desktop';
}

function renderNtfySubscribeLinks() {
    const container = document.getElementById('ntfy-subscribe-links');
    if (!container) return;
    const platform = detectPlatform();
    const lang = getNotificationLanguage();
    const langTopics = NTFY_TOPICS[lang] || NTFY_TOPICS.en;
    const topics = [
        { key: langTopics.alerts, en: 'EEW &amp; Tsunami Alerts', ja: '緊急地震速報・津波警報' },
        { key: langTopics.forecasts, en: 'Earthquake Forecasts', ja: '地震動予測' },
    ];
    let html = `<div class="settings-note">
        <span class="en">Showing topics for notification language: ${lang.toUpperCase()}. Change it above to switch.</span>
        <span class="ja">通知言語: ${lang.toUpperCase()} のトピックを表示中です。上の設定で切り替えられます。</span>
    </div>`;
    if (platform === 'android') {
        // The Android app supports ntfy:// deep links straight to the subscribe screen.
        html += `<a class="ntfy-btn" href="${NTFY_APP_LINKS.android}" target="_blank" rel="noopener">
            <span class="en">Get the ntfy app</span><span class="ja">ntfyアプリを入手</span>
        </a>`;
        for (const t of topics) {
            html += `<a class="ntfy-btn" href="ntfy://${NTFY_SERVER}/${t.key}">
                <span class="en">Subscribe: ${t.en}</span><span class="ja">登録: ${t.ja}</span>
            </a>`;
        }
        html += `<div class="settings-note">
            <span class="en">If the app isn't installed yet, install it first, then tap the subscribe links again. Server: ${NTFY_SERVER}</span>
            <span class="ja">アプリが未インストールの場合は、先にインストールしてから登録リンクを再度タップしてください。サーバー: ${NTFY_SERVER}</span>
        </div>`;
    } else if (platform === 'ios') {
        // The ntfy iOS app doesn't support ntfy:// deep links yet (Android-only),
        // so subscribing has to be done manually inside the app.
        html += `<a class="ntfy-btn" href="${NTFY_APP_LINKS.ios}" target="_blank" rel="noopener">
            <span class="en">Get the ntfy app</span><span class="ja">ntfyアプリを入手</span>
        </a>`;
        html += `<div class="settings-note">
            <span class="en">The iOS app can't be deep-linked yet. After installing: open ntfy &rarr; Settings &rarr; Default Server, enter <code>https://${NTFY_SERVER}</code>, then tap "+" and subscribe to each topic below by name.</span>
            <span class="ja">iOS版アプリはまだディープリンクに対応していません。インストール後: ntfyを開く → 設定 → デフォルトサーバー に <code>https://${NTFY_SERVER}</code> を入力し、「+」をタップして以下のトピック名を登録してください。</span>
        </div>`;
        for (const t of topics) {
            html += `<div class="ntfy-topic-row">
                <span class="ntfy-topic-name">${t.key}</span>
                <button type="button" class="ntfy-btn ntfy-copy-btn" data-topic="${t.key}">
                    <span class="en">Copy</span><span class="ja">コピー</span>
                </button>
            </div>`;
        }
    } else {
        html += `<div class="settings-note">
            <span class="en">On desktop, ntfy still relies on browser push and has the same reliability limits as above. You can open these topics in a browser instead:</span>
            <span class="ja">デスクトップでは、ntfyもブラウザのプッシュ通知に依存するため上記と同じ制約があります。代わりにブラウザで以下のトピックを開くこともできます：</span>
        </div>`;
        for (const t of topics) {
            html += `<a class="ntfy-btn" href="https://${NTFY_SERVER}/${t.key}" target="_blank" rel="noopener">
                <span class="en">Open: ${t.en}</span><span class="ja">開く: ${t.ja}</span>
            </a>`;
        }
    }
    container.innerHTML = html;
    container.querySelectorAll('.ntfy-copy-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            navigator.clipboard?.writeText(btn.dataset.topic).catch(() => {});
        });
    });
}

function toggleNtfyEnabled() {
    const checkbox = document.getElementById('tog-ntfy-enabled');
    if (!checkbox) return;
    localStorage.setItem(NTFY_ENABLED_KEY, checkbox.checked ? '1' : '0');
    updateNtfyUI();
}

function updateNtfyUI() {
    const enabled = localStorage.getItem(NTFY_ENABLED_KEY) === '1';
    const checkbox = document.getElementById('tog-ntfy-enabled');
    if (checkbox) checkbox.checked = enabled;
    const linksContainer = document.getElementById('ntfy-subscribe-links');
    if (linksContainer) {
        linksContainer.style.display = enabled ? '' : 'none';
        if (enabled) renderNtfySubscribeLinks();
    }
    const warning = document.getElementById('ntfy-onesignal-warning');
    if (!warning) return;
    let oneSignalOptedIn = false;
    if (window.OneSignal) {
        try { oneSignalOptedIn = !!window.OneSignal.User.PushSubscription.optedIn; } catch (e) { /* not ready yet */ }
    }
    warning.style.display = (enabled && oneSignalOptedIn) ? '' : 'none';
}

async function toggleNotifications() {
    const OneSignal = window.OneSignal;
    const checkbox = document.getElementById('tog-notif-enabled');
    if (!OneSignal) {
        if (checkbox) checkbox.checked = false;
        return;
    }
    try {
        if (checkbox.checked) {
            if (OneSignal.Notifications.permission !== true) {
                await OneSignal.Notifications.requestPermission();
            }
            if (OneSignal.Notifications.permission === true) {
                await OneSignal.User.PushSubscription.optIn();
                OneSignal.User.setLanguage(getNotificationLanguage());
            } else {
                alert(currentLanguage === 'ja'
                    ? '通知が許可されていません。ブラウザの設定を確認してください。'
                    : 'Notification permission was not granted. Please check your browser settings.');
            }
        } else {
            await OneSignal.User.PushSubscription.optOut();
        }
    } catch (error) {
        console.error('Failed to toggle notifications:', error);
    }
    await refreshNotificationUI();
}

async function toggleNotifTag(tagKey, checkboxId) {
    const OneSignal = window.OneSignal;
    const checkbox = document.getElementById(checkboxId);
    if (!OneSignal || !checkbox) return;
    try {
        if (checkbox.checked) {
            await OneSignal.User.addTag(tagKey, 'subscribed');
        } else {
            await OneSignal.User.removeTag(tagKey);
        }
    } catch (error) {
        console.error(`Failed to update '${tagKey}' subscription:`, error);
        checkbox.checked = !checkbox.checked;
    }
}

function toggleAlertsTag() {
    return toggleNotifTag('alerts', 'tog-notif-alerts');
}

function toggleForecastsTag() {
    return toggleNotifTag('forecasts', 'tog-notif-forecasts');
}

window.OneSignalDeferred = window.OneSignalDeferred || [];
OneSignalDeferred.push(async function(OneSignal) {
    window.OneSignal = OneSignal;
    updateNotifLanguageUI(getNotificationLanguage());
    OneSignal.User.setLanguage(getNotificationLanguage());
    await refreshNotificationUI();
    OneSignal.User.PushSubscription.addEventListener('change', refreshNotificationUI);
    OneSignal.Notifications.addEventListener('permissionChange', refreshNotificationUI);
});

updateNtfyUI();

document.getElementById('bottom-version').textContent = '– ' + VERSION;

// ── Audio ─────────────────────────────────────────────────────────────
const sndNewQuake       = document.getElementById('snd-new-quake');
const sndNewInfo        = document.getElementById('snd-new-info');
const sndNewReport      = document.getElementById('snd-new-report');
const sndNewTsunami     = document.getElementById('snd-new-tsunami');
const sndTsunamiWarning = document.getElementById('snd-tsunami-warning');

function playSound(audio) {
    if (replayActive) return; // never honk live alert sounds while showing historical data
    audio.currentTime = 0;
    audio.play().catch(() => {});
}

let muteEewAlert       = false;
let muteEewReport      = false;
let muteReport         = false;
let muteTsunamiWarning = false;
let muteTsunamiAdvisory = false;

function toggleSettings() {
    document.getElementById('settings-popup').classList.toggle('visible');
}
document.addEventListener('click', e => {
    const popup = document.getElementById('settings-popup');
    const btn   = document.getElementById('settings-btn');
    if (popup.classList.contains('visible') && !popup.contains(e.target) && e.target !== btn) {
        popup.classList.remove('visible');
    }
});

// ── Intensity helpers ────────────────────────────────────────────────
const INT_COLORS = {
    '0':'#E0E0E0',
    '1':'#A0D8EF','2':'#0000FF','3':'#00CC00','4':'#FFFF00',
    '5-':'#FF9900','5+':'#FF6600','6-':'#FF0000','6+':'#A50000','7':'#800080'
};
const INT_RANK = {'0':0,'1':1,'2':2,'3':3,'4':4,'5-':5,'5+':6,'6-':7,'6+':8,'7':9};
const INT_TEXT_DARK = new Set(['0','1','4','5-']);

function intColor(v) { return INT_COLORS[v] || '#555'; }
function intRank(v)  { return INT_RANK[v]  ?? -1; }
function intTextColor(v) { return INT_TEXT_DARK.has(v) ? '#111' : '#fff'; }

// ── LPGM (long-period ground motion) helpers ──────────────────────────
// JMA's 長周期地震動階級 runs 1–4. Class 0 means "no appreciable long-period
// motion", which is what the vast majority of telegrams carry, so it is treated
// as "nothing to show" rather than rendered as a zero.
const LPGM_COLORS = { '1':'#FFE082', '2':'#FFB300', '3':'#FF6600', '4':'#D32F2F' };
const LPGM_TEXT_DARK = new Set(['1','2']);

// Normalizes max_lpgm (arrives as a string or a number depending on telegram)
// to a displayable class string, or null when there is nothing worth showing.
function lpgmClass(v) {
    if (v == null) return null;
    const s = String(v).trim();
    return LPGM_COLORS[s] ? s : null;
}
function lpgmColor(v)     { return LPGM_COLORS[v] || '#555'; }
function lpgmTextColor(v) { return LPGM_TEXT_DARK.has(v) ? '#111' : '#fff'; }

// ── PGA helpers ───────────────────────────────────────────────────────
function pgaColor(gal) {
    if (gal == null) return '#555';
    if (gal >= 1000) return '#330000';
    if (gal >= 500)  return '#550000';
    if (gal >= 200)  return '#880000';
    if (gal >= 100)  return '#cc0000';
    if (gal >= 50)   return '#ff2200';
    if (gal >= 20)   return '#ff6600';
    if (gal >= 10)   return '#ffaa00';
    if (gal >= 5)    return '#ffff00';
    if (gal >= 2)    return '#66ff00';
    if (gal >= 1)    return '#00ff99';
    if (gal >= 0.5)  return '#00ffee';
    if (gal >= 0.2)  return '#00bbff';
    if (gal >= 0.1)  return '#0088ff';
    if (gal >= 0.05) return '#0033ff';
    if (gal >= 0.02) return '#001177';
    return '#111133';
}
function pgaTextColor(gal) {
    return (gal != null && gal >= 2 && gal < 50) ? '#111' : '#fff';
}
function formatPga(gal) {
    if (gal == null) return '–';
    if (gal >= 1000) return '>999 gal';
    if (gal >= 10) return Math.round(gal) + ' gal';
    if (gal >= 1)  return gal.toFixed(1) + ' gal';
    return gal.toFixed(2) + ' gal';
}

// ── Map init ─────────────────────────────────────────────────────────
const DEFAULT_CENTER = [38.5, 138.5];
const DEFAULT_ZOOM   = (window.innerWidth < window.innerHeight && window.innerWidth < 600) ? 5 : 6;
const QUAKE_ZOOM     = 7;
const RYUKYU_CENTER  = [26.5, 128.0];
const RYUKYU_ZOOM    = 7;
// Ryukyu Islands bounding box (Okinawa chain, SW of Japan main islands)
function isRyukyuCoord(lat, lon) {
    return lat >= 24 && lat <= 29.5 && lon >= 122 && lon <= 132;
}
let ryukyuEpicentre = false;
let ryukyuStations  = false;
function hasRyukyuActivity() { return ryukyuEpicentre || ryukyuStations; }
let niedZoomActive = false;
let niedZoomCoords = null;
const _niedLog = []; // {time: ms, hasPga: bool}
let _niedEverReceived = false;
let _niedWarningActive = false;
function _evalNiedWarning() {
    if (!_niedEverReceived) return;
    const el = document.getElementById('obs-nied-warning');
    if (replayActive) {
        if (el) el.style.display = 'none';
        return;
    }
    const now = Date.now();
    while (_niedLog.length && now - _niedLog[0].time > 8000) _niedLog.shift();
    if (!el) return;
    const lastUpdate = _niedLog.length ? _niedLog[_niedLog.length - 1].time : 0;
    const hasGap = now - lastUpdate > 5000;
    const recentEntries = _niedLog.slice(-5);
    const sustainedMissingPga = recentEntries.length >= 5 && recentEntries.every(e => !e.hasPga);
    const canClear = !hasGap && _niedLog.length >= 5 && _niedLog.slice(-5).every(e => e.hasPga);
    if (hasGap || sustainedMissingPga) _niedWarningActive = true;
    else if (_niedWarningActive && canClear) _niedWarningActive = false;
    el.style.display = _niedWarningActive ? 'block' : 'none';
}
setInterval(_evalNiedWarning, 1000);

const map = L.map('map', { center: DEFAULT_CENTER, zoom: DEFAULT_ZOOM, zoomControl: false, preferCanvas: true });
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; <a href="https://openstreetmap.org/">OSM</a>',
    subdomains: 'abcd', maxZoom: 19
}).addTo(map);
map.createPane('japanLabels');
map.getPane('japanLabels').style.zIndex = 250;
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png', {
    subdomains: 'abcd', maxZoom: 19,
    pane: 'japanLabels',
    bounds: [[24, 122], [46, 147]]
}).addTo(map);
// Polygon tracing Japan's western extent, stepping north-east to follow
// the Japan Sea coast, excluding Korea, Vladivostok, and Manchuria.
const JAPAN_CLIP_POLY = [
    [24, 122],  // SW Ryukyu
    [31, 122],  // NW Ryukyu
    [31, 128.5],  // Ryukyu-Kyushu junction (includes Goto islands at 128.84°E)
    [34.5, 128.5], // includes Tsushima (129.3°E) and Iki (129.7°E)
    [35, 131],  // step east past Korea (Busan 129.08°E excluded)
    [36, 132],  // W Honshu south (Shimane coast)
    [38, 135],  // W Honshu central (Niigata, Sea of Japan)
    [40, 138],  // W Honshu north (Akita, Sea of Japan)
    [42, 139],  // Honshu-Hokkaido junction
    [44, 141],  // SW Hokkaido
    [46, 142],  // NW Hokkaido
    [46, 147],  // NE Hokkaido
    [24, 147],  // SE corner
];
function updateJapanLabelClip() {
    const pane = map.getPane('japanLabels');
    const pts = JAPAN_CLIP_POLY.map(([lat, lon]) => {
        const p = map.latLngToLayerPoint([lat, lon]);
        return `${p.x}px ${p.y}px`;
    });
    pane.style.clipPath = `polygon(${pts.join(',')})`;
}
updateJapanLabelClip();
map.on('zoomend moveend', updateJapanLabelClip);
L.control.zoom({ position: 'bottomright' }).addTo(map);

// ── Layer state ───────────────────────────────────────────────────────
let regionLayer   = null;
const epicenterMarks = new Map(); // key → Leaflet marker; EEW keys are String(origin_time), past quake uses '__past__'
const activeEews     = new Map(); // key → EEW data object
const seenEewOrigins    = new Set(); // origin_times we've already sounded for
// canonical quake key (event_id/ctt, or a time-matched stand-in) → { ts, fp } of the info
// last announced with new_info.mp3, shared across the WQ (live telegram) and JMA history
// sources so the same quake doesn't chime twice just because a second source reported it.
const quakeInfoSoundState = new Map();
let hasPastQuakeData = false;
let activePastQuakeEventId = null;
let activePastQuakeTime = null;
let activePastQuakeAreaIntensities = null;
// True once a report carrying per-station data (VXSE53) has been seen for the
// current past quake, so later follow-up reports (e.g. a subsequent VXSE52
// lacking area_intensities) keep refreshing the already-revealed points instead
// of re-hiding them.
let pastQuakeStationsShown = false;

// ── Epicentre pane ──────────────────────────────────────────────────────
// Any Leaflet pane (markerPane, and this one) lives inside Leaflet's
// internal _mapPane, which gets a CSS `transform` for panning — that
// transform makes _mapPane its own stacking context, so z-index set on
// panes *inside* it can never out-rank a sibling of _mapPane that has an
// explicit z-index (like the raw <canvas> station overlays below, which
// are appended straight to the map container so their manual
// containerPoint-based redraw logic isn't double-transformed by panning).
// So the epicentre *icon* marker is rendered as a plain DOM element in
// epicenterLayer (a sibling of _mapPane, see below) instead of a Leaflet
// marker, guaranteeing it stacks above those canvases. The PLUM dashed
// ring is a true geographic circle (radius in metres) that's harder to
// reproduce by hand, so it stays a Leaflet vector in this pane.
map.createPane('epicenterPane');
map.getPane('epicenterPane').style.zIndex = 620;

const epicenterLayer = document.createElement('div');
epicenterLayer.style.cssText = 'position:absolute;top:0;left:0;z-index:500;';
map.getContainer().appendChild(epicenterLayer);

function positionEpicenterMark(mark) {
    const pt = map.latLngToContainerPoint([mark.lat, mark.lon]);
    mark.el.style.transform = `translate(${pt.x - 13}px, ${pt.y - 13}px)`;
}
map.on('move zoom resize', () => {
    for (const mark of epicenterMarks.values()) {
        if (mark && mark._isDomMark) positionEpicenterMark(mark);
    }
});

function removeEpicenterMark(key) {
    const mark = epicenterMarks.get(key);
    if (!mark) return;
    if (mark._isDomMark) mark.el.remove();
    else map.removeLayer(mark);
    epicenterMarks.delete(key);
}

// ── Seismic wave pane ──────────────────────────────────────────────────
map.createPane('wavesPane');
map.getPane('wavesPane').style.zIndex = 300;
map.getPane('wavesPane').style.pointerEvents = 'none';

// Vector layers (our wave circles) get projected using the map's live pixel
// origin, but the SVG/Canvas renderer container is repositioned via a CSS
// transform that's only rebased on 'moveend' — so creating/moving a circle
// mid-flyTo double-applies the pan/zoom delta and it renders far from its
// true position until the flight (or chain of flights, if reports keep
// re-triggering flyTo) finally settles. Track that window so wave rendering
// can pause until it's safe. flyTo/flyToBounds fire 'movestart' immediately
// but only fire 'moveend' once the animation truly completes (repeated
// flyTo calls restart it via map._stop(), which doesn't fire 'moveend').
let mapIsMoving = false;
map.on('movestart', () => { mapIsMoving = true; });
map.on('moveend', () => { mapIsMoving = false; });

// ── Tsunami region colouring ──────────────────────────────────────────
let tsunamiLayer = null;
const tsunamiRegionCentroids = {}; // region code → [lat, lon], from tsunami-regions.geojson coastlines

function drawTsunamiCoastlines(data) {
    if (!tsunamiLayer) return;
    const codes      = data.region_codes || [];
    const kindCodes  = data.kind_codes || [];
    const colorMap = {};
    codes.forEach((code, i) => {
        // A lifted coast (50/60/00) has nothing in force and must not stay coloured.
        // The backend already drops these; replayed history predates that.
        if (TSUNAMI_LIFTED_KINDS.has(kindCodes[i])) return;
        const col = kindColor(kindCodes[i]);
        if (col && code) colorMap[String(code)] = col;
    });
    tsunamiLayer.setStyle(feat => {
        const col = colorMap[String(feat.properties.code)];
        return col
            ? { color: col, weight: 4, opacity: 0.9, fillColor: col, fillOpacity: 0.18 }
            : { color: 'transparent', weight: 0, fillOpacity: 0 };
    });
}

function clearTsunamiCoastlines() {
    if (tsunamiLayer) tsunamiLayer.setStyle({ color: 'transparent', weight: 0, fillOpacity: 0 });
}

// ── Load regions GeoJSONs ─────────────────────────────────────────────
let _regionFeatureBounds = null; // [{f, bbox:[minLon,minLat,maxLon,maxLat]}] for point-in-region lookups

function _geometryBBox(geometry) {
    let minLon = Infinity, minLat = Infinity, maxLon = -Infinity, maxLat = -Infinity;
    const rings = geometry.type === 'MultiPolygon' ? geometry.coordinates.flat() : geometry.coordinates;
    rings.forEach(ring => ring.forEach(([lon, lat]) => {
        if (lon < minLon) minLon = lon;
        if (lon > maxLon) maxLon = lon;
        if (lat < minLat) minLat = lat;
        if (lat > maxLat) maxLat = lat;
    }));
    return [minLon, minLat, maxLon, maxLat];
}

function _pointInRing(lon, lat, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
        const [xi, yi] = ring[i], [xj, yj] = ring[j];
        const intersect = ((yi > lat) !== (yj > lat)) &&
            (lon < (xj - xi) * (lat - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
    }
    return inside;
}

function _pointInPolygonCoords(lon, lat, polygon) {
    if (!polygon.length || !_pointInRing(lon, lat, polygon[0])) return false;
    for (let i = 1; i < polygon.length; i++) if (_pointInRing(lon, lat, polygon[i])) return false;
    return true;
}

function _pointInGeometry(lon, lat, geometry) {
    if (geometry.type === 'Polygon') return _pointInPolygonCoords(lon, lat, geometry.coordinates);
    if (geometry.type === 'MultiPolygon') return geometry.coordinates.some(poly => _pointInPolygonCoords(lon, lat, poly));
    return false;
}

function findRegionCodeForPoint(lat, lon) {
    if (!_regionFeatureBounds) return null;
    for (const { f, bbox } of _regionFeatureBounds) {
        if (lon < bbox[0] || lon > bbox[2] || lat < bbox[1] || lat > bbox[3]) continue;
        if (_pointInGeometry(lon, lat, f.geometry)) return f.properties.code;
    }
    return null;
}

// Fallback region colouring for reports with no area_intensities (e.g. epicenter-only
// telegrams): bucket each station into its containing region and take the max per region.
function computeRegionIntensitiesFromStations(stations) {
    if (!_regionFeatureBounds || !stations || !stations.length) return [];
    const maxByCode = {};
    stations.forEach(st => {
        if (st.int == null || st.lat == null || st.lon == null) return;
        if (intRank(st.int) < 0) return;
        const code = findRegionCodeForPoint(st.lat, st.lon);
        if (!code) return;
        if (!(code in maxByCode) || intRank(st.int) > intRank(maxByCode[code])) maxByCode[code] = st.int;
    });
    return Object.entries(maxByCode).map(([code, max_int]) => ({ code, max_int }));
}

fetch('/japan-regions.geojson')
    .then(r => r.json())
    .then(gj => {
        _regionFeatureBounds = gj.features.map(f => ({ f, bbox: _geometryBBox(f.geometry) }));
        regionLayer = L.geoJSON(gj, {
            style: { color: '#333', weight: 0.5, fillColor: '#1c1c1c', fillOpacity: 0.65 }
        }).addTo(map);
    });

fetch('/tsunami-regions.geojson')
    .then(r => r.json())
    .then(gj => {
        tsunamiLayer = L.geoJSON(gj, {
            style: { color: 'transparent', weight: 0, fillOpacity: 0 }
        }).addTo(map);
        tsunamiLayer.eachLayer(layer => {
            const code = layer.feature && layer.feature.properties && layer.feature.properties.code;
            if (code != null && layer.getBounds) {
                const c = layer.getBounds().getCenter();
                tsunamiRegionCentroids[String(code)] = [c.lat, c.lng];
            }
        });
    });

// ── Tsunami region pin (clicking a forecast/observation row in the panel) ──
const TSUNAMI_REGION_ZOOM = 8;
let tsunamiRegionMark = null;
const TSUNAMI_PIN_HTML = '<div style="font-size:22px;width:24px;height:24px;display:flex;align-items:center;justify-content:center;color:#4fc3f7;text-shadow:0 0 6px #000,0 0 2px #000;line-height:1">📍</div>';
const TSUNAMI_PIN_ICON = () => L.divIcon({ html: TSUNAMI_PIN_HTML, className: '', iconSize: [24, 24], iconAnchor: [12, 22] });

function focusTsunamiRegion(code) {
    const centroid = tsunamiRegionCentroids[String(code)];
    if (!centroid) return;
    clearTsunamiRegionMark();
    tsunamiRegionMark = L.marker(centroid, { icon: TSUNAMI_PIN_ICON(), zIndexOffset: 1500 }).addTo(map);
    map.flyTo(centroid, Math.max(map.getZoom(), TSUNAMI_REGION_ZOOM), { animate: true, duration: 1 });
}

function clearTsunamiRegionMark() {
    if (tsunamiRegionMark) { map.removeLayer(tsunamiRegionMark); tsunamiRegionMark = null; }
}

map.on('click', clearTsunamiRegionMark);

document.getElementById('tsunami-panel').addEventListener('click', e => {
    const row = e.target.closest('.tsun-row[data-tsun-code]');
    if (!row) return;
    const code = row.getAttribute('data-tsun-code');
    if (code) focusTsunamiRegion(code);
});

// ── Region colouring ──────────────────────────────────────────────────
let _eewAreaIntensities = [];

function renderRegionColors() {
    if (!regionLayer) return;
    const eewMap = {};
    _eewAreaIntensities.forEach(a => { if (a.code) eewMap[String(a.code)] = a.max_int; });
    regionLayer.setStyle(feat => {
        const eewInt = eewMap[String(feat.properties.code)];
        return {
            fillColor:   eewInt ? intColor(eewInt) : '#1c1c1c',
            fillOpacity: eewInt ? 0.78 : 0.60,
            color:       eewInt ? '#111' : '#333',
            weight:      eewInt ? 0.4 : 0.5,
        };
    });
    updateMapKey();
}

function updateRegionColors(areaIntensities) {
    _eewAreaIntensities = areaIntensities;
    _maxEewIntRank = -1;
    areaIntensities.forEach(a => { const r = intRank(a.max_int); if (r > _maxEewIntRank) _maxEewIntRank = r; });
    renderRegionColors();
}

function resetRegionColors() {
    _eewAreaIntensities = [];
    _maxEewIntRank = -1;
    renderRegionColors();
}

// ── Adaptive map key / legend ──────────────────────────────────────────
// Shows intensity levels from 1 up to the highest currently shown on the
// map, and tsunami levels from "Slight sea level change" up to the highest
// currently active warning level.
const INT_LEVELS = ['1','2','3','4','5-','5+','6-','6+','7'];
const TSUNAMI_LEVELS = ['Slight sea level change','Advisory','Warning','Major Warning'];
const TSUNAMI_LEVEL_LABELS_EN = {
    'Slight sea level change': 'Slight Sea Level Change',
    'Advisory':     'Tsunami Advisory',
    'Warning':      'Tsunami Warning',
    'Major Warning':'Major Tsunami Warning',
};
const TSUNAMI_LEVEL_LABELS_JA = {
    'Slight sea level change': '若干の海面変動',
    'Advisory':     '津波注意報',
    'Warning':      '津波警報',
    'Major Warning':'大津波警報',
};

// Max intensity ranks are cached at data-change time (updateRegionColors,
// _setStationData, setJmaStations) so this stays O(1) — updateMapKey runs on
// every canvas redraw, i.e. every animation frame during a pan.
let _maxEewIntRank = -1;
let _maxLiveStationRank = -1;
let _maxJmaStationRank = -1;

function currentMaxIntRank() {
    let max = _maxEewIntRank;
    if (!liveStationsHidden && _maxLiveStationRank > max) max = _maxLiveStationRank;
    if (jmaStationsVisible && _maxJmaStationRank > max) max = _maxJmaStationRank;
    return max;
}

function currentMaxTsunamiRank() {
    if (!activeTsunamiData) return -1;
    return TSUNAMI_LEVELS.indexOf(activeTsunamiData.warning_level);
}

let _lastMapKeyState = null;

function updateMapKey() {
    const el = document.getElementById('map-key');
    if (!el) return;

    const maxIntRank = currentMaxIntRank();
    const maxTsuRank = currentMaxTsunamiRank();
    const state = `${maxIntRank}|${maxTsuRank}|${currentLanguage}`;
    if (state === _lastMapKeyState) return;
    _lastMapKeyState = state;

    let html = '';
    if (maxIntRank >= 1) {
        html += '<div class="key-section">';
        html += '<div class="key-title"><span class="en">Intensity</span><span class="ja">震度</span></div>';
        for (let r = 1; r <= maxIntRank; r++) {
            const lvl = INT_LEVELS[r - 1];
            html += `<div class="key-row"><span class="key-swatch" style="background:${intColor(lvl)};color:${intTextColor(lvl)}">${lvl}</span></div>`;
        }
        html += '</div>';
    }

    if (maxTsuRank >= 0) {
        html += '<div class="key-section">';
        html += '<div class="key-title"><span class="en">Tsunami</span><span class="ja">津波</span></div>';
        for (let r = 0; r <= maxTsuRank; r++) {
            const lvl = TSUNAMI_LEVELS[r];
            const c = tsunamiLevelColor(lvl), tc = tsunamiLevelTextColor(lvl);
            html += `<div class="key-row"><span class="key-swatch" style="background:${c};color:${tc}"></span>`
                  + `<span class="key-label"><span class="en">${TSUNAMI_LEVEL_LABELS_EN[lvl]}</span><span class="ja">${TSUNAMI_LEVEL_LABELS_JA[lvl]}</span></span></div>`;
        }
        html += '</div>';
    }

    el.innerHTML = html;
    el.classList.toggle('visible', html !== '');
    el.title = currentLanguage === 'ja' ? '凡例' : 'Map key';
}

// ── NIED station dots (canvas overlay) ───────────────────────────────
let lastStations = [];
// Pre-split/sorted views of lastStations, recomputed only when the data
// changes (in updateStations) rather than on every redraw — redraws also
// fire on every map move frame, where re-sorting would be wasted work.
let _zeroStations = [];
let _labeledStations = [];

// Mirrors of the true live station state, kept up to date even while replay
// is active (when live messages are diverted into replayLiveBuffer instead of
// reaching displayData). Used to restore the live view exactly on exitReplay,
// since the buffered messages may be diffs-only and would otherwise leave
// unchanged stations missing.
const liveStationCache = new Map();
const liveSnetCache = new Map();

// Mirrors of the true live JMA quake-history list and WS-sourced sidebar
// entries, kept up to date even while replay is active. Replay playback
// overwrites lastQuakeData/wsQuakeBuffer with historical snapshots, and
// jma_history is only re-broadcast when a new quake arrives — so without
// these mirrors the sidebar could stay stuck on stale replay data
// indefinitely after exitReplay.
let liveQuakeHistory = null;
let liveWsQuakeBuffer = null;

const stationCanvas = document.createElement('canvas');
stationCanvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:450;';
map.getContainer().appendChild(stationCanvas);

function zeroOpacity(rawInt) {
    const t = Math.max(0, Math.min(1, (rawInt + 3) / 3.5));
    return 0.12 + t * 0.33;
}

function resizeStationCanvas() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    stationCanvas.width  = sz.x * dpr;
    stationCanvas.height = sz.y * dpr;
    stationCanvas.style.width  = sz.x + 'px';
    stationCanvas.style.height = sz.y + 'px';
}

function redrawStations() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const ctx = stationCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, sz.x, sz.y);
    updateMapKey();
    if (liveStationsHidden || !lastStations.length) return;

    const z    = map.getZoom();
    const base = Math.max(6, Math.min(20, Math.round(z * 2 - 2)));
    const zeroDiam = Math.max(4, base - 3);

    // draw zero/null dots first (background layer), then labeled dots sorted
    // ascending by intensity. Both groups are precomputed in updateStations.
    for (const [group, isZero] of [[_zeroStations, true], [_labeledStations, false]]) {
        group.forEach(st => {
            const diam   = isZero ? zeroDiam : base;
            const pt     = map.latLngToContainerPoint(st._ll || [st.lat, st.lon]);
            // Skip dots outside the viewport — when zoomed in (i.e. during an
            // actual quake) most of the ~2000 stations are off-screen.
            if (pt.x < -diam || pt.y < -diam || pt.x > sz.x + diam || pt.y > sz.y + diam) return;
            const r      = diam / 2;
            const opacity = isZero ? zeroOpacity(st.raw ?? -3) : 1;
            const lbl    = isZero ? '' : (st.int || '');

            ctx.globalAlpha = opacity;
            ctx.fillStyle = intColor(st.int);
            if (boreholeVisible && st.borehole_int != null) {
                const bRank = intRank(st.borehole_int);
                const bw = bRank >= 1 ? 1.5 : 0;
                ctx.beginPath();
                ctx.rect(pt.x - r, pt.y - r, diam, diam);
                ctx.fill();
                if (bw > 0) {
                    // Inset stroke so the border doesn't grow the shape
                    ctx.save();
                    ctx.clip();
                    ctx.strokeStyle = intColor(st.borehole_int);
                    ctx.lineWidth = bw * 2;
                    ctx.stroke();
                    ctx.restore();
                }
            } else {
                ctx.beginPath();
                ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
                ctx.fill();
                ctx.strokeStyle = 'rgba(0,0,0,0.3)';
                ctx.lineWidth = 0.8;
                ctx.stroke();
            }

            if (lbl) {
                ctx.globalAlpha = 1;
                const fs = lbl.length > 1 ? diam * 0.5 : diam * 0.68;
                ctx.font = `700 ${fs}px sans-serif`;
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillStyle = intTextColor(st.int);
                ctx.fillText(lbl, pt.x, pt.y);
            }
        });
    }
    ctx.globalAlpha = 1;
}

// Coalesce redraws to at most one per animation frame. Map 'move' fires every
// frame during a pan, and WS station updates arrive ~3×/s; without this the two
// sources stack redundant full-canvas redraws on the same frame.
let _redrawStationsPending = false;
function scheduleRedrawStations() {
    if (_redrawStationsPending) return;
    _redrawStationsPending = true;
    requestAnimationFrame(() => { _redrawStationsPending = false; redrawStations(); });
}

resizeStationCanvas();
// Only the canvas backing buffer reallocation must run on an actual resize;
// pans/zooms just need a redraw at the new projection.
map.on('resize', () => { resizeStationCanvas(); scheduleRedrawStations(); dismissStationPopup && dismissStationPopup(); });
map.on('move zoom', () => { scheduleRedrawStations(); dismissStationPopup && dismissStationPopup(); });

// ── Borehole toggle ───────────────────────────────────────────────────
let boreholeVisible = true;

function toggleBoreholeVisible() {
    boreholeVisible = !boreholeVisible;
    const cb = document.getElementById('tog-borehole');
    if (cb) cb.checked = boreholeVisible;
    redrawStations();
}

// ── S-net seafloor station dots (canvas overlay) ─────────────────────
let lastSnetStations = [];
let _snetSorted = []; // lastSnetStations sorted by intensity; recomputed on data change only
let snetVisible = true;

function toggleSnetVisible() {
    snetVisible = !snetVisible;
    snetCanvas.style.display = snetVisible ? '' : 'none';
    const cb = document.getElementById('tog-snet');
    if (cb) cb.checked = snetVisible;
}

const snetCanvas = document.createElement('canvas');
snetCanvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:449;';
map.getContainer().appendChild(snetCanvas);

function resizeSnetCanvas() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    snetCanvas.width  = sz.x * dpr;
    snetCanvas.height = sz.y * dpr;
    snetCanvas.style.width  = sz.x + 'px';
    snetCanvas.style.height = sz.y + 'px';
}

function redrawSnetStations() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const ctx = snetCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, sz.x, sz.y);
    if (!lastSnetStations.length || liveStationsHidden) return;
    const z    = map.getZoom();
    const base = Math.max(5, Math.min(16, Math.round(z * 2 - 3)));
    const r    = base / 2;
    _snetSorted.forEach(st => {
        const pt = map.latLngToContainerPoint(st._ll || [st.lat, st.lon]);
        if (pt.x < -r || pt.y < -r || pt.x > sz.x + r || pt.y > sz.y + r) return;
        // Draw as a diamond to distinguish seafloor stations from land stations
        ctx.globalAlpha = st.int === '0' ? 0.15 : 0.35;
        ctx.beginPath();
        ctx.moveTo(pt.x,     pt.y - r);
        ctx.lineTo(pt.x + r, pt.y    );
        ctx.lineTo(pt.x,     pt.y + r);
        ctx.lineTo(pt.x - r, pt.y    );
        ctx.closePath();
        ctx.fillStyle = intColor(st.int);
        ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.4)';
        ctx.lineWidth = 0.8;
        ctx.stroke();
    });
    ctx.globalAlpha = 1;
}

let _snetByCode = new Map(); // persistent code→station index, as for NIED stations

function updateSnetStations(stations) {
    _snetByCode = new Map(stations.map(s => [s.code, s]));
    _setSnetData(stations);
}

function _setSnetData(stations) {
    lastSnetStations = stations;
    for (const st of stations) if (!st._ll) st._ll = L.latLng(st.lat, st.lon);
    _snetSorted = [...stations].sort((a, b) => intRank(a.int) - intRank(b.int));
    scheduleRedrawSnetStations();
}

function applySnetDiff(changed, removed) {
    for (const code of (removed || [])) _snetByCode.delete(code);
    for (const entry of (changed || [])) _snetByCode.set(entry.code, entry);
    _setSnetData([..._snetByCode.values()]);
}

let _redrawSnetPending = false;
function scheduleRedrawSnetStations() {
    if (_redrawSnetPending) return;
    _redrawSnetPending = true;
    requestAnimationFrame(() => { _redrawSnetPending = false; redrawSnetStations(); });
}

resizeSnetCanvas();
map.on('resize', () => { resizeSnetCanvas(); scheduleRedrawSnetStations(); });
map.on('move zoom', scheduleRedrawSnetStations);

// ── JMA station dots (past quake point data) ─────────────────────────
let jmaStations = [];
let _jmaSorted = []; // jmaStations sorted by intensity; recomputed on data change only
function setJmaStations(arr) {
    jmaStations = arr;
    for (const st of arr) if (!st._ll) st._ll = L.latLng(st.lat, st.lon);
    _jmaSorted = [...arr].sort((a, b) => intRank(a.int) - intRank(b.int));
    _maxJmaStationRank = _jmaSorted.length
        ? intRank(_jmaSorted[_jmaSorted.length - 1].int) : -1;
}
let jmaStationsEventId = null;
let jmaStationsVisible = false;
let jmaPointsIndex = []; // [{ts, lat, lon, st}] — events with epicenter/station data
let jmaSidebarActiveTs = null; // origin time (UTC unix sec) of sidebar-selected quake
let _jmaSidebarClearTimer = null;
let liveStationsHidden = false; // true only when user manually opens a past-quake map view

const jmaCanvas = document.createElement('canvas');
jmaCanvas.style.cssText = 'position:absolute;top:0;left:0;pointer-events:none;z-index:451;';
map.getContainer().appendChild(jmaCanvas);

function resizeJmaCanvas() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    jmaCanvas.width  = sz.x * dpr;
    jmaCanvas.height = sz.y * dpr;
    jmaCanvas.style.width  = sz.x + 'px';
    jmaCanvas.style.height = sz.y + 'px';
}

function redrawJmaStations() {
    const sz  = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    const ctx = jmaCanvas.getContext('2d');
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, sz.x, sz.y);
    updateMapKey();
    if (!jmaStationsVisible || !jmaStations.length) return;
    const z    = map.getZoom();
    const base = Math.max(7, Math.min(22, Math.round(z * 2)));
    const r    = base / 2;
    _jmaSorted.forEach(st => {
        const pt = map.latLngToContainerPoint(st._ll || [st.lat, st.lon]);
        if (pt.x < -base || pt.y < -base || pt.x > sz.x + base || pt.y > sz.y + base) return;
        ctx.beginPath();
        ctx.arc(pt.x, pt.y, r, 0, Math.PI * 2);
        ctx.fillStyle = intColor(st.int);
        ctx.fill();
        ctx.strokeStyle = 'rgba(0,0,0,0.7)';
        ctx.lineWidth = 1;
        ctx.stroke();
        if (base >= 12) {
            const lbl = st.int || '';
            const fs = lbl.length > 1 ? base * 0.5 : base * 0.68;
            ctx.font = `700 ${fs}px sans-serif`;
            ctx.textAlign = 'center';
            ctx.textBaseline = 'middle';
            ctx.fillStyle = intTextColor(st.int);
            ctx.fillText(lbl, pt.x, pt.y);
        }
    });
}

let _redrawJmaPending = false;
function scheduleRedrawJmaStations() {
    if (_redrawJmaPending) return;
    _redrawJmaPending = true;
    requestAnimationFrame(() => { _redrawJmaPending = false; redrawJmaStations(); });
}

resizeJmaCanvas();
map.on('resize', () => { resizeJmaCanvas(); scheduleRedrawJmaStations(); });
map.on('move zoom', scheduleRedrawJmaStations);

async function fetchJmaPoints(eventId) {
    if (!eventId) return;
    if (jmaStationsEventId === eventId) {
        jmaStationsVisible = !jmaStationsVisible;
        redrawJmaStations();
        return;
    }
    try {
        const resp = await fetch(`/api/quake_points/${encodeURIComponent(eventId)}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.stations || !data.stations.length) return;
        setJmaStations(data.stations);
        jmaStationsEventId = eventId;
        jmaStationsVisible = true;
        resizeJmaCanvas();
        redrawJmaStations();
    } catch(e) { console.warn('JMA points fetch failed', e); }
}

async function fetchJmaPointsNear(isoTime) {
    if (!isoTime) return;
    const ts = Math.round(new Date(isoTime).getTime() / 1000);
    const cacheKey = `near:${ts}`;
    if (jmaStationsEventId === cacheKey) {
        jmaStationsVisible = !jmaStationsVisible;
        redrawJmaStations();
        return;
    }
    try {
        const resp = await fetch(`/api/quake_points_near/${ts}`);
        if (!resp.ok) return;
        const data = await resp.json();
        if (!data.stations || !data.stations.length) return;
        setJmaStations(data.stations);
        jmaStationsEventId = cacheKey;
        jmaStationsVisible = true;
        resizeJmaCanvas();
        redrawJmaStations();
    } catch(e) { console.warn('JMA points near fetch failed', e); }
}

function clearJmaStations() {
    if (_jmaSidebarClearTimer) { clearTimeout(_jmaSidebarClearTimer); _jmaSidebarClearTimer = null; }
    setJmaStations([]);
    jmaStationsEventId = null;
    jmaStationsVisible = false;
    jmaSidebarActiveTs = null;
    if (liveStationsHidden) {
        liveStationsHidden = false;
        document.getElementById('obs-live-hidden').style.display = 'none';
        redrawStations();
    }
    removeEpicenterMark('__jma_pts__');
    redrawJmaStations();
}

function findJmaIndexEntry(originTs) {
    if (!originTs) return null;
    // JMA bosai q.at is minute-truncated, so drift can be up to 59s; pick closest within 90s
    let best = null, bestDiff = Infinity;
    for (const e of jmaPointsIndex) {
        const d = Math.abs(e.ts - originTs);
        if (d <= 90 && d < bestDiff) { bestDiff = d; best = e; }
    }
    return best;
}


async function handleJmaMapBtn(e, originTs, epicLat, epicLon) {
    e.preventDefault();
    e.stopPropagation();
    // Toggle off if already active
    if (jmaSidebarActiveTs === originTs) {
        clearJmaStations();
        removeEpicenterMark('__past__');
        const eewWithAreas = [...activeEews.values()].find(d => d.area_intensities && d.area_intensities.length);
        if (eewWithAreas) {
            updateRegionColors(eewWithAreas.area_intensities);
        } else {
            resetRegionColors();
        }
        renderSidebar(lastQuakeData);
        map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 });
        return;
    }
    // Clear any previous state
    if (_jmaSidebarClearTimer) { clearTimeout(_jmaSidebarClearTimer); _jmaSidebarClearTimer = null; }
    setJmaStations([]);
    jmaStationsEventId = null;
    jmaStationsVisible = false;
    redrawJmaStations();
    removeEpicenterMark('__jma_pts__');
    jmaSidebarActiveTs = originTs;
    liveStationsHidden = true;
    document.getElementById('obs-live-hidden').style.display = 'block';
    document.getElementById('obs-panel').classList.add('visible');
    positionObsPanel();
    redrawStations();
    // Auto-clear after 180s
    _jmaSidebarClearTimer = setTimeout(() => {
        _jmaSidebarClearTimer = null;
        if (jmaSidebarActiveTs === originTs) { clearJmaStations(); renderSidebar(lastQuakeData); map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 }); }
    }, 180000);
    // Fetch station data if available
    const indexEntry = findJmaIndexEntry(originTs);
    if (indexEntry && indexEntry.st) {
        try {
            const resp = await fetch(`/api/quake_points_near/${originTs}`);
            if (resp.ok) {
                const body = await resp.json();
                if (body.stations && body.stations.length) {
                    setJmaStations(body.stations);
                    jmaStationsEventId = `near:${originTs}`;
                    jmaStationsVisible = true;
                    resizeJmaCanvas();
                    redrawJmaStations();
                }
            }
        } catch(err) { console.warn('JMA map btn fetch failed', err); }
    }
    // Epicenter — prefer explicit lat/lon from card, fall back to index entry
    const lat = epicLat ?? indexEntry?.lat ?? null;
    const lon = epicLon ?? indexEntry?.lon ?? null;
    if (lat != null && lon != null) placeMarker('__jma_pts__', lat, lon);
    // Region colors — show if this quake matches the active past quake, else clear
    // any leftover EEW region colouring (the epicentre marker/wave animation of an
    // active EEW are left alone — only the region shading needs to reflect this view).
    if (activePastQuakeAreaIntensities && activePastQuakeTime != null && Math.abs(originTs - activePastQuakeTime) < 90) {
        updateRegionColors(activePastQuakeAreaIntensities);
    } else {
        const computed = computeRegionIntensitiesFromStations(jmaStations);
        if (computed.length) updateRegionColors(computed);
        else resetRegionColors();
    }
    // Auto-zoom
    if (jmaStations.length) {
        const bounds = L.latLngBounds(jmaStations.map(s => [s.lat, s.lon]));
        if (lat != null && lon != null) bounds.extend([lat, lon]);
        map.fitBounds(bounds, { padding: [60, 60], animate: true, duration: 1, maxZoom: 8 });
    } else if (lat != null && lon != null) {
        map.flyTo([lat, lon], QUAKE_ZOOM, { animate: true, duration: 1 });
    }
    renderSidebar(lastQuakeData);
}

// ── Station popup on click ────────────────────────────────────────────
const stationPopup = document.getElementById('station-popup');
let selectedStationCode = null;
let snetDataTime = null; // ms timestamp of last snet update
let _snetAgeInterval = null;

function dismissStationPopup() {
    stationPopup.style.display = 'none';
    selectedStationCode = null;
    if (_snetAgeInterval) { clearInterval(_snetAgeInterval); _snetAgeInterval = null; }
}

function _formatAge(ms) {
    const s = Math.round((Date.now() - ms) / 1000);
    if (currentLanguage === 'ja') {
        if (s < 60) return `${s}秒前`;
        const m = Math.floor(s / 60), r = s % 60;
        return r > 0 ? `${m}分${r}秒前` : `${m}分前`;
    }
    if (s < 60) return `${s}s ago`;
    const m = Math.floor(s / 60), r = s % 60;
    return r > 0 ? `${m}m ${r}s ago` : `${m}m ago`;
}

function showStationPopup(st, px, py, isSnet) {
    document.getElementById('sp-name').textContent = st.code || st.name || '–';
    const intLabel = (currentLanguage === 'ja' ? '震度 ' : 'Int. ') + (st.raw != null ? st.raw : (st.int ?? '–'));
    document.getElementById('sp-int-label').textContent = intLabel;
    document.getElementById('sp-int-bar').style.background = intColor(st.int);
    const pgaRow = document.getElementById('sp-pga-row');
    if (st.pga != null) {
        document.getElementById('sp-pga-label').textContent = formatPga(st.pga);
        document.getElementById('sp-pga-bar').style.background = pgaColor(st.pga);
        pgaRow.style.display = 'flex';
    } else {
        pgaRow.style.display = 'none';
    }
    const boreholeRow = document.getElementById('sp-borehole-row');
    if (boreholeVisible && st.borehole_int != null) {
        const bLabel = (currentLanguage === 'ja' ? '地中 ' : 'Borehole ') + (st.borehole_raw != null ? st.borehole_raw : st.borehole_int);
        document.getElementById('sp-borehole-label').textContent = bLabel;
        document.getElementById('sp-borehole-bar').style.background = intColor(st.borehole_int);
        boreholeRow.style.display = 'flex';
    } else {
        boreholeRow.style.display = 'none';
    }
    const ageRow = document.getElementById('sp-age-row');
    if (_snetAgeInterval) { clearInterval(_snetAgeInterval); _snetAgeInterval = null; }
    if (isSnet && snetDataTime) {
        const prefix = currentLanguage === 'ja' ? '海底観測 · ' : 'Seafloor · ';
        const tip    = currentLanguage === 'ja' ? '海底観測点は約1分ごとに更新されます' : 'Seafloor stations update every ~1 min';
        const updateAge = () => {
            ageRow.innerHTML = `<span title="${tip}" style="text-decoration:underline dotted;text-underline-offset:2px;cursor:help">${prefix + _formatAge(snetDataTime)}</span>`;
        };
        updateAge();
        ageRow.style.display = 'block';
        _snetAgeInterval = setInterval(updateAge, 1000);
    } else {
        ageRow.style.display = 'none';
    }
    stationPopup.style.display = 'block';
    stationPopup.style.left = px + 'px';
    stationPopup.style.top  = py + 'px';
}

map.getContainer().addEventListener('click', function(e) {
    if (e.target.closest('#station-popup')) return;

    const rect = map.getContainer().getBoundingClientRect();
    const mx = e.clientX - rect.left;
    const my = e.clientY - rect.top;
    const z  = map.getZoom();

    // Check S-net stations first (they're below NIED z-index but we check by proximity)
    if (lastSnetStations.length) {
        const snetHitR = Math.max(5, Math.min(16, Math.round(z * 2 - 3))) / 2 + 4;
        let snetClosest = null, snetBest = Infinity;
        lastSnetStations.forEach(st => {
            const pt = map.latLngToContainerPoint(st._ll || [st.lat, st.lon]);
            const d  = Math.hypot(pt.x - mx, pt.y - my);
            if (d < snetHitR && d < snetBest) { snetBest = d; snetClosest = { st, pt }; }
        });
        if (snetClosest) {
            selectedStationCode = snetClosest.st.code;
            showStationPopup(snetClosest.st, snetClosest.pt.x, snetClosest.pt.y, true);
            L.DomEvent.stopPropagation(e);
            return;
        }
    }

    if (!lastStations.length || liveStationsHidden) { dismissStationPopup(); return; }

    const hitR = Math.max(6, Math.min(20, Math.round(z * 2 - 2))) / 2 + 4;
    let closest = null, bestDist = Infinity;
    lastStations.forEach(st => {
        const pt = map.latLngToContainerPoint(st._ll || [st.lat, st.lon]);
        const d  = Math.hypot(pt.x - mx, pt.y - my);
        if (d < hitR && d < bestDist) { bestDist = d; closest = { st, pt }; }
    });

    if (closest) {
        selectedStationCode = closest.st.code;
        showStationPopup(closest.st, closest.pt.x, closest.pt.y, false);
        L.DomEvent.stopPropagation(e);
    } else {
        dismissStationPopup();
    }
});

function getNiedActiveCoords(stations) {
    const high = stations.filter(st => intRank(st.int) >= 2);
    if (high.length > 0) return high.map(st => [st.lat, st.lon]);
    const int1 = stations.filter(st => st.int === '1');
    const clustered = new Set();
    for (let i = 0; i < int1.length; i++) {
        for (let j = i + 1; j < int1.length; j++) {
            const dlat = int1[i].lat - int1[j].lat;
            const dlon = int1[i].lon - int1[j].lon;
            if (Math.hypot(dlat, dlon) < 0.45) { clustered.add(i); clustered.add(j); }
        }
    }
    return clustered.size >= 2 ? [...clustered].map(i => [int1[i].lat, int1[i].lon]) : null;
}

function zoomToNiedStations() {
    if (!niedZoomCoords || niedZoomCoords.length === 0) return;
    if (niedZoomCoords.length === 1) {
        map.flyTo(niedZoomCoords[0], QUAKE_ZOOM, { animate: true, duration: 1 });
    } else {
        map.flyToBounds(L.latLngBounds(niedZoomCoords), { padding: [60, 60], animate: true, duration: 1, maxZoom: QUAKE_ZOOM });
    }
}

function zoomAfterClear() {
    const niedCoords = getNiedActiveCoords(lastStations);
    if (niedCoords && niedCoords.length > 0 && !ryukyuStations && epicenterMarks.size === 0) {
        niedZoomActive = true;
        niedZoomCoords = niedCoords;
        zoomToNiedStations();
    } else if (!hasRyukyuActivity()) {
        map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 });
    }
}

// Persistent code→station index behind lastStations, so ~3×/s diffs update in
// place instead of rebuilding the whole Map from the array on every message.
let _stationsByCode = new Map();

function updateStations(stations) {
    _stationsByCode = new Map(stations.map(s => [s.code, s]));
    _setStationData(stations);
}

function _setStationData(stations) {
    lastStations = stations;
    // Cache a LatLng per station: redraws run every frame during a pan, and
    // passing [lat, lon] arrays would allocate a fresh LatLng per dot per frame.
    for (const st of stations) if (!st._ll) st._ll = L.latLng(st.lat, st.lon);
    _zeroStations    = stations.filter(st => st.int === '0' || st.int == null);
    _labeledStations = stations
        .filter(st => st.int !== '0' && st.int != null)
        .sort((a, b) => intRank(a.int) - intRank(b.int));
    _maxLiveStationRank = _labeledStations.length
        ? intRank(_labeledStations[_labeledStations.length - 1].int) : -1;
    scheduleRedrawStations();
    if (selectedStationCode && stationPopup.style.display !== 'none') {
        const st = stations.find(s => s.code === selectedStationCode);
        if (st) {
            const pt = map.latLngToContainerPoint([st.lat, st.lon]);
            showStationPopup(st, pt.x, pt.y);
        }
    }
    let maxInt = null;
    let maxPga = null;
    stations.forEach(st => {
        if (intRank(st.int) > intRank(maxInt)) maxInt = st.int;
        if (st.pga != null && (maxPga == null || st.pga > maxPga)) maxPga = st.pga;
    });
    if (!replayActive) {
        _niedEverReceived = true;
        _niedLog.push({time: Date.now(), hasPga: maxPga !== null});
        _evalNiedWarning();
    }
    if (maxInt) {
        const box = document.getElementById('obs-int-box');
        box.textContent = maxInt;
        box.style.background = intColor(maxInt);
        box.style.color = intTextColor(maxInt);
        document.getElementById('obs-panel').classList.add('visible');
        positionObsPanel();
    }
    const pgaBox   = document.getElementById('obs-pga-box');
    const pgaLabel = document.getElementById('obs-pga-label');
    if (maxPga != null) {
        pgaBox.textContent      = formatPga(maxPga);
        pgaBox.style.background = maxPga >= 10 ? pgaColor(maxPga) : '#555';
        pgaBox.style.color      = maxPga >= 10 ? pgaTextColor(maxPga) : '#fff';
        pgaBox.style.display  = 'block';
        pgaLabel.style.display = 'block';
    } else {
        pgaBox.style.display   = 'none';
        pgaLabel.style.display = 'none';
    }

    const hadRyukyuStations = ryukyuStations;
    ryukyuStations = stations.some(st =>
        st.int && st.int !== '0' && isRyukyuCoord(st.lat, st.lon)
    );
    if (ryukyuStations && !hadRyukyuStations && epicenterMarks.size === 0) {
        if (jmaSidebarActiveTs !== null) { clearJmaStations(); renderSidebar(lastQuakeData); }
        map.flyTo(RYUKYU_CENTER, RYUKYU_ZOOM, { animate: true, duration: 1 });
    } else if (!ryukyuStations && hadRyukyuStations && !hasRyukyuActivity()) {
        map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 });
    }

    const hadNiedZoom = niedZoomActive;
    const niedCoords = getNiedActiveCoords(stations);
    niedZoomActive = !!(niedCoords && niedCoords.length > 0);
    if (niedZoomActive) niedZoomCoords = niedCoords;

    if (niedZoomActive && !hadNiedZoom) {
        if (jmaSidebarActiveTs !== null) { clearJmaStations(); renderSidebar(lastQuakeData); }
    }
    if (niedZoomActive && !hadNiedZoom && epicenterMarks.size === 0 && !ryukyuStations) {
        zoomToNiedStations();
    } else if (!niedZoomActive && hadNiedZoom && epicenterMarks.size === 0 && !hasRyukyuActivity()) {
        niedZoomCoords = null;
        map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 });
    }
}

function applyStationsDiff(changed, removed) {
    for (const code of (removed || [])) _stationsByCode.delete(code);
    for (const entry of (changed || [])) _stationsByCode.set(entry.code, entry);
    _setStationData([..._stationsByCode.values()]);
}

// ── P/S wave circles (computed client-side) ───────────────────────────
// Map keyed by event key → { lat, lon, depthKm, originTimeSec, pCircle, sCircle }
const waveMap = new Map();
let waveAnimFrame = null;

// Simplified depth-dependent velocity model; returns surface epicentral radius in km
function seismicRadiusKm(elapsedSec, depthKm, isS) {
    // Effective velocities account for Pn head-wave dominance at typical EEW distances (100+ km)
    const vp = depthKm < 30 ? 6.5 : depthKm < 60 ? 7.0 : depthKm < 100 ? 7.5 : 8.0;
    const v  = isS ? vp / 1.73 : vp;
    const hypo = v * elapsedSec;
    return hypo > depthKm ? Math.sqrt(hypo * hypo - depthKm * depthKm) : 0;
}

function startWaveAnimation(key, lat, lon, depthKm, originTimeSec) {
    const existing = waveMap.get(key);
    if (existing) {
        // Same quake — update position/depth in case it refined
        existing.lat = lat; existing.lon = lon; existing.depthKm = depthKm;
        if (originTimeSec != null) existing.originTimeSec = originTimeSec;
        if (existing.pCircle) existing.pCircle.setLatLng([lat, lon]);
        if (existing.sCircle) existing.sCircle.setLatLng([lat, lon]);
        return;
    }
    waveMap.set(key, { lat, lon, depthKm, originTimeSec, pCircle: null, sCircle: null });
    if (!waveAnimFrame) animateWaves();
}

function clearWaveForKey(key) {
    const entry = waveMap.get(key);
    if (!entry) return;
    if (entry.pCircle) map.removeLayer(entry.pCircle);
    if (entry.sCircle) map.removeLayer(entry.sCircle);
    waveMap.delete(key);
    if (waveMap.size === 0 && waveAnimFrame) { cancelAnimationFrame(waveAnimFrame); waveAnimFrame = null; }
}

function clearWaveAnimation() {
    for (const entry of waveMap.values()) {
        if (entry.pCircle) { map.removeLayer(entry.pCircle); }
        if (entry.sCircle) { map.removeLayer(entry.sCircle); }
    }
    waveMap.clear();
    if (waveAnimFrame) { cancelAnimationFrame(waveAnimFrame); waveAnimFrame = null; }
}

function animateWaves() {
    if (waveMap.size === 0) { waveAnimFrame = null; return; }
    const now = (replayActive && replayClock != null) ? currentReplayClock() : Date.now() / 1000;
    let renderer = null;
    for (const entry of waveMap.values()) {
        const { lat, lon, depthKm, originTimeSec } = entry;
        const elapsed = now - originTimeSec;
        const pR = seismicRadiusKm(elapsed, depthKm, false) * 1000; // metres
        const sR = seismicRadiusKm(elapsed, depthKm, true)  * 1000;

        if (pR > 0) {
            if (!entry.pCircle) {
                entry.pCircle = L.circle([lat, lon], {
                    pane: 'wavesPane', radius: pR,
                    color: '#44aaff', weight: 2, fill: false, interactive: false
                }).addTo(map);
            } else {
                entry.pCircle.setLatLng([lat, lon]);
                entry.pCircle.setRadius(pR);
            }
            renderer = renderer || entry.pCircle._renderer;
        }
        if (sR > 0) {
            if (!entry.sCircle) {
                entry.sCircle = L.circle([lat, lon], {
                    pane: 'wavesPane', radius: sR,
                    color: '#ff6600', weight: 2,
                    fillColor: '#cc3300', fillOpacity: 0.25, interactive: false
                }).addTo(map);
            } else {
                entry.sCircle.setLatLng([lat, lon]);
                entry.sCircle.setRadius(sR);
            }
            renderer = renderer || entry.sCircle._renderer;
        }
    }
    if (renderer && (map._animatingZoom || mapIsMoving)) {
        // The SVG/Canvas vector renderer only rebases its cached center/zoom
        // (and the CSS transform it derives from them) on 'moveend'. During a
        // flyTo — or a chain of them, re-triggered by fast-arriving reports
        // before the previous one ever reaches 'moveend' — that baseline goes
        // stale, so circles projected against the live map view above get
        // double-shifted by the renderer's leftover transform and render far
        // from the epicentre. Force the same resync 'viewreset' would do, every
        // frame, so the waves stay correctly placed throughout instead of
        // freezing (or only catching up once movement finally settles).
        renderer._update();
        renderer._updateTransform(renderer._center, renderer._zoom);
        for (const id in renderer._layers) {
            renderer._layers[id]._reset();
        }
    }
    waveAnimFrame = requestAnimationFrame(animateWaves);
}

// ── Epicentre markers ─────────────────────────────────────────────────
const EPICENTRE_HTML       = '<div style="font-size:24px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;color:#fff;text-shadow:0 0 6px #000,0 0 2px #000;line-height:1">✕</div>';
const EPICENTRE_HTML_FLASH = '<div class="eew-epicentre-flash" style="font-size:24px;width:26px;height:26px;display:flex;align-items:center;justify-content:center;color:#fff;text-shadow:0 0 6px #000,0 0 2px #000;line-height:1">✕</div>';
const EPICENTRE_ICON       = ()      => L.divIcon({ html: EPICENTRE_HTML,       className: '', iconSize: [26, 26], iconAnchor: [13, 13] });
const EPICENTRE_ICON_FLASH = ()      => L.divIcon({ html: EPICENTRE_HTML_FLASH, className: '', iconSize: [26, 26], iconAnchor: [13, 13] });

function placeMarker(key, lat, lon, isPlum, isEew) {
    removeEpicenterMark(key);
    if (isPlum) {
        epicenterMarks.set(key, L.circle([lat, lon], {
            pane: 'epicenterPane',
            radius: 30000, color: '#ffffff', weight: 2,
            dashArray: '8 5', fill: false, interactive: false
        }).addTo(map));
    } else {
        const el = document.createElement('div');
        el.style.cssText = 'position:absolute;top:0;left:0;';
        el.innerHTML = isEew ? EPICENTRE_HTML_FLASH : EPICENTRE_HTML;
        epicenterLayer.appendChild(el);
        const mark = { _isDomMark: true, el, lat, lon };
        positionEpicenterMark(mark);
        epicenterMarks.set(key, mark);
    }
}

function placeAllEpicenters() {
    for (const [key, data] of activeEews) {
        if (data.lat != null && data.lon != null) placeMarker(key, data.lat, data.lon, data.is_plum, true);
    }
    ryukyuEpicentre = [...activeEews.values()].some(d => d.lat != null && d.lon != null && isRyukyuCoord(d.lat, d.lon));
    const coords = [...activeEews.values()].filter(d => d.lat != null && d.lon != null).map(d => [d.lat, d.lon]);
    if (coords.length === 0) return;
    if (coords.length === 1) {
        const [lat, lon] = coords[0];
        if (isRyukyuCoord(lat, lon)) {
            map.flyTo([lat, lon], RYUKYU_ZOOM, { animate: true, duration: 1 });
        } else if (!hasRyukyuActivity()) {
            map.flyTo([lat, lon], QUAKE_ZOOM, { animate: true, duration: 1 });
        }
    } else {
        map.flyToBounds(L.latLngBounds(coords), { padding: [60, 60], animate: true, duration: 1, maxZoom: QUAKE_ZOOM });
    }
}

// ── Tsunami card ──────────────────────────────────────────────────────
let activeTsunamiData = null;
let activeTsunamiObs  = null;

// ── Offshore tsunami observation station regions ──────────────────────
// JMA's offshore observation telegrams give a station name/code but no
// coordinates, so locations come from eng_codes.offshore_station_coords —
// some surveyed (GPS wave-gauge buoy positions), others geocoded estimates.
// Estimated ones are drawn as dashed circles with a disclaimer in their tooltip.
let offshoreObsCircles = [];

function clearOffshoreObsRegions() {
    offshoreObsCircles.forEach(c => map.removeLayer(c));
    offshoreObsCircles = [];
}

function drawOffshoreObsRegions(data) {
    clearOffshoreObsRegions();
    const lats = data.obs_lats || [], lons = data.obs_lons || [], radii = data.obs_radii_km || [];
    const estimated = data.obs_loc_estimated || [];
    const namesEn = data.obs_station_names_en || [], namesJa = data.obs_station_names || [];
    for (let i = 0; i < lats.length; i++) {
        const lat = lats[i], lon = lons[i], radiusKm = radii[i];
        if (lat == null || lon == null || radiusKm == null) continue;
        const isEstimated = !!estimated[i];
        const circle = L.circle([lat, lon], {
            radius: radiusKm * 1000,
            color: '#81c995',
            weight: isEstimated ? 1.5 : 2,
            opacity: isEstimated ? 0.6 : 0.85,
            dashArray: isEstimated ? '6 5' : null,
            fill: true, fillColor: '#81c995', fillOpacity: isEstimated ? 0.05 : 0.1,
            interactive: true,
        }).addTo(map);
        const nameEn = namesEn[i] || '', nameJa = namesJa[i] || '';
        const disclaimer = isEstimated
            ? '<br><span style="color:#ffcc66">⚠ Estimated location — exact position unknown, area shown is a rough guess</span>'
            + '<br><span style="color:#ffcc66">⚠ 推定位置 — 正確な観測点位置は不明、表示範囲はおおよその目安</span>'
            : '';
        circle.bindTooltip(
            `<b>${nameEn}</b><br>${nameJa}${disclaimer}`,
            { direction: 'top', sticky: true }
        );
        offshoreObsCircles.push(circle);
    }
}

function tsunamiLevelColor(level) {
    if (level === 'Major Warning') return '#cc00ff';
    if (level === 'Warning')       return '#ff3300';
    if (level === 'Advisory')      return '#ffdd00';
    return '#4488ff';
}
function tsunamiLevelTextColor(level) {
    return (level === 'Advisory' || level === 'Slight sea level change') ? '#111' : '#fff';
}
// JMA tsunami kind codes (警報等情報要素／津波警報・注意報・予報). Must stay in sync
// with TSUNAMI_KIND_LEVEL / TSUNAMI_LIFTED_KINDS in private/main.py.
//   52 大津波警報 and 53 大津波警報：発表 are both the major tier — 53 is the code
//   used the moment an area enters a major warning, so it must never fall through
//   to the "Forecast" default.
//   50 警報解除 / 60 津波注意報解除 / 00 津波なし mean nothing is in force. The backend
//   drops those areas, but replayed history predates that, so guard here too.
const TSUNAMI_LIFTED_KINDS = new Set(['50', '60', '00']);
const TSUNAMI_KIND_TIER = { '53': 'major', '52': 'major', '51': 'warning', '62': 'advisory' };
function kindTier(code) {
    return TSUNAMI_KIND_TIER[code] || 'forecast'; // 71/72/73 若干の海面変動
}
function kindColor(code) {
    const t = kindTier(code);
    if (t === 'major')    return '#cc00ff';
    if (t === 'warning')  return '#ff3300';
    if (t === 'advisory') return '#ffdd00';
    return '#4488ff';
}
function kindTextColor(code) {
    const t = kindTier(code);
    return (t === 'advisory' || !code) ? '#111' : '#fff';
}
function kindLabelEn(code) {
    const t = kindTier(code);
    if (t === 'major')    return 'Major';
    if (t === 'warning')  return 'Warning';
    if (t === 'advisory') return 'Advisory';
    return 'Forecast';
}
function kindLabelJa(code) {
    const t = kindTier(code);
    if (t === 'major')    return '大津波';
    if (t === 'warning')  return '警報';
    if (t === 'advisory') return '注意報';
    return '予報';
}

function renderTsunamiCard() {
    const panel = document.getElementById('tsunami-panel');
    if (!activeTsunamiData && !activeTsunamiObs) {
        panel.classList.remove('visible');
        positionTsunamiPanel();
        updateMapKey();
        return;
    }
    const d   = activeTsunamiData;
    const obs = activeTsunamiObs;
    let html = '<div class="tsun-card">';

    if (d) {
        const level = d.warning_level || '';
        const lc  = tsunamiLevelColor(level);
        const ltc = tsunamiLevelTextColor(level);
        const levelEn = {'Major Warning':'Major Tsunami Warning','Warning':'Tsunami Warning','Advisory':'Tsunami Advisory','Slight sea level change':'Slight Sea Level Change'}[level] || level;
        const levelJa = {'Major Warning':'大津波警報','Warning':'津波警報','Advisory':'津波注意報','Slight sea level change':'若干の海面変動'}[level] || level;
        html += `<div class="tsun-header"><span class="tsun-level-badge" style="background:${lc};color:${ltc}"><span class="en">${levelEn}</span><span class="ja">${levelJa}</span></span></div>`;
        html += `<div class="tsun-time">${formatJst(d.report_time)}</div>`;

        const KIND_PRIORITY = {'major':4,'warning':3,'advisory':2,'forecast':1};
        // Sort key: 巨大 (huge) > 高い (high) > numeric height > kind priority fallback
        function heightSortKey(h, hCond, code) {
            if (hCond === '巨大') return 100000;
            if (hCond === '高い') return 10000;
            if (h > 0) return h;
            return (KIND_PRIORITY[kindTier(code)] || 0) * 0.001;
        }
        // Display text for height: use condition label when no numeric value.
        // `over` marks JMA's "N m以上" form — the top forecast band (10m超) and
        // gauge-limited observations — so it must not render as a plain "Nm".
        function fmtHeight(h, hCond, over) {
            if (h > 0) return over
                ? `<span class="en">over ${h}m</span><span class="ja">${h}m超</span>`
                : h + 'm';
            if (hCond === '巨大') return `<span class="en">Huge</span><span class="ja">巨大</span>`;
            if (hCond === '高い') return `<span class="en">High</span><span class="ja">高い</span>`;
            if (hCond === '微弱') return `<span class="en">Faint</span><span class="ja">微弱</span>`;
            if (hCond === '欠測') return `<span class="en">No Data</span><span class="ja">欠測</span>`;
            return '–';
        }
        // Compact "DD日HH:MM" JST formatting, matching JMA's own forecast displays
        function fmtCompactJst(unixSec) {
            const dt = new Date((unixSec + 9 * 3600) * 1000);
            const pad = n => String(n).padStart(2, '0');
            return `${pad(dt.getUTCDate())}日${pad(dt.getUTCHours())}:${pad(dt.getUTCMinutes())}`;
        }
        // Forecast region status before a numeric/qualitative max height is known:
        // either an estimated arrival time or a "presumed arriving" / "confirmed" status
        function fmtArrivalStatus(cond, arrivalTs) {
            if (cond === '津波到達中と推測') return `<span class="en">Tsunami presumed arriving</span><span class="ja">津波到達中と推測</span>`;
            if (cond === '第１波の到達を確認') return `<span class="en">First wave confirmed</span><span class="ja">第１波の到達を確認</span>`;
            if (cond === '第１波識別不能') return `<span class="en">First wave unclear</span><span class="ja">第１波識別不能</span>`;
            if (cond === '不明') return `<span class="en">Unknown</span><span class="ja">不明</span>`;
            if (arrivalTs) {
                const t = fmtCompactJst(arrivalTs);
                return `<span class="en">Est. arrival ${t}</span><span class="ja">到達予想 ${t}</span>`;
            }
            return '–';
        }

        if (d.regions_en && d.regions_en.length) {
            const rows = d.regions_en.map((en, i) => ({
                en, ja: (d.regions_jp || [])[i] || en,
                h: (d.heights || [])[i] ?? 0,
                hCond: (d.height_conditions || [])[i] || '',
                over: !!(d.height_over || [])[i],
                code: (d.kind_codes || [])[i] || '',
                fCond: (d.conditions || [])[i] || '',
                arrival: (d.arrival_times || [])[i] || null,
                regionCode: (d.region_codes || [])[i] || '',
            })).filter(r => !TSUNAMI_LIFTED_KINDS.has(r.code)); // stood-down coasts (replayed history)
            rows.sort((a, b) => heightSortKey(b.h, b.hCond, b.code) - heightSortKey(a.h, a.hCond, a.code));
            html += `<div class="tsun-section-label"><span class="en">Forecast Regions</span><span class="ja">予報・警報地域</span></div><div class="tsun-list">`;
            rows.forEach(r => {
                const kc = kindColor(r.code), ktc = kindTextColor(r.code);
                // Before a max wave height/condition is reported, show the first-wave
                // arrival status (estimated arrival time, "presumed arriving", etc.)
                const heightCell = (r.h <= 0 && !r.hCond) ? fmtArrivalStatus(r.fCond, r.arrival) : fmtHeight(r.h, r.hCond, r.over);
                const clickable = tsunamiRegionCentroids[String(r.regionCode)] ? ' tsun-row-clickable' : '';
                html += `<div class="tsun-row${clickable}" data-tsun-code="${r.regionCode}"><span class="tsun-row-name"><span class="en">${r.en}</span><span class="ja">${r.ja}</span></span><span class="tsun-row-height">${heightCell}</span><span class="tsun-row-kind" style="background:${kc};color:${ktc}"><span class="en">${kindLabelEn(r.code)}</span><span class="ja">${kindLabelJa(r.code)}</span></span></div>`;
            });
            html += '</div>';
        }

        if (Array.isArray(d.obs_regions_en) && d.obs_regions_en.length) {
            const rows = d.obs_regions_en.map((en, i) => ({
                en, ja: (d.obs_regions_jp || [])[i] || en,
                h: (d.obs_heights || [])[i] ?? 0,
                hCond: (d.obs_height_conditions || [])[i] || '',
                over: !!(d.obs_height_over || [])[i],
                regionCode: (d.obs_region_codes || [])[i] || '',
            }));
            rows.sort((a, b) => heightSortKey(b.h, b.hCond, '') - heightSortKey(a.h, a.hCond, ''));
            html += `<div class="tsun-sep"></div><div class="tsun-section-label"><span class="en">Observations</span><span class="ja">観測</span></div><div class="tsun-list">`;
            rows.forEach(r => {
                const clickable = tsunamiRegionCentroids[String(r.regionCode)] ? ' tsun-row-clickable' : '';
                html += `<div class="tsun-row${clickable}" data-tsun-code="${r.regionCode}"><span class="tsun-row-name"><span class="en">${r.en}</span><span class="ja">${r.ja}</span></span><span class="tsun-row-height" style="color:#4fc3f7">${fmtHeight(r.h, r.hCond, r.over)}</span></div>`;
            });
            html += '</div>';
        }
    }

    if (obs && Array.isArray(obs.obs_station_names) && obs.obs_station_names.length) {
        const rows = obs.obs_station_names.map((nameJa, i) => ({
            nameJa, nameEn: (obs.obs_station_names_en || [])[i] || nameJa,
            h: (obs.obs_heights || [])[i] ?? 0,
            hCond: (obs.obs_height_conditions || [])[i] || '',
            over: !!(obs.obs_height_over || [])[i],
            estimated: !!(obs.obs_loc_estimated || [])[i],
        }));
        rows.sort((a, b) => heightSortKey(b.h, b.hCond, '') - heightSortKey(a.h, a.hCond, ''));
        if (d) html += '<div class="tsun-sep"></div>';
        html += `<div class="tsun-section-label"><span class="en">Offshore Obs.</span><span class="ja">沖合観測</span></div><div class="tsun-list">`;
        rows.forEach(r => {
            const estBadge = r.estimated
                ? '<span class="tsun-row-est-badge" title="Estimated location — exact station position unknown / 推定位置 — 観測点の正確な位置は不明">~</span>' : '';
            html += `<div class="tsun-row"><span class="tsun-row-name"><span class="en">${r.nameEn}${estBadge}</span><span class="ja">${r.nameJa}${estBadge}</span></span><span class="tsun-row-height" style="color:#81c995">${fmtHeight(r.h, r.hCond, r.over)}</span></div>`;
        });
        html += '</div>';
    }

    html += '</div>';
    panel.innerHTML = html;
    panel.classList.add('visible');
    setLanguage(currentLanguage);
    positionTsunamiPanel();
    updateMapKey();
}

function positionTsunamiPanel() {
    const panel = document.getElementById('tsunami-panel');
    if (!panel.classList.contains('visible')) return;
    const obsEl = document.getElementById('obs-panel');
    const eewEl = document.getElementById('eew-panel');
    const anchor = obsEl.classList.contains('visible') ? obsEl : eewEl;
    const rect = anchor.getBoundingClientRect();
    panel.style.top  = (rect.bottom + 6) + 'px';
    panel.style.left = '10px';
}

// ── EEW panel ─────────────────────────────────────────────────────────
function buildEewCard(data) {
    const isPlum     = !!data.is_plum;
    const isFlash    = !isPlum && !!data.is_flash;
    const isWarning  = data.warning;
    const typeEnText = isPlum ? 'EEW (PLUM Method)' : isFlash ? 'EEW (Flash Report)' : (isWarning ? 'EEW (Warning)' : 'EEW (Forecast)');
    const typeJaText = isPlum ? '緊急地震速報（PLUM法）' : isFlash ? '緊急地震速報（速報）' : (isWarning ? '緊急地震速報（警報）' : '緊急地震速報（予報）');
    const reportNo   = data.report_num || '–';
    const maxInt     = (isPlum || isFlash) ? '?' : (data.max_int != null ? String(data.max_int) : '–');
    const locEn      = (data.epi_location_en && data.epi_location_en.length ? data.epi_location_en[0] : '–');
    const locJa      = (data.epi_location_jp && data.epi_location_jp.length ? data.epi_location_jp[0] : '–');
    const mag        = isPlum ? '–' : (data.magnitude != null ? data.magnitude : '–');
    const depth      = isPlum ? '–' : (data.depth != null ? data.depth : '–');
    const quakeTime  = (data.origin_time ?? data.quake_time) ? formatJst(data.origin_time ?? data.quake_time) : '–';
    // PLUM/flash reports carry no computed forecast, so any LgInt in them is meaningless.
    const lpgm       = (isPlum || isFlash) ? null : lpgmClass(data.max_lpgm);
    const finalEn    = data.last_report ? '  [Final]' : '';
    const finalJa    = data.last_report ? '  [最終]' : '';
    const div = document.createElement('div');
    div.className = 'eew-card';
    div.innerHTML =
        `<div class="eew-type"><span class="en">${typeEnText}  #${reportNo}${finalEn}</span><span class="ja">${typeJaText}  第${reportNo}報${finalJa}</span></div>` +
        `<div class="eew-int-box" style="background:${(isPlum || isFlash) ? '#555' : intColor(maxInt)};color:${(isPlum || isFlash) ? '#aaa' : intTextColor(maxInt)}">${maxInt}</div>` +
        (lpgm ? `<div class="eew-lpgm-box" style="background:${lpgmColor(lpgm)};color:${lpgmTextColor(lpgm)}">` +
                    `<span class="eew-lpgm-label">` +
                        `<span class="en">Est. max long-period<br>ground motion class</span>` +
                        `<span class="ja">推定 最大長周期<br>地震動階級</span>` +
                    `</span>` +
                    `<span class="eew-lpgm-val">${lpgm}</span>` +
                `</div>` : '') +
        `<div class="eew-location"><span class="en">${locEn}</span><span class="ja">${locJa}</span></div>` +
        `<div class="eew-detail">M ${mag}  Depth ${depth} km</div>` +
        (isPlum ? `<div class="eew-detail" style="color:#aaa;font-style:italic"><span class="en">Hypothetical source – no detailed info</span><span class="ja">仮定震源要素</span></div>` : '') +
        (isFlash ? `<div class="eew-detail" style="color:#aaa;font-style:italic"><span class="en">Intensity forecast not yet available</span><span class="ja">震度予測未計算</span></div>` : '') +
        `<div class="eew-time">${quakeTime}</div>`;
    return div;
}

function renderEewPanels() {
    const container = document.getElementById('eew-cards');
    container.innerHTML = '';
    if (activeEews.size === 0) {
        document.getElementById('no-eew-msg').style.display = '';
        setLanguage(currentLanguage);
        positionObsPanel();
        return;
    }
    document.getElementById('no-eew-msg').style.display = 'none';
    let first = true;
    for (const data of activeEews.values()) {
        if (!first) {
            const sep = document.createElement('div');
            sep.className = 'eew-card-sep';
            container.appendChild(sep);
        }
        container.appendChild(buildEewCard(data));
        first = false;
    }
    setLanguage(currentLanguage);
    positionObsPanel();
}

function positionObsPanel() {
    const eewEl = document.getElementById('eew-panel');
    const obsEl = document.getElementById('obs-panel');
    const rect  = eewEl.getBoundingClientRect();
    obsEl.style.top  = (rect.bottom + 6) + 'px';
    obsEl.style.left = '10px';
    positionTsunamiPanel();
}

function formatJst(unixSec) {
    const d = new Date((unixSec + 9 * 3600) * 1000);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}/${pad(d.getUTCMonth()+1)}/${pad(d.getUTCDate())} ` +
           `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} JST`;
}

// ── Language toggle ───────────────────────────────────────────────────
let currentLanguage = 'en';
let lastQuakeData = [];
const wsQuakeBuffer = new Map(); // quake_time (unix seconds) → ws entry

function setLanguage(lang) {
    currentLanguage = lang;
    document.documentElement.lang = lang;
    const boreholeRow = document.getElementById('sp-borehole-row');
    if (boreholeRow) boreholeRow.title = lang === 'ja' ? '地中観測点' : 'Underground station';
    const mapKey = document.getElementById('map-key');
    if (mapKey) mapKey.title = lang === 'ja' ? '凡例' : 'Map key';
    renderSidebar(lastQuakeData);
    // Follow the UI language for notifications unless the user has set an explicit override
    if (!localStorage.getItem(NOTIF_LANG_KEY)) {
        updateNotifLanguageUI(lang);
        if (window.OneSignal) window.OneSignal.User.setLanguage(lang);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    setLanguage(window.__WEBQUAKE_LANG === 'ja' ? 'ja' : 'en');
    loadTimelineMarkersAndGaps();
    // Fallback only — new quakes/tsunamis trigger an immediate refresh themselves (see displayData).
    if (!_timelineRefreshTimer) _timelineRefreshTimer = setInterval(loadTimelineMarkersAndGaps, 300000);
});

// ── WebSocket ─────────────────────────────────────────────────────────
let lastMessageTime = Date.now();
let connectionAttempts = 0;
let reconnectTimer = null;

function updateConnectionStatus(text, bg) {
    const el = document.getElementById('connection-status');
    el.textContent = text;
    el.style.background = bg;
    el.style.color = bg === '#8f8' ? '#000' : '#fff';
}

function monitorConnection() {
    const age = Date.now() - lastMessageTime;
    if (age < 1500) {
        updateConnectionStatus(currentLanguage === 'ja' ? '接続中' : 'Connected', '#8f8');
    } else if (age < 5000) {
        updateConnectionStatus(currentLanguage === 'ja' ? '不安定' : 'Unstable', '#ff8');
    } else {
        updateConnectionStatus(currentLanguage === 'ja' ? '切断' : 'Disconnected', '#f44');
    }
}
setInterval(monitorConnection, 1000);

function createWebSocket() {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    updateConnectionStatus('Connecting…', '#555');

    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const socket = new WebSocket(`${proto}://${location.host}/ws`);

    const pingInterval = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) socket.send('!ping');
    }, 6000);

    socket.addEventListener('open', () => {
        lastMessageTime = Date.now();
        connectionAttempts = 0;
        updateConnectionStatus('Connected', '#8f8');
    });

    socket.addEventListener('error', e => console.error('WS error', e));

    socket.addEventListener('close', () => {
        clearInterval(pingInterval);
        connectionAttempts++;
        const delay = Math.min(1000 * connectionAttempts, 30000);
        updateConnectionStatus(`Reconnecting… (${connectionAttempts})`, '#f80');
        reconnectTimer = setTimeout(createWebSocket, delay);
    });

    socket.addEventListener('message', e => {
        lastMessageTime = Date.now();
        let data;
        try { data = JSON.parse(e.data); } catch { return; }
        if (data.type === 'nied_stations') {
            liveStationCache.clear();
            data.stations.forEach(s => liveStationCache.set(s.code, s));
        } else if (data.type === 'nied_stations_diff') {
            (data.removed || []).forEach(code => liveStationCache.delete(code));
            (data.stations || []).forEach(s => liveStationCache.set(s.code, s));
        } else if (data.type === 'snet_stations') {
            liveSnetCache.clear();
            data.stations.forEach(s => liveSnetCache.set(s.code, s));
        } else if (data.type === 'snet_stations_diff') {
            (data.removed || []).forEach(code => liveSnetCache.delete(code));
            (data.stations || []).forEach(s => liveSnetCache.set(s.code, s));
        } else if (data.type === 'jma_history') {
            liveQuakeHistory = data.quakes;
        }
        if (replayActive) {
            replayLiveBuffer.push(data);
            if (replayLiveBuffer.length > 1000) replayLiveBuffer.shift();
            if (!lockTimelineOnReplay && REPLAY_DISMISS_TYPES.has(data.type)) exitReplay();
            return;
        }
        displayData(data);
    });
}

createWebSocket();

// ── Earthquake history sidebar ────────────────────────────────────────
let quakeSidebarCollapsed = window.innerWidth <= 600;
if (quakeSidebarCollapsed) {
    document.getElementById('quake-sidebar').classList.add('collapsed');
    const btn = document.getElementById('quake-sidebar-toggle');
    btn.classList.add('sidebar-collapsed');
    btn.textContent = '◀';
}

function toggleQuakeSidebar() {
    quakeSidebarCollapsed = !quakeSidebarCollapsed;
    const sb  = document.getElementById('quake-sidebar');
    const btn = document.getElementById('quake-sidebar-toggle');
    sb.classList.toggle('collapsed', quakeSidebarCollapsed);
    btn.classList.toggle('sidebar-collapsed', quakeSidebarCollapsed);
    btn.textContent = quakeSidebarCollapsed ? '◀' : '▶';
    const leafletRight = document.querySelector('.leaflet-bottom.leaflet-right');
    if (leafletRight) leafletRight.style.right = quakeSidebarCollapsed ? '0' : '262px';
}

// Collapses the quake history sidebar after a map/replay action on mobile,
// where it overlays the map and would otherwise block the view.
function collapseSidebarOnMobile() {
    if (window.innerWidth <= 600 && !quakeSidebarCollapsed) toggleQuakeSidebar();
}

function parseQuakeDepth(cod) {
    const m = (cod || '').match(/([+-]\d+)\/$/);
    return m ? Math.round(Math.abs(parseInt(m[1])) / 1000) : null;
}

// Builds a comparable fingerprint of the fields that matter to the user (time/location/
// intensity/magnitude/depth) from either source's raw fields, normalizing away the minor
// formatting differences between the WQ (live telegram) and JMA history feeds.
function quakeSoundFingerprint({ at, quakeTimeSec, intensity, mag, depth, loc }) {
    const tsMin = at != null ? Math.round(new Date(at).getTime() / 60000)
                : (quakeTimeSec != null ? Math.round(quakeTimeSec / 60) : null);
    const magNum = (mag != null && mag !== '') ? Number(mag).toFixed(1) : null;
    const depthNum = depth != null ? Math.round(depth) : null;
    return [tsMin, intensity || null, magNum, depthNum, loc || null].join('|');
}

// Resolves a canonical key for quakeInfoSoundState: an exact id match if we have one,
// otherwise the existing entry whose time is within 90s (JMA history times are minute-
// truncated, so this mirrors the drift tolerance used elsewhere for source matching).
function resolveQuakeSoundKey(candidateKey, tsSeconds) {
    if (candidateKey != null && quakeInfoSoundState.has(candidateKey)) return candidateKey;
    if (tsSeconds != null) {
        for (const [k, v] of quakeInfoSoundState) {
            if (v.ts != null && Math.abs(v.ts - tsSeconds) < 90) return k;
        }
    }
    return candidateKey;
}

function formatQuakeTime(isoStr) {
    const d = new Date(new Date(isoStr).getTime() + 9 * 3600 * 1000);
    const y = d.getUTCFullYear();
    const mo = String(d.getUTCMonth() + 1).padStart(2, '0');
    const dy = String(d.getUTCDate()).padStart(2, '0');
    const h  = String(d.getUTCHours()).padStart(2, '0');
    const mi = String(d.getUTCMinutes()).padStart(2, '0');
    return `${y}/${mo}/${dy} ${h}:${mi}`;
}

function addWsQuakeToSidebar(data) {
    const key = data.event_id || data.quake_time;
    if (!key) return;
    const existing = wsQuakeBuffer.get(key) || {};
    wsQuakeBuffer.set(key, {
        event_id:   data.event_id  ?? existing.event_id  ?? null,
        quake_time: data.quake_time ?? existing.quake_time ?? null,
        max_int:    data.max_int    ?? existing.max_int    ?? '0',
        magnitude:  data.magnitude  ?? existing.magnitude  ?? null,
        depth:      data.depth      ?? existing.depth      ?? null,
        loc_en:     (data.epi_location_en && data.epi_location_en[0]) || existing.loc_en || null,
        loc_jp:     (data.epi_location_jp && data.epi_location_jp[0]) || existing.loc_jp || null,
    });
    renderSidebar(lastQuakeData);
}

// Closes any open multi-source JMA popover when the user clicks elsewhere.
if (!window._jmaPopoverDocListenerAdded) {
    window._jmaPopoverDocListenerAdded = true;
    document.addEventListener('click', e => {
        if (e.target.closest('.quake-jma-multi') || e.target.closest('.quake-jma-popover')) return;
        document.querySelectorAll('.quake-jma-popover').forEach(p => p.classList.add('hidden'));
    });
}

function formatCttTime(ctt) {
    if (!ctt || ctt.length < 12) return '';
    return `${ctt.slice(8, 10)}:${ctt.slice(10, 12)}`;
}

function jmaDetailUrl(ctt, isJa) {
    return `https://www.data.jma.go.jp/multi/quake/quake_detail.html?eventID=${ctt}&lang=${isJa ? 'jp' : 'en'}`;
}

// Normalizes a JMA history entry or a WS buffer entry into a common shape so
// per-field provenance (which source contributed the currently-displayed value)
// can be tracked when several reports describe the same earthquake.
function normalizeJmaCandidate(q, isJa) {
    return {
        kind: 'jma', ctt: q.ctt,
        intensity: (q.maxi && q.maxi !== '0') ? q.maxi : null,
        depth: parseQuakeDepth(q.cod),
        mag: q.mag ?? null,
        loc: (isJa ? q.anm : (q.en_anm || '').replace(/ Prefecture/gi, '').replace(/ Pref\./gi, '')) || q.anm || null,
        at: q.at,
        lat: null, lon: null,
        url: jmaDetailUrl(q.ctt, isJa),
    };
}
function normalizeWsCandidate(ws) {
    return {
        kind: 'ws', ctt: null,
        intensity: (ws.max_int && ws.max_int !== '0') ? ws.max_int : null,
        depth: ws.depth ?? null,
        mag: ws.magnitude ?? null,
        loc: (currentLanguage === 'ja' ? ws.loc_jp : ws.loc_en) || null,
        at: ws.quake_time ? new Date(ws.quake_time * 1000).toISOString() : null,
        lat: ws.lat ?? null, lon: ws.lon ?? null,
        url: null,
    };
}
function pickField(priorityList, key) {
    for (const c of priorityList) {
        if (c[key] != null) return { value: c[key], source: c };
    }
    return { value: null, source: null };
}

function renderSidebar(quakes) {
    const list = document.getElementById('quake-sidebar-list');
    const isJa = currentLanguage === 'ja';
    list.innerHTML = '';

    const unknownTip  = isJa ? 'JMAがまだ詳細情報を公表していません。' : 'Detailed info not released (yet) by JMA.';
    const wqTip       = isJa ? '公式情報。JMAデータより先に情報を受信したWebQuakeが発信した速報です。' : 'Official report originated from WebQuake as info was received before JMA data.';
    const wqjmaTip    = isJa ? 'JMAの速報とWebQuakeのデータを統合した情報です。JMAの情報が速報のため、欠損フィールドはWebQuakeで補完されています。' : 'JMA report combined with WebQuake data, as JMA report is preliminary.';
    const fieldLabels = {
        intensity: isJa ? '最大震度' : 'Max intensity',
        depth:     isJa ? '深さ'     : 'Depth',
        mag:       isJa ? 'マグニチュード' : 'Magnitude',
        loc:       isJa ? '震源地'   : 'Location',
    };
    const wqStaticLabel = isJa ? 'WebQuake（速報データ）' : 'WebQuake (live telegram data)';
    const sourcesTitle  = isJa ? '情報の出典' : 'Data sources';

    // Cluster each WS (live telegram) entry with every JMA history entry that
    // describes the same earthquake (matched by event_id or within 90 s of origin time).
    // Unmatched JMA entries form their own single-candidate cluster.
    const usedJmaIdx = new Set();
    const clusters = [];
    for (const [wsKey, ws] of wsQuakeBuffer) {
        const jmaIdxs = [];
        quakes.forEach((q, idx) => {
            const jt = q.at ? Math.round(new Date(q.at).getTime() / 1000) : null;
            const idMatch = q.ctt && ws.event_id && q.ctt === ws.event_id;
            const timeMatch = jt && ws.quake_time && Math.abs(jt - ws.quake_time) < 90;
            if (idMatch || timeMatch) { jmaIdxs.push(idx); usedJmaIdx.add(idx); }
        });
        clusters.push({ ws, jmaIdxs, ts: ws.quake_time || 0 });
    }
    quakes.forEach((q, idx) => {
        if (usedJmaIdx.has(idx)) return;
        const ts = q.at ? Math.round(new Date(q.at).getTime() / 1000) : 0;
        clusters.push({ ws: null, jmaIdxs: [idx], ts });
    });

    const renderItems = []; // { ts, el } — sorted newest-first before being appended

    clusters.forEach(cluster => {
        // JMA candidates take priority over WS, and among themselves the most recently
        // issued report (highest ctt, a YYYYMMDDHHMMSS string) wins ties for a given field.
        const jmaCandidates = cluster.jmaIdxs
            .map(idx => normalizeJmaCandidate(quakes[idx], isJa))
            .sort((a, b) => String(b.ctt || '').localeCompare(String(a.ctt || '')));
        const wsCandidate = cluster.ws ? normalizeWsCandidate(cluster.ws) : null;
        const priorityList = wsCandidate ? [...jmaCandidates, wsCandidate] : jmaCandidates;

        const fIntensity = pickField(priorityList, 'intensity');
        const fDepth      = pickField(priorityList, 'depth');
        const fMag        = pickField(priorityList, 'mag');
        const fLoc        = pickField(priorityList, 'loc');
        const atSource = fLoc.source || fIntensity.source || fMag.source || fDepth.source || priorityList[0] || null;

        // Group contributing fields by the source that supplied them, for the breakdown popover.
        const sourceGroups = new Map();
        [['intensity', fIntensity], ['depth', fDepth], ['mag', fMag], ['loc', fLoc]].forEach(([key, f]) => {
            if (!f.source) return;
            const groupKey = f.source.kind === 'jma' ? `jma:${f.source.ctt}` : 'ws';
            if (!sourceGroups.has(groupKey)) sourceGroups.set(groupKey, { kind: f.source.kind, ctt: f.source.ctt, url: f.source.url, fields: [] });
            sourceGroups.get(groupKey).fields.push(fieldLabels[key]);
        });
        const sources = [...sourceGroups.values()];
        const jmaSources = sources.filter(s => s.kind === 'jma');
        const hasWs = sources.some(s => s.kind === 'ws');
        const hasJma = jmaSources.length > 0;

        const maxi  = fIntensity.value || '0';
        const badge = maxi === '0' ? '?' : maxi;
        const bg    = intColor(maxi);
        const fg    = intTextColor(maxi);
        const loc   = fLoc.value || '–';
        const t     = atSource && atSource.at ? formatQuakeTime(atSource.at) : '–';
        const mag   = fMag.value;
        const depth = fDepth.value;
        const depthLabel = fieldLabels.depth;
        const depthPart = depth != null
            ? `&ensp;${depthLabel}: <b>${depth} km</b>`
            : mag != null ? `&ensp;<span title="${unknownTip}">${depthLabel}: <b>?</b></span>` : '';
        const magStr = mag != null
            ? `<b>M ${mag}</b>${depthPart}`
            : `<span title="${unknownTip}">–</span>`;
        const badgeHtml = badge === '?'
            ? `<div class="quake-int-badge" style="background:${bg};color:${fg}" title="${unknownTip}">${badge}</div>`
            : `<div class="quake-int-badge" style="background:${bg};color:${fg}">${badge}</div>`;

        const ws = cluster.ws;
        const originTs = ws ? (ws.quake_time || null) : null;
        const jmaIndexEntry = (!ws && atSource && atSource.at) ? findJmaIndexEntry(Math.round(new Date(atSource.at).getTime() / 1000)) : null;
        const mapTs = originTs || (jmaIndexEntry ? jmaIndexEntry.ts : (atSource && atSource.at ? Math.round(new Date(atSource.at).getTime() / 1000) : null));
        const mapLat = ws ? ws.lat ?? jmaIndexEntry?.lat ?? null : jmaIndexEntry?.lat ?? null;
        const mapLon = ws ? ws.lon ?? jmaIndexEntry?.lon ?? null : jmaIndexEntry?.lon ?? null;
        const mapActive = mapTs && jmaSidebarActiveTs === mapTs;
        const replayMarker = mapTs ? findReplayMarkerForTs(mapTs) : null;

        const a = document.createElement('a');
        const isActive = hasPastQuakeData && (
            (activePastQuakeEventId && ((ws && ws.event_id === activePastQuakeEventId) || jmaSources.some(s => s.ctt === activePastQuakeEventId))) ||
            (activePastQuakeTime && mapTs && Math.abs(mapTs - activePastQuakeTime) < 90)
        );
        a.className = 'quake-entry' + (isActive ? ' quake-entry-active' : '');

        const sourceLabel = hasWs
            ? (hasJma
                ? `<span class="quake-source quake-source-wqjma" title="${wqjmaTip}">WQ+</span>`
                : `<span class="quake-source quake-source-ws" title="${wqTip}">WQ</span>`)
            : '';

        let jmaBtnHtml = '';
        let jmaPopoverHtml = '';
        if (jmaSources.length === 1) {
            jmaBtnHtml = `<a class="quake-jma-btn" href="${jmaSources[0].url}" target="_blank" rel="noopener noreferrer" title="Open on JMA website">JMA ↗</a>`;
        } else if (jmaSources.length > 1 || (hasJma && hasWs)) {
            const popoverItems = sources.map(s => {
                if (s.kind === 'jma') {
                    const timeLabel = formatCttTime(s.ctt);
                    const reportLabel = isJa ? `JMA速報${timeLabel ? ` (${timeLabel})` : ''} ↗` : `JMA report${timeLabel ? ` (${timeLabel})` : ''} ↗`;
                    return `<a class="quake-jma-popover-item" href="${s.url}" target="_blank" rel="noopener noreferrer">${s.fields.join(', ')} — ${reportLabel}</a>`;
                }
                return `<div class="quake-jma-popover-item quake-jma-popover-static">${s.fields.join(', ')} — ${wqStaticLabel}</div>`;
            }).join('');
            jmaBtnHtml = `<button type="button" class="quake-jma-btn quake-jma-multi" title="${sourcesTitle}">JMA ▾</button>`;
            jmaPopoverHtml = `<div class="quake-jma-popover hidden"><div class="quake-jma-popover-title">${sourcesTitle}</div>${popoverItems}</div>`;
        }

        const mapPin = '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="13" viewBox="0 0 11 13" fill="currentColor"><path d="M5.5 0C3.02 0 1 2.02 1 4.5c0 3.38 4.5 8.5 4.5 8.5s4.5-5.12 4.5-8.5C10 2.02 7.98 0 5.5 0zm0 6.1a1.6 1.6 0 1 1 0-3.2 1.6 1.6 0 0 1 0 3.2z"/></svg>';
        const mapBtnHtml = mapTs
            ? `<button class="quake-map-btn${mapActive ? ' active' : ''}" title="${mapActive ? 'Hide on map' : 'Show on map'}">${mapActive ? '✕' : mapPin}</button>`
            : '';
        const replayIcon = '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="11" viewBox="0 0 10 11" fill="currentColor"><path d="M0 0l10 5.5L0 11z"/></svg>';
        const replayBtnHtml = replayMarker
            ? `<button class="quake-replay-btn" title="${isJa ? 'リプレイで確認' : 'View in replay'}">${replayIcon}</button>`
            : '';

        a.innerHTML =
            badgeHtml +
            `<div class="quake-info">` +
            `<div class="quake-location" title="${loc}">${loc}</div>` +
            `<div class="quake-time">` +
            `<span>${t}</span>` +
            `<div class="quake-time-btns">` + mapBtnHtml + replayBtnHtml + `</div>` +
            `</div>` +
            `<div class="quake-mag">` +
            `<span>${magStr}</span>` +
            `<div class="quake-source-row">` + sourceLabel + jmaBtnHtml + `</div>` +
            `</div>` +
            jmaPopoverHtml +
            `</div>`;

        if (mapTs) {
            a.querySelector('.quake-map-btn').addEventListener('click', e => { handleJmaMapBtn(e, mapTs, mapLat, mapLon); collapseSidebarOnMobile(); });
            a.addEventListener('click', e => {
                if (e.target.closest('.quake-jma-btn') || e.target.closest('.quake-jma-popover') || e.target.closest('.quake-map-btn') || e.target.closest('.quake-replay-btn')) return;
                handleJmaMapBtn(e, mapTs, mapLat, mapLon);
                collapseSidebarOnMobile();
            });
        }
        if (replayMarker) {
            a.querySelector('.quake-replay-btn').addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                // event_ts is the quake's origin time rounded down to the minute by JMA,
                // which can be tens of seconds before the marker was actually broadcast.
                // Jump to just before the broadcast itself (replayMarker.ts) instead, so
                // playback doesn't have to idle through the rest of that minute first.
                jumpReplayTo((replayMarker.ts ?? replayMarker.event_ts) - 5).then(() => { openTimelinePanel(); playReplay(); });
                collapseSidebarOnMobile();
            });
        }
        const multiBtn = a.querySelector('.quake-jma-multi');
        if (multiBtn) {
            multiBtn.addEventListener('click', e => {
                e.preventDefault();
                e.stopPropagation();
                const popover = a.querySelector('.quake-jma-popover');
                document.querySelectorAll('.quake-jma-popover').forEach(p => { if (p !== popover) p.classList.add('hidden'); });
                popover.classList.toggle('hidden');
            });
        }

        renderItems.push({ ts: cluster.ts, el: a });
    });

    renderItems.sort((a, b) => b.ts - a.ts);
    renderItems.forEach(({ el }) => list.appendChild(el));
}

function renderQuakeHistory(quakes) {
    lastQuakeData = quakes;
    renderSidebar(quakes);
}


// ── Audio state ───────────────────────────────────────────────────────
let jmaHistoryReceived = false; // suppresses a chime for the first (cached) jma_history snapshot on connect

// ── EEW / wave clear ──────────────────────────────────────────────────
function clearEewDisplay(key) {
    if (key !== undefined && activeEews.has(key)) {
        activeEews.delete(key);
        removeEpicenterMark(key);
        clearWaveForKey(key);
        ryukyuEpicentre = [...activeEews.values()].some(d => d.lat != null && d.lon != null && isRyukyuCoord(d.lat, d.lon));
        if (activeEews.size > 0) {
            renderEewPanels();
            placeAllEpicenters();
            return;
        }
    } else if (key === undefined) {
        for (const k of [...epicenterMarks.keys()]) {
            if (k !== '__past__') removeEpicenterMark(k);
        }
        activeEews.clear();
    } else {
        // key defined but already removed from activeEews — clean up any lingering wave only
        clearWaveForKey(key);
        return;
    }
    clearWaveAnimation();
    if (!hasPastQuakeData) {
        resetRegionColors();
        ryukyuEpicentre = false;
        zoomAfterClear();
    }
    renderEewPanels();
    if (!liveStationsHidden) {
        document.getElementById('obs-panel').classList.remove('visible');
    }
    document.getElementById('obs-pga-box').style.display   = 'none';
    document.getElementById('obs-pga-label').style.display = 'none';
    niedZoomActive = false;
    niedZoomCoords = null;
    ryukyuStations = false;
    redrawStations();
}

// ── Main message dispatcher ───────────────────────────────────────────
function displayData(data) {
    if (data.type === 'jma_history') {
        const top = data.quakes.length > 0 ? data.quakes[0] : null;
        if (top) {
            const tsSeconds = top.at ? Math.round(new Date(top.at).getTime() / 1000) : null;
            const matchKey = resolveQuakeSoundKey(top.ctt || top.at, tsSeconds);
            const fp = quakeSoundFingerprint({
                at: top.at,
                intensity: (top.maxi && top.maxi !== '0') ? top.maxi : null,
                mag: top.mag,
                depth: parseQuakeDepth(top.cod),
                loc: top.en_anm || top.anm || null,
            });
            const prev = quakeInfoSoundState.get(matchKey);
            if (jmaHistoryReceived && (!prev || prev.fp !== fp) && !muteReport) playSound(sndNewInfo);
            quakeInfoSoundState.set(matchKey, { ts: tsSeconds, fp });
        }
        jmaHistoryReceived = true;
        renderQuakeHistory(data.quakes);
        return;
    }
    if (data.type === 'quake_points_index') {
        jmaPointsIndex = data.events || [];
        renderSidebar(lastQuakeData);
        return;
    }
    if (data.type === 'nied_stations') {
        updateStations(data.stations);
        return;
    }
    if (data.type === 'nied_stations_diff') {
        applyStationsDiff(data.stations, data.removed);
        return;
    }
    if (data.type === 'snet_stations') {
        snetDataTime = Date.now();
        updateSnetStations(data.stations);
        return;
    }
    if (data.type === 'snet_stations_diff') {
        snetDataTime = Date.now();
        applySnetDiff(data.stations, data.removed);
        return;
    }
    if (data.type === 'pswaves') {
        return; // now computed client-side
    }
    if (data.type === 'eew_clear') {
        clearEewDisplay(data.key);
        return;
    }
    if (data.type === 'past_quake_clear') {
        hasPastQuakeData = false;
        activePastQuakeEventId = null;
        activePastQuakeTime = null;
        activePastQuakeAreaIntensities = null;
        pastQuakeStationsShown = false;
        ryukyuEpicentre = false;
        removeEpicenterMark('__past__');
        clearJmaStations();
        renderSidebar(lastQuakeData);
        resetRegionColors();
        zoomAfterClear();
        return;
    }
    if (data.jst_time) {
        document.getElementById('jst-time').textContent = data.jst_time;
    }
    if (data.type === 'earthquake' && data.last_report !== undefined) {
        hasPastQuakeData = false;
        if (jmaSidebarActiveTs !== null || liveStationsHidden) { clearJmaStations(); renderSidebar(lastQuakeData); }
        const eewOrigin = data.origin_time ?? data.quake_time ?? null;
        const key = data.event_id || String(eewOrigin ?? data.report_time ?? Date.now());
        if (!seenEewOrigins.has(key)) {
            seenEewOrigins.add(key);
            if (!muteEewAlert) playSound(sndNewQuake);
        }
        if (!muteEewReport) playSound(sndNewReport);
        if (activeEews.get(key)?.warning) data = { ...data, warning: true };
        activeEews.set(key, data);
        renderEewPanels();
        if (data.lat != null && data.lon != null) {
            placeAllEpicenters();
            if (!data.is_plum) {
                const waveOriginTime = data.origin_time ?? data.quake_time;
                const depthKm = parseFloat(data.depth) || 10;
                startWaveAnimation(key, data.lat, data.lon, depthKm, waveOriginTime);
            }
        }
        if (data.area_intensities && data.area_intensities.length) {
            updateRegionColors(data.area_intensities);
        }
        if (data.max_int && data.max_int !== '0') loadTimelineMarkersAndGaps();
    } else if (data.type === 'earthquake') {
        // Past earthquake info (VXSE51/52/53)
        if (jmaSidebarActiveTs !== null || liveStationsHidden) { clearJmaStations(); renderSidebar(lastQuakeData); }
        const pastKey = data.event_id || data.quake_time;
        if (pastKey) {
            const tsSeconds = data.quake_time ?? null;
            const matchKey = resolveQuakeSoundKey(pastKey, tsSeconds);
            const loc = (data.epi_location_en && data.epi_location_en[0]) || (data.epi_location_jp && data.epi_location_jp[0]) || null;
            const fp = quakeSoundFingerprint({
                quakeTimeSec: data.quake_time,
                intensity: (data.max_int && data.max_int !== '0') ? data.max_int : null,
                mag: data.magnitude,
                depth: data.depth,
                loc,
            });
            const prev = quakeInfoSoundState.get(matchKey);
            if ((!prev || prev.fp !== fp) && !muteReport) playSound(sndNewInfo);
            quakeInfoSoundState.set(matchKey, { ts: tsSeconds, fp });
        }
        if (data.max_int && data.max_int !== '0') loadTimelineMarkersAndGaps();
        hasPastQuakeData = true;
        activePastQuakeEventId = data.event_id || null;
        activePastQuakeTime = data.quake_time || null;
        activePastQuakeAreaIntensities = (data.area_intensities && data.area_intensities.length) ? data.area_intensities : null;
        if (!liveStationsHidden) {
            liveStationsHidden = true;
            document.getElementById('obs-live-hidden').style.display = 'block';
            redrawStations();
        }
        addWsQuakeToSidebar(data);
        // Always resync region colours to this past quake's own report (or clear
        // them if it has none) so a prior EEW's shading doesn't linger.
        if (activePastQuakeAreaIntensities) {
            updateRegionColors(activePastQuakeAreaIntensities);
        } else {
            resetRegionColors();
        }
        if (data.lat != null && data.lon != null) {
            placeMarker('__past__', data.lat, data.lon);
            if (isRyukyuCoord(data.lat, data.lon)) {
                ryukyuEpicentre = true;
                map.flyTo(RYUKYU_CENTER, RYUKYU_ZOOM, { animate: true, duration: 1 });
            } else if (!hasRyukyuActivity()) {
                map.flyTo([data.lat, data.lon], QUAKE_ZOOM, { animate: true, duration: 1 });
            }
        }
        // Per-station intensity points only exist once the VXSE53 report (the one
        // carrying area_intensities) has been processed server-side — fetching on
        // every past-quake message (including the epicentre-only VXSE52) would, during
        // replay, pull in already-persisted station data before that report's own
        // timestamp is reached, showing points/regions too early. Once such a report
        // has been seen for this quake, later follow-ups keep refreshing the points.
        if (data.quake_time && (activePastQuakeAreaIntensities || pastQuakeStationsShown)) {
            const _ts = data.quake_time, _lat = data.lat, _lon = data.lon;
            (async () => {
                try {
                    const resp = await fetch(`/api/quake_points_near/${_ts}`);
                    if (!resp.ok) return;
                    const body = await resp.json();
                    if (!body.stations || !body.stations.length) return;
                    setJmaStations(body.stations);
                    jmaStationsEventId = `near:${_ts}`;
                    jmaStationsVisible = true;
                    jmaSidebarActiveTs = _ts;
                    pastQuakeStationsShown = true;
                    resizeJmaCanvas();
                    redrawJmaStations();
                    if (!activePastQuakeAreaIntensities) {
                        const computed = computeRegionIntensitiesFromStations(jmaStations);
                        if (computed.length) updateRegionColors(computed);
                    }
                    renderSidebar(lastQuakeData);
                    if (!(_lat != null && isRyukyuCoord(_lat, _lon)) && !hasRyukyuActivity()) {
                        const bounds = L.latLngBounds(jmaStations.map(s => [s.lat, s.lon]));
                        if (_lat != null && _lon != null) bounds.extend([_lat, _lon]);
                        map.fitBounds(bounds, { padding: [60, 60], animate: true, duration: 1, maxZoom: 8 });
                    }
                } catch(e) { /* station data unavailable */ }
            })();
        }
    } else if (data.type === 'tsunami') {
        activeTsunamiData = data;
        drawTsunamiCoastlines(data);
        renderTsunamiCard();
        if (data.warning_level === 'Warning' || data.warning_level === 'Major Warning') {
            if (!muteTsunamiWarning) playSound(sndTsunamiWarning);
        } else {
            if (!muteTsunamiAdvisory) playSound(sndNewTsunami);
        }
        if (data.warning_level) loadTimelineMarkersAndGaps();
    } else if (data.type === 'tsunami_obs') {
        activeTsunamiObs = data;
        drawOffshoreObsRegions(data);
        renderTsunamiCard();
    } else if (data.type === 'tsunami_clear') {
        activeTsunamiData = null;
        activeTsunamiObs  = null;
        clearTsunamiCoastlines();
        clearOffshoreObsRegions();
        renderTsunamiCard();
    } else if (data.type === 'tsunami_obs_clear') {
        activeTsunamiObs = null;
        clearOffshoreObsRegions();
        renderTsunamiCard();
    }
}

// ── 48h history replay ─────────────────────────────────────────────────
// Replay re-feeds historical broadcast messages through displayData() — the
// same function live data uses — so it doesn't need a parallel render path.
// While active, the live socket is gated (see createWebSocket's message
// listener) into replayLiveBuffer instead of reaching displayData().
const HISTORY_RETENTION_SECONDS = 48 * 3600; // must match backend HISTORY_RETENTION_SECONDS
// Must match #timeline-range::-webkit-slider-thumb / ::-moz-range-thumb width in style.css.
// Native range inputs travel within [thumbWidth/2, trackWidth - thumbWidth/2], not edge to
// edge, so anything we draw on top (ticks, gap markers) needs the same inset or it visibly
// drifts away from the actual thumb position as you move away from the track's midpoint.
const TIMELINE_THUMB_PX = 16;

// Maps a timestamp to a left-offset percentage that matches where the native
// #timeline-range thumb would sit for that value, given the track's rendered width.
function timelineTsToPercent(ts, from, span, trackWidthPx) {
    const pct = (ts - from) / span;
    if (!trackWidthPx) return pct * 100;
    const usablePx = trackWidthPx - TIMELINE_THUMB_PX;
    const centerPx = TIMELINE_THUMB_PX / 2 + pct * usablePx;
    return (centerPx / trackWidthPx) * 100;
}
let replayActive = false;
let replayPlaying = false;
let replayLoading = false;    // true while jumpReplayTo is fetching the target position's state
let replayClock = null;       // virtual unix ts currently displayed
let replayClockAnchorMs = null; // Date.now() at which replayClock was last set, for sub-second interpolation
let replayBuffer = [];        // upcoming {ts, data} messages, ts ascending
let replayBufferUntil = 0;    // we've fetched window data up to this ts
let replayGen = 0;            // bumped on each jump; stale in-flight fetches discard themselves
let replayRefilling = false;  // a window fetch is in flight; prevents overlapping refills
let replayAbort = null;       // AbortController for the current jump's fetches; aborted when a newer jump starts
let replayLiveBuffer = [];    // live messages received while replay is active
let replayTickTimer = null;
let lockTimelineOnReplay = false;
let hideTimelineFeature = false;
const REPLAY_DISMISS_TYPES = new Set([
    'earthquake', 'tsunami', 'tsunami_obs',
    'eew_clear', 'tsunami_clear', 'tsunami_obs_clear', 'past_quake_clear'
]);

// Sub-second-accurate replay clock for smooth per-frame animation (replayClock
// itself only advances in whole-second ticks).
function currentReplayClock() {
    if (replayClock == null) return null;
    if (!replayPlaying || replayClockAnchorMs == null) return replayClock;
    return replayClock + (Date.now() - replayClockAnchorMs) / 1000;
}

function formatReplayClock(tsSeconds) {
    const d = new Date((tsSeconds + 9 * 3600) * 1000);
    const pad = n => String(n).padStart(2, '0');
    return `${d.getUTCFullYear()}/${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())} JST`;
}

function resetToNeutralState() {
    clearEewDisplay(undefined);
    hasPastQuakeData = false;
    activePastQuakeEventId = null;
    activePastQuakeTime = null;
    activePastQuakeAreaIntensities = null;
    pastQuakeStationsShown = false;
    removeEpicenterMark('__past__');
    clearJmaStations();
    resetRegionColors();
    activeTsunamiData = null;
    activeTsunamiObs = null;
    clearTsunamiCoastlines();
    clearOffshoreObsRegions();
    renderTsunamiCard();
    updateStations([]);
    updateSnetStations([]);
    renderSidebar(lastQuakeData);
}

async function fetchHistoryState(at, signal) {
    try {
        const resp = await fetch(`/api/history/state?at=${at}`, { signal });
        if (!resp.ok) return null;
        return await resp.json();
    } catch { return null; }
}

async function fetchHistoryWindow(from, to, signal) {
    try {
        const resp = await fetch(`/api/history/window?from=${from}&to=${to}`, { signal });
        if (!resp.ok) return [];
        const body = await resp.json();
        return body.messages || [];
    } catch { return []; }
}

async function refillReplayBuffer(span = 600) {
    if (replayRefilling) return; // a fetch is already in flight
    if (replayBufferUntil - replayClock > 120) return; // still plenty buffered
    const from = replayBufferUntil || replayClock;
    const to = Math.min(from + span, Date.now() / 1000);
    if (to <= from) return;
    const gen = replayGen;
    replayRefilling = true;
    try {
        const messages = await fetchHistoryWindow(from, to, replayAbort && replayAbort.signal);
        if (gen !== replayGen) return; // a newer jump superseded this fetch
        replayBuffer.push(...messages);
        replayBufferUntil = to;
    } finally {
        if (gen === replayGen) replayRefilling = false;
    }
}

async function jumpReplayTo(ts) {
    const now = Date.now() / 1000;
    ts = Math.max(now - HISTORY_RETENTION_SECONDS, Math.min(ts, now));
    pauseReplay();
    const gen = ++replayGen;
    if (replayAbort) replayAbort.abort(); // cancel a previous jump's in-flight fetches so they don't hog connections
    replayAbort = new AbortController();
    const signal = replayAbort.signal;
    replayRefilling = false; // abandon any in-flight fetch from a previous jump
    replayLoading = true;
    updateTimelineUI();
    // Only snapshot on the initial jump into replay — later jumps within an
    // already-active replay must not overwrite the snapshot with polluted data.
    if (!replayActive) liveWsQuakeBuffer = new Map(wsQuakeBuffer);
    replayActive = true;
    replayClock = ts;
    replayClockAnchorMs = Date.now();
    replayBuffer = [];
    replayBufferUntil = ts;
    resetToNeutralState();
    // The replay log only stores stations whose readings changed, so seed every
    // known station (from the live cache) as a baseline "no data yet" dot —
    // otherwise the map looks empty until a station happens to report a change.
    updateStations([...liveStationCache.values()].map(s => ({ code: s.code, lat: s.lat, lon: s.lon, int: '0', raw: -3 })));
    updateSnetStations([...liveSnetCache.values()].map(s => ({ code: s.code, lat: s.lat, lon: s.lon, int: '0', raw: -3 })));
    // Fetch the reconstructed state and a small initial playback buffer in
    // parallel. Only block on a 90s buffer so playback can start almost
    // immediately; the rest is prefetched in the background below.
    const statePromise = fetchHistoryState(ts, signal);
    const refillPromise = refillReplayBuffer(90);
    const state = await statePromise;
    if (gen !== replayGen) return; // a newer jump started while we were fetching
    if (state && state.messages) state.messages.forEach(displayData);
    await refillPromise;
    if (gen !== replayGen) return;
    replayLoading = false;
    updateTimelineUI();
    refillReplayBuffer(600); // top up the buffer without blocking the start
}

function replayTick() {
    if (!replayActive || !replayPlaying) return;
    replayClock += 1;
    replayClockAnchorMs = Date.now();
    const now = Date.now() / 1000;
    if (replayClock >= now) {
        exitReplay();
        return;
    }
    while (replayBuffer.length && replayBuffer[0].ts <= replayClock) {
        displayData(replayBuffer.shift().data);
    }
    refillReplayBuffer(); // cheap no-op unless the buffer is running low
    updateTimelineUI();
}

function playReplay() {
    if (!replayActive) return;
    replayPlaying = true;
    if (!replayTickTimer) replayTickTimer = setInterval(replayTick, 1000);
    updateTimelineUI();
}

function pauseReplay() {
    replayPlaying = false;
    if (replayTickTimer) { clearInterval(replayTickTimer); replayTickTimer = null; }
    updateTimelineUI();
}

function stepReplay(deltaSeconds) {
    const target = replayActive ? replayClock + deltaSeconds : Date.now() / 1000 + deltaSeconds;
    jumpReplayTo(target).then(playReplay);
}

function exitReplay() {
    pauseReplay();
    replayGen++; // discard any in-flight buffer fetch from the last jump
    if (replayAbort) { replayAbort.abort(); replayAbort = null; }
    replayRefilling = false;
    const buffered = replayLiveBuffer.slice();
    replayLiveBuffer = [];
    replayActive = false;
    replayClock = null;
    replayBuffer = [];
    // Replay playback overwrites lastQuakeData/wsQuakeBuffer with historical
    // snapshots; restore the true live state before re-rendering the sidebar.
    if (liveQuakeHistory !== null) lastQuakeData = liveQuakeHistory;
    if (liveWsQuakeBuffer !== null) { wsQuakeBuffer.clear(); liveWsQuakeBuffer.forEach((v, k) => wsQuakeBuffer.set(k, v)); }
    liveWsQuakeBuffer = null;
    resetToNeutralState();
    buffered.forEach(displayData);
    // Buffered nied/snet messages may be diffs-only, leaving unchanged
    // stations missing — restore the full live snapshot to be sure.
    updateStations([...liveStationCache.values()]);
    updateSnetStations([...liveSnetCache.values()]);
    updateTimelineUI();
    closeTimelinePanel();
    map.flyTo(DEFAULT_CENTER, DEFAULT_ZOOM, { animate: true, duration: 1 });
}

function enterReplay() {
    if (replayActive) return;
    jumpReplayTo(Date.now() / 1000 - 600);
}

// ── Timeline UI ──────────────────────────────────────────────────────
let timelinePanelOpen = false;
let timelineMarkers = [];
let timelineGaps = [];
let _timelineRefreshTimer = null;

function setLockTimelineOnReplay(checked) {
    lockTimelineOnReplay = checked;
}

function setHideTimelineFeature(checked) {
    hideTimelineFeature = checked;
    const btn = document.getElementById('timeline-btn');
    if (btn) btn.style.display = checked ? 'none' : '';
    if (checked) {
        if (replayActive) exitReplay();
        closeTimelinePanel();
    }
}

function openTimelinePanel() {
    if (hideTimelineFeature) return;
    timelinePanelOpen = true;
    document.getElementById('timeline-panel').classList.add('visible');
    renderTimelineOverlays();
    if (!replayActive) enterReplay();
    else updateTimelineUI();
}

function closeTimelinePanel() {
    timelinePanelOpen = false;
    document.getElementById('timeline-panel').classList.remove('visible');
}

function toggleTimelinePanel() {
    if (timelinePanelOpen) {
        if (replayActive) exitReplay();
        else closeTimelinePanel();
    } else {
        openTimelinePanel();
    }
}

document.addEventListener('keydown', (e) => {
    if (!timelinePanelOpen) return;
    const tag = document.activeElement && document.activeElement.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA') return;
    if (e.key === 'ArrowLeft') {
        e.preventDefault();
        stepReplay(-10);
    } else if (e.key === 'ArrowRight') {
        e.preventDefault();
        stepReplay(10);
    }
});

async function loadTimelineMarkersAndGaps() {
    const now = Date.now() / 1000;
    const from = now - HISTORY_RETENTION_SECONDS;
    try {
        const [mResp, gResp] = await Promise.all([
            fetch(`/api/history/markers?from=${from}&to=${now}`),
            fetch(`/api/history/gaps?from=${from}&to=${now}`)
        ]);
        timelineMarkers = mResp.ok ? (await mResp.json()).markers || [] : [];
        timelineGaps = gResp.ok ? (await gResp.json()).gaps || [] : [];
    } catch { /* keep previous markers/gaps on failure */ }
    renderTimelineOverlays();
    renderSidebar(lastQuakeData);
}

// Gap bars and tick labels are rebuilt only here — on panel open, resize, and
// the periodic marker/gap refresh — not on every 1s replay tick. Their
// positions are anchored to "now", but the drift between refreshes (≤5 min out
// of a 48 h span) is well under a pixel of track width.
function renderTimelineOverlays() {
    renderTimelineMarkers();
    renderTimelineTicks();
}

function renderTimelineMarkers() {
    const now = Date.now() / 1000;
    const from = now - HISTORY_RETENTION_SECONDS;
    const span = now - from;
    const gapLayer = document.getElementById('timeline-gap-layer');
    if (!gapLayer) return;
    const trackWidthPx = gapLayer.getBoundingClientRect().width;
    gapLayer.innerHTML = '';
    timelineGaps.forEach(g => {
        const el = document.createElement('div');
        el.className = 'tl-gap';
        const leftPct = timelineTsToPercent(g.start, from, span, trackWidthPx);
        const rightPct = timelineTsToPercent(g.end, from, span, trackWidthPx);
        el.style.left = `${leftPct}%`;
        el.style.width = `${Math.max(rightPct - leftPct, 0.3)}%`;
        el.title = 'Data gap (backend down or no updates received)';
        gapLayer.appendChild(el);
    });
}

// Finds the most recent quake marker (from the last 48h of recorded history)
// whose origin time is close to `ts`, so the sidebar can offer a "view in
// replay" shortcut for history-list entries that are still in the replay window.
function findReplayMarkerForTs(ts) {
    if (!ts) return null;
    return timelineMarkers.find(m => m.kind === 'quake' && Math.abs((m.event_ts ?? m.ts) - ts) < 90) || null;
}

// Candidate spacings (hours) for the tick labels below the slider, smallest
// first. renderTimelineTicks picks the smallest one whose labels won't
// overlap at the panel's current width, so desktop (wider panel) typically
// gets 3h ticks while a narrow phone screen falls back to 6h/12h/24h.
const TIMELINE_TICK_STEP_HOURS = [3, 6, 12, 24];

function jumpReplayToTick(ts) {
    jumpReplayTo(ts).then(playReplay);
}

function renderTimelineTicks() {
    const layer = document.getElementById('timeline-ticks');
    if (!layer) return;
    const now = Date.now() / 1000;
    const from = now - HISTORY_RETENTION_SECONDS;
    const span = now - from;
    const widthPx = layer.getBoundingClientRect().width;
    if (widthPx <= 0) return;
    const MIN_LABEL_SPACING_PX = 48;
    let stepHours = TIMELINE_TICK_STEP_HOURS[TIMELINE_TICK_STEP_HOURS.length - 1];
    for (const candidate of TIMELINE_TICK_STEP_HOURS) {
        const stepPx = (candidate * 3600 / span) * widthPx;
        if (stepPx >= MIN_LABEL_SPACING_PX) { stepHours = candidate; break; }
    }
    const stepSeconds = stepHours * 3600;
    const JST_OFFSET = 9 * 3600;
    // Align ticks to JST clock boundaries (e.g. 00:00/03:00/06:00) rather than
    // arbitrary offsets from "now", so they read like a real clock.
    const firstJst = Math.ceil((from + JST_OFFSET) / stepSeconds) * stepSeconds;
    layer.innerHTML = '';
    for (let tsJst = firstJst; tsJst - JST_OFFSET <= now; tsJst += stepSeconds) {
        const ts = tsJst - JST_OFFSET;
        const leftPct = timelineTsToPercent(ts, from, span, widthPx);
        const d = new Date(tsJst * 1000);
        const hour = d.getUTCHours();
        const isDayStart = hour === 0;
        const pad = n => String(n).padStart(2, '0');
        const text = isDayStart ? `${pad(d.getUTCMonth() + 1)}/${pad(d.getUTCDate())}` : `${pad(hour)}:00`;
        const btn = document.createElement('button');
        btn.className = 'tl-tick' + (isDayStart ? ' tl-tick-day' : '');
        btn.textContent = text;
        btn.title = formatReplayClock(ts);
        btn.style.left = `${leftPct}%`;
        btn.style.transform = leftPct < 5 ? 'translateX(0)' : leftPct > 95 ? 'translateX(-100%)' : 'translateX(-50%)';
        btn.onclick = () => jumpReplayToTick(ts);
        layer.appendChild(btn);
    }
}

window.addEventListener('resize', () => { if (timelinePanelOpen) renderTimelineOverlays(); });

const TIMELINE_PLAY_ICON_D = 'M8 5v14l11-7z';
const TIMELINE_PAUSE_ICON_D = 'M7 5h4v14H7zM13 5h4v14h-4z';

// state: 'play' | 'pause' | 'loading'. 'loading' covers the gap between jumping to a new
// timeline position and its data actually arriving — without it, that window looked
// identical to a deliberate pause, even though clicking play would do nothing yet.
function setTimelinePlayPauseIcon(state) {
    const btn = document.getElementById('timeline-playpause');
    const playPauseSvg = document.getElementById('timeline-playpause-svg');
    const loadingSvg = document.getElementById('timeline-loading-svg');
    const icon = document.getElementById('timeline-playpause-icon');
    if (!btn || !icon) return;
    if (state === 'loading') {
        playPauseSvg.style.display = 'none';
        loadingSvg.style.display = '';
        btn.setAttribute('aria-label', 'Loading');
    } else {
        playPauseSvg.style.display = '';
        loadingSvg.style.display = 'none';
        icon.setAttribute('d', state === 'pause' ? TIMELINE_PAUSE_ICON_D : TIMELINE_PLAY_ICON_D);
        btn.setAttribute('aria-label', state === 'pause' ? 'Pause' : 'Play');
    }
}

function updateTimelineUI() {
    const slider = document.getElementById('timeline-range');
    const label = document.getElementById('timeline-label');
    const liveBtn = document.getElementById('timeline-live-btn');
    if (!slider) return;
    const now = Date.now() / 1000;
    const from = now - HISTORY_RETENTION_SECONDS;
    slider.min = from;
    slider.max = now;
    if (replayActive && replayClock != null) {
        slider.value = replayClock;
        label.textContent = formatReplayClock(replayClock);
        setTimelinePlayPauseIcon(replayLoading ? 'loading' : (replayPlaying ? 'pause' : 'play'));
        liveBtn.classList.add('tl-replaying');
    } else {
        slider.value = now;
        label.textContent = currentLanguage === 'ja' ? 'ライブ' : 'Live';
        setTimelinePlayPauseIcon(replayLoading ? 'loading' : 'play');
        liveBtn.classList.remove('tl-replaying');
    }
}

let _timelineScrubTimer = null;

function onTimelineScrub(value) {
    const ts = parseFloat(value);
    const label = document.getElementById('timeline-label');
    if (label) label.textContent = formatReplayClock(ts);
    if (_timelineScrubTimer) clearTimeout(_timelineScrubTimer);
    _timelineScrubTimer = setTimeout(() => {
        _timelineScrubTimer = null;
        jumpReplayTo(ts).then(playReplay);
    }, 500);
}

function onTimelinePlayPause() {
    if (!replayActive) { enterReplay(); return; }
    if (replayPlaying) pauseReplay(); else playReplay();
}

