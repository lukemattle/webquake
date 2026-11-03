import env_loader  # noqa: F401 - loads .env into os.environ before config is read
import dmdws, time, os, json, db, threading, requests, io, colorsys, glob, secrets, mimetypes, re, itertools, gzip, math
import numpy as np
import brotli
import history_cache
import websocket_hub
from concurrent.futures import ThreadPoolExecutor
from werkzeug.security import safe_join
from functools import wraps, lru_cache
from datetime import datetime, timezone, timedelta
from enums import eng_codes
from flask import Flask, send_from_directory, request, session, redirect, jsonify, abort
from flask_sock import Sock
from flask_compress import Compress
from simple_websocket.ws import Base as WebsocketBase
from PIL import Image, ImageDraw, ImageFont

mimetypes.add_type('application/geo+json', '.geojson')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
JST = timezone(timedelta(hours=9))
TAIPEI = timezone(timedelta(hours=8))
_station_lookup_path = os.path.join(BASE_DIR, 'jma-stations.json')
with open(_station_lookup_path, encoding='utf-8') as _f:
    _station_lookup = {s['code']: s for s in json.load(_f)}

def _flatten_geometry_rings(geometry):
    """Flatten a GeoJSON Polygon/MultiPolygon into a flat list of rings [[(lon,lat),
    ...], ...]."""
    gtype = geometry.get('type')
    coords = geometry.get('coordinates', [])
    rings = []
    if gtype == 'Polygon':
        rings.extend(coords)
    elif gtype == 'MultiPolygon':
        for polygon in coords:
            rings.extend(polygon)
    return [[(lon, lat) for lon, lat in ring] for ring in rings]

def _ring_bbox(rings):
    lons = [lon for ring in rings for lon, lat in ring]
    lats = [lat for ring in rings for lon, lat in ring]
    return (min(lons), min(lats), max(lons), max(lats))

_japan_regions_path = os.path.join(BASE_DIR, '..', 'public', 'japan-regions.geojson')
with open(_japan_regions_path, encoding='utf-8') as _f:
    _jp_regions_geojson = json.load(_f)
_JP_REGIONS = []
for _feature in _jp_regions_geojson['features']:
    _rings = _flatten_geometry_rings(_feature['geometry'])
    if not _rings:
        continue
    _JP_REGIONS.append({
        'code': _feature['properties'].get('code', ''),
        'rings': _rings,
        'bbox': _ring_bbox(_rings),
    })
_JP_FULL_BOUNDS = (
    min(r['bbox'][0] for r in _JP_REGIONS), min(r['bbox'][1] for r in _JP_REGIONS),
    max(r['bbox'][2] for r in _JP_REGIONS), max(r['bbox'][3] for r in _JP_REGIONS),
)


# --- Configuration ---------------------------------------------------------
# Required environment variables are checked at startup; empty optional values
# disable their features. Notification destinations have no defaults.

_missing_env = []

def _require_env(name):
    """Read a required variable. Missing names are collected, not raised on, so
    that a fresh deployment is told about all of them at once."""
    value = os.environ.get(name, "").strip()
    if not value:
        _missing_env.append(name)
    return value

def _optional_env(name):
    """Read an optional variable. Empty means "this feature is not configured"."""
    return os.environ.get(name, "").strip()

def _check_required_env():
    if _missing_env:
        raise SystemExit(
            "WebQuake cannot start - required environment variable(s) not set:\n"
            + "\n".join(f"  - {name}" for name in _missing_env)
            + "\n\nSee .env.example for the full list and how to obtain each value."
        )

# Keep the session key stable across restarts and workers to preserve logins.
SECRET_KEY = _require_env("WEBQUAKE_SECRET_KEY")

# dmdws reads these credentials; validate them here for one startup error list.
DMDATA_REFRESH_TOKEN = _require_env("WEBQUAKE_DMDATA_REFRESH_TOKEN")
_require_env("WEBQUAKE_DMDATA_CLIENT_ID")
_require_env("WEBQUAKE_DMDATA_CLIENT_SECRET")

# Public URL of this deployment, used for notification click-through links.
SITE_URL = _optional_env("WEBQUAKE_SITE_URL")

# --- OneSignal (hosted web push) config ---
# The app ID is public (it also appears in index.html); the REST key is not.
ONESIGNAL_APP_ID = _optional_env("WEBQUAKE_ONESIGNAL_APP_ID")
ONESIGNAL_REST_API_KEY = _optional_env("WEBQUAKE_ONESIGNAL_REST_API_KEY")

# --- ntfy (self-hosted) config ---
# NTFY_TOKEN is a publish-only access token created on the ntfy server.
NTFY_URL = _optional_env("WEBQUAKE_NTFY_URL")
NTFY_TOPIC_ALERTS_EN = _optional_env("WEBQUAKE_NTFY_TOPIC_ALERTS_EN")
NTFY_TOPIC_ALERTS_JA = _optional_env("WEBQUAKE_NTFY_TOPIC_ALERTS_JA")
NTFY_TOPIC_FORECASTS_EN = _optional_env("WEBQUAKE_NTFY_TOPIC_FORECASTS_EN")
NTFY_TOPIC_FORECASTS_JA = _optional_env("WEBQUAKE_NTFY_TOPIC_FORECASTS_JA")
NTFY_TOKEN = _optional_env("WEBQUAKE_NTFY_TOKEN")
NTFY_ICON_URL = _optional_env("WEBQUAKE_NTFY_ICON_URL")
NTFY_CLICK_URL = SITE_URL

# --- Discord webhook config ---
DISCORD_WEBHOOK_EN = _optional_env("WEBQUAKE_DISCORD_WEBHOOK_EN")
DISCORD_WEBHOOK_JA = _optional_env("WEBQUAKE_DISCORD_WEBHOOK_JA")
# Crossposting requires a bot with Manage Messages permission in the channel.
DISCORD_BOT_TOKEN = _optional_env("WEBQUAKE_DISCORD_BOT_TOKEN")
SITE_LINK_EN = SITE_URL
SITE_LINK_JA = f"{SITE_URL.rstrip('/')}/ja/" if SITE_URL else ""

# --- Admin panel config ---
# An unset password makes all admin routes return 404.
ADMIN_PASSWORD = _optional_env("WEBQUAKE_ADMIN_PASSWORD")
ADMIN_ENABLED = bool(ADMIN_PASSWORD)
ADMIN_DIR = os.path.join(BASE_DIR, 'admin')

# --- Taiwan (CWA) config ---
# CWA opendata requires a personal API key (https://opendata.cwa.gov.tw/user/authkey).
# poll_cwa_reports() logs a warning and never polls if unset.
CWA_API_KEY = _optional_env("WEBQUAKE_CWA_API_KEY")

_check_required_env()

if not ADMIN_ENABLED:
    dmdws.logger.warning("WEBQUAKE_ADMIN_PASSWORD is not set - the admin panel is disabled")

conn = dmdws.Connection(
    refresh_token=DMDATA_REFRESH_TOKEN,
    types=[
        "VXSE43",  # EEW warning (predicted intensity 5− or above).
        "VXSE45",  # EEW forecast.
        "VXSE47",  # PLUM intensity prediction.
        "VXSE51",  # Observed intensity report.
        "VXSE52",  # Epicenter report.
        "VXSE53",  # Epicenter and observed intensity report.
        "VTSE41",  # Tsunami warning/advisory/forecast.
        "VTSE51",  # Coastal tsunami observations.
        "VTSE52"  # Offshore tsunami observations.
    ],
    debug=True  # If you want to see the debug messages, disable in production
)

def _safe_ts(iso_str):
    """Parse an ISO datetime string to a Unix timestamp, or return None."""
    if not iso_str:
        return None
    try:
        return int(datetime.fromisoformat(iso_str).timestamp())
    except (ValueError, TypeError):
        return None

# JMA tsunami kind codes: 警報等情報要素／津波警報・注意報・予報.
# Both 53 (new/upgraded) and 52 (continuing) mean a major warning.
TSUNAMI_KIND_LEVEL = {
    '53': 'Major Warning',  # 大津波警報：発表
    '52': 'Major Warning',  # 大津波警報
    '51': 'Warning',  # 津波警報
    '62': 'Advisory',  # 津波注意報
    '71': 'Slight sea level change',  # 津波予報（若干の海面変動）
    '72': 'Slight sea level change',  # 〃 (after an advisory was lifted)
    '73': 'Slight sea level change',  # 〃 (after a warning was lifted)
}
# Lifted states: 50 = warning, 60 = advisory, 00 = no tsunami.
TSUNAMI_LIFTED_KINDS = {'50', '60', '00'}
# Most severe first. Mirrors TSUNAMI_LEVELS in public/app.js (which is low→high).
_TSUNAMI_LEVEL_ORDER = ['Major Warning', 'Warning', 'Advisory', 'Slight sea level change']

def _tsunami_forecasts_in_force(data):
    """Return forecast areas with an active warning, advisory or forecast.

    JMA includes lifted areas in the same array; exclude them before rendering."""
    forecasts = data.get('body', {}).get('body', {}).get('tsunami', {}).get('forecasts', []) or []
    return [f for f in forecasts
            if ((f.get('kind') or {}).get('code')) not in TSUNAMI_LIFTED_KINDS]

def _tsunami_warning_level(kind_codes):
    """Highest tier still in force across the telegram's forecast areas."""
    levels = {TSUNAMI_KIND_LEVEL.get(code) for code in kind_codes}
    for level in _TSUNAMI_LEVEL_ORDER:
        if level in levels:
            return level
    return 'Slight sea level change'

def _is_tsunami_all_clear(data):
    """Return whether a VTSE41/VTSE51 report retracts all active tsunami levels.

    Retractions (infoType 取消) can omit tsunami data entirely. With forecast
    areas present, all must be lifted: code 0107 only clears warnings and
    advisories, so slight-sea-level forecasts may still be active. Use 0107
    as the fallback only when no forecast areas are supplied."""
    if data.get('body', {}).get('infoType') == '取消':
        return True
    tsunami = data.get('body', {}).get('body', {}).get('tsunami', {}) or {}
    if tsunami.get('forecasts'):
        return not _tsunami_forecasts_in_force(data)
    warning_codes = data.get('body', {}).get('body', {}).get('comments', {}).get('warning', {}).get('codes', []) or []
    return '0107' in warning_codes

def _safe_float(value, default=0.0):
    try:
        return float(value) if value is not None else default
    except (ValueError, TypeError):
        return default

def _summarize_obs_region(region):
    """Return (height, condition, over) for a coastal observation region.

    Prefer the highest numeric station reading, then a qualitative condition.
    Keep the winning station's over flag: a saturated gauge reports a lower
    bound, not an exact height."""
    best_height = 0.0
    best_condition = ''
    best_over = False
    for station in region.get('stations', []):
        max_height = station.get('maxHeight') or {}
        height = max_height.get('height') or {}
        raw_value = height.get('value')
        if raw_value is not None:
            try:
                value = float(raw_value)
                if value > best_height:
                    best_height = value
                    best_over = bool(height.get('over'))
            except (ValueError, TypeError):
                pass
        condition = max_height.get('condition') or max_height.get('status') or ''
        if condition and (not best_condition or best_condition == '欠測'):
            best_condition = condition
    return best_height, ('' if best_height else best_condition), (best_over if best_height else False)

def _extract_coord(data):
    """Return (lat, lon) floats from the hypocenter coordinate object, or (None,
    None)."""
    hypo = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {})
    coord = hypo.get('coordinate', {})
    lat_val = coord.get('latitude', {}).get('value')
    lon_val = coord.get('longitude', {}).get('value')
    if lat_val is not None and lon_val is not None:
        return float(lat_val), float(lon_val)
    return None, None

_INT_RANK = {'0': 0, '1': 1, '2': 2, '3': 3, '4': 4, '5-': 5, '5+': 6, '6-': 7, '6+': 8, '7': 9}

def _max_shindo_from_stations(stations_raw):
    """Return the highest shindo value among per-station readings, or None if there
    are none."""
    best = None
    for st in stations_raw:
        val = st.get('revInt') or st.get('int') or '0'
        if val == '0':
            continue
        if best is None or _INT_RANK.get(val, -1) > _INT_RANK.get(best, -1):
            best = val
    return best

def _resolve_bound(bounds):
    """Resolve a JMA forecast range to one value, or None.

    The to="over" sentinel means "from or above"; use from in that case.
    Applies to both intensity and long-period ground motion forecasts."""
    to = bounds.get('to')
    return bounds.get('from') if to == 'over' else to

def _extract_area_intensities(data):
    """Return list of {code, max_int, is_warning} for each forecast/observed region."""
    regions = data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])
    result = []
    for r in regions:
        # EEW (VXSE43/45/47) uses forecastMaxInt; post-event (VXSE53) uses maxInt
        forecast = r.get('forecastMaxInt', {})
        max_int = (_resolve_bound(forecast)
                   or forecast.get('from')
                   or r.get('maxInt')
                   or '0')
        result.append({
            'code': r.get('code', ''),
            'max_int': max_int,
            'is_warning': r.get('isWarning', False),
            'is_plum': r.get('isPlum', False)
        })
    return result

# Keep this palette in sync with INT_COLORS / INT_TEXT_DARK in public/app.js.
_MAP_INT_COLORS = {
    '0': (224, 224, 224), '1': (160, 216, 239), '2': (0, 0, 255), '3': (0, 204, 0),
    '4': (255, 255, 0), '5-': (255, 153, 0), '5+': (255, 102, 0), '6-': (255, 0, 0),
    '6+': (165, 0, 0), '7': (128, 0, 128),
}
_MAP_INT_TEXT_DARK = {'0', '1', '4', '5-'}
# the only fills light enough to need dark in-region labels
_MAP_REGION_LABEL_DARK = {'1', '4'}
_MAP_UNAFFECTED_FILL = (28, 28, 28)
_MAP_UNAFFECTED_OUTLINE = (51, 51, 51)
_MAP_BACKGROUND = (15, 16, 18)

def _map_int_color(val):
    return _MAP_INT_COLORS.get(val, (85, 85, 85))

def _map_int_text_color(val):
    return (17, 17, 17) if val in _MAP_INT_TEXT_DARK else (255, 255, 255)

def _map_region_label_color(val):
    return (17, 17, 17) if val in _MAP_REGION_LABEL_DARK else (255, 255, 255)

def _point_in_polygon(x, y, poly):
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside

def _dist_to_segment(px, py, x1, y1, x2, y2):
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(px - x1, py - y1)
    t = max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (x1 + t * dx), py - (y1 + t * dy))

def _dist_to_polygon_boundary(x, y, poly):
    n = len(poly)
    return min(_dist_to_segment(x, y, *poly[i], *poly[(i + 1) % n]) for i in range(n))

def _polygon_label_point(poly):
    """Approximate the polygon's 'pole of inaccessibility' via iterative grid search,
    so region labels land inside the shape even for concave/irregular JMA region
    outlines."""
    xs, ys = [p[0] for p in poly], [p[1] for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    fallback = ((minx + maxx) / 2, (miny + maxy) / 2)
    cell = max(maxx - minx, maxy - miny) / 16 or 1
    best_pt = None
    for _ in range(6):
        pass_best, pass_best_dist = None, -1.0
        gx = minx
        while gx <= maxx:
            gy = miny
            while gy <= maxy:
                if _point_in_polygon(gx, gy, poly):
                    d = _dist_to_polygon_boundary(gx, gy, poly)
                    if d > pass_best_dist:
                        pass_best_dist, pass_best = d, (gx, gy)
                gy += cell
            gx += cell
        if pass_best is not None:
            best_pt = pass_best
            minx, maxx = pass_best[0] - cell, pass_best[0] + cell
            miny, maxy = pass_best[1] - cell, pass_best[1] + cell
        cell /= 3
    return best_pt if best_pt is not None else fallback

def _quake_map_bounds(lat, lon, area_intensities):
    """Return (min_lon, min_lat, max_lon, max_lat) to render, centred on the epicenter
    and expanded to cover any regions with a non-zero max_int, with padding."""
    min_lon, max_lon = lon, lon
    min_lat, max_lat = lat, lat
    affected_codes = {a['code'] for a in area_intensities if a.get('max_int') not in (None, '0', 0)}
    if affected_codes:
        for region in _JP_REGIONS:
            if region['code'] not in affected_codes:
                continue
            r_min_lon, r_min_lat, r_max_lon, r_max_lat = region['bbox']
            min_lon, max_lon = min(min_lon, r_min_lon), max(max_lon, r_max_lon)
            min_lat, max_lat = min(min_lat, r_min_lat), max(max_lat, r_max_lat)
    else:
        # Without region data (e.g. PLUM), use a fixed radius around the epicenter.
        min_lon, max_lon = lon - 1.2, lon + 1.2
        min_lat, max_lat = lat - 1.2, lat + 1.2

    # Padding + a minimum span so small/near-field quakes don't render at absurd zoom.
    lon_span = max(max_lon - min_lon, 0.6)
    lat_span = max(max_lat - min_lat, 0.6)
    pad_lon, pad_lat = lon_span * 0.3, lat_span * 0.3
    return (min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)

def _project(lon, lat, bounds, width, height):
    min_lon, min_lat, max_lon, max_lat = bounds
    x = (lon - min_lon) / (max_lon - min_lon) * width
    y = (max_lat - lat) / (max_lat - min_lat) * height  # image Y grows downward
    return x, y

def _inset_bounds_and_size(max_w, max_h):
    """Padded full-Japan bounds, sized to fit within max_w x max_h preserving aspect
    ratio."""
    min_lon, min_lat, max_lon, max_lat = _JP_FULL_BOUNDS
    pad_lon = (max_lon - min_lon) * 0.05
    pad_lat = (max_lat - min_lat) * 0.05
    bounds = (min_lon - pad_lon, min_lat - pad_lat, max_lon + pad_lon, max_lat + pad_lat)
    mean_lat_rad = math.radians((bounds[1] + bounds[3]) / 2)
    lon_scale = max(math.cos(mean_lat_rad), 0.1)
    lon_span = (bounds[2] - bounds[0]) * lon_scale
    lat_span = bounds[3] - bounds[1]
    if lon_span / lat_span > max_w / max_h:
        w, h = max_w, int(max_w * lat_span / lon_span)
    else:
        w, h = int(max_h * lon_span / lat_span), max_h
    return bounds, max(w, 40), max(h, 40)

def _draw_inset_overview(img, view_bounds):
    """Paste a small full-Japan silhouette in the top-right corner with a red box
    marking the region the main image is zoomed into."""
    width, height = img.size
    inset_w, inset_h = 170, 170
    inset_bounds, inset_w, inset_h = _inset_bounds_and_size(inset_w, inset_h)
    margin, panel_pad = 14, 6
    inset_x0 = width - inset_w - margin
    inset_y0 = margin

    draw = ImageDraw.Draw(img)
    draw.rectangle(
        [inset_x0 - panel_pad, inset_y0 - panel_pad, inset_x0 + inset_w + panel_pad, inset_y0 + inset_h + panel_pad],
        fill=(10, 10, 11), outline=(90, 90, 90),
    )

    inset_img = Image.new('RGB', (inset_w, inset_h), (10, 10, 11))
    inset_draw = ImageDraw.Draw(inset_img)
    for region in _JP_REGIONS:
        for ring in region['rings']:
            points = [_project(lon_, lat_, inset_bounds, inset_w, inset_h) for lon_, lat_ in ring]
            if len(points) >= 3:
                inset_draw.polygon(points, fill=(70, 70, 74))

    min_lon, min_lat, max_lon, max_lat = view_bounds
    rx0, ry0 = _project(min_lon, max_lat, inset_bounds, inset_w, inset_h)
    rx1, ry1 = _project(max_lon, min_lat, inset_bounds, inset_w, inset_h)
    rcx, rcy = (rx0 + rx1) / 2, (ry0 + ry1) / 2
    rw, rh = max(abs(rx1 - rx0), 6), max(abs(ry1 - ry0), 6)
    inset_draw.rectangle(
        [rcx - rw / 2, rcy - rh / 2, rcx + rw / 2, rcy + rh / 2],
        outline=(255, 40, 40), width=2,
    )

    img.paste(inset_img, (inset_x0, inset_y0))

def render_quake_map(output_data):
    """Render a schematic PNG of the affected JMA region(s) + epicenter, for Discord
    embeds. Returns PNG bytes, or None if there's no epicenter coordinate to plot."""
    lat, lon = output_data.get('lat'), output_data.get('lon')
    if lat is None or lon is None:
        return None

    area_intensities = output_data.get('area_intensities', [])
    max_int = output_data.get('max_int')

    min_lon, min_lat, max_lon, max_lat = _quake_map_bounds(lat, lon, area_intensities)
    # Correct longitude span for latitude to preserve region shapes.
    mean_lat_rad = math.radians((min_lat + max_lat) / 2)
    lon_scale = max(math.cos(mean_lat_rad), 0.1)
    width = 900
    height = int(width * (max_lat - min_lat) / ((max_lon - min_lon) * lon_scale))
    height = max(500, min(height, 900))
    bounds = (min_lon, min_lat, max_lon, max_lat)

    img = Image.new('RGB', (width, height), _MAP_BACKGROUND)
    draw = ImageDraw.Draw(img)

    int_by_code = {a['code']: a['max_int'] for a in area_intensities if a.get('max_int') not in (None, '0', 0)}
    region_label_font = ImageFont.load_default(size=26)
    for region in _JP_REGIONS:
        r_min_lon, r_min_lat, r_max_lon, r_max_lat = region['bbox']
        if r_max_lon < min_lon or r_min_lon > max_lon or r_max_lat < min_lat or r_min_lat > max_lat:
            continue  # skip regions entirely outside the render bounds
        region_int = int_by_code.get(region['code'])
        fill = _map_int_color(region_int) if region_int else _MAP_UNAFFECTED_FILL
        outline = (17, 17, 17) if region_int else _MAP_UNAFFECTED_OUTLINE
        largest_ring, largest_area = None, -1
        for ring in region['rings']:
            points = [_project(lon_, lat_, bounds, width, height) for lon_, lat_ in ring]
            if len(points) >= 3:
                draw.polygon(points, fill=fill, outline=outline)
                xs, ys = [p[0] for p in points], [p[1] for p in points]
                area = (max(xs) - min(xs)) * (max(ys) - min(ys))
                if area > largest_area:
                    largest_area, largest_ring = area, points
        if region_int and largest_ring:
            lx, ly = _polygon_label_point(largest_ring)
            text_bbox = draw.textbbox((0, 0), region_int, font=region_label_font)
            text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
            draw.text((lx - text_w / 2 - text_bbox[0], ly - text_h / 2 - text_bbox[1]),
                       region_int, fill=_map_region_label_color(region_int), font=region_label_font)

    # Match the app marker with a white cross and black halo.
    cx, cy = _project(lon, lat, bounds, width, height)
    cross_r = 13
    for x0, y0, x1, y1 in ((cx - cross_r, cy - cross_r, cx + cross_r, cy + cross_r),
                           (cx - cross_r, cy + cross_r, cx + cross_r, cy - cross_r)):
        draw.line([(x0, y0), (x1, y1)], fill=(0, 0, 0), width=7)
    for x0, y0, x1, y1 in ((cx - cross_r, cy - cross_r, cx + cross_r, cy + cross_r),
                           (cx - cross_r, cy + cross_r, cx + cross_r, cy - cross_r)):
        draw.line([(x0, y0), (x1, y1)], fill=(255, 255, 255), width=3)

    # Intensity badge: rounded square, bottom-right corner
    badge_size, badge_margin = 64, 14
    bx0 = width - badge_size - badge_margin
    by0 = height - badge_size - badge_margin
    bx1, by1 = bx0 + badge_size, by0 + badge_size
    badge_color = _map_int_color(max_int) if max_int and max_int != 0 else (85, 85, 85)
    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=14, fill=badge_color, outline=(255, 255, 255), width=2)
    label = str(max_int) if max_int else '?'
    font = ImageFont.load_default(size=30)
    text_bbox = draw.textbbox((0, 0), label, font=font)
    text_w, text_h = text_bbox[2] - text_bbox[0], text_bbox[3] - text_bbox[1]
    text_color = _map_int_text_color(max_int) if max_int else (255, 255, 255)
    bcx, bcy = (bx0 + bx1) / 2, (by0 + by1) / 2
    draw.text((bcx - text_w / 2 - text_bbox[0], bcy - text_h / 2 - text_bbox[1]), label, fill=text_color, font=font)

    _draw_inset_overview(img, bounds)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()

def _is_plum_report(data):
    """Identify a PLUM-derived hypothetical hypocenter via 仮定震源要素.

    Per-region isPlum flags also occur in ordinary reports and cannot identify
    a report-level placeholder hypocenter."""
    condition = data.get('body', {}).get('body', {}).get('earthquake', {}).get('condition', '')
    return condition == '仮定震源要素'

def _is_eew_canceled(data):
    """Identify an EEW retraction before parsing normal earthquake fields.

    Cancellations omit earthquake and intensity data; normal parsing would
    create a phantom event instead of clearing the original warning."""
    return data.get('body', {}).get('body', {}).get('isCanceled', False) is True

def _eew_cancel_output(data):
    """Payload for a cancelled EEW: retract the event instead of reporting it.

    The key must match the one `store_recent_data` derived for the original
    reports of this event, which is the eventId when present."""
    event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
    key = event_id or str(_safe_ts(data.get('body', {}).get('targetDateTime')) or '')
    return {'type': 'eew_clear', 'key': key, 'canceled': True}

def _is_flash_report(data):
    """Identify an initial report with a real hypocenter but no intensity yet.

    Check cancellation first: retractions also omit intensity. PLUM reports
    have a hypothetical hypocenter and are classified separately."""
    return 'intensity' not in data.get('body', {}).get('body', {})

def _extract_and_store_stations(event_id, data):
    """Extract per-station intensities from a VXSE53 message and persist to
    quake_stations."""
    if not event_id:
        return
    intensity = data.get('body', {}).get('body', {}).get('intensity', {})
    stations_raw = intensity.get('stations', [])
    rows = []
    for st in stations_raw:
        code = st.get('code', '')
        intensity_val = st.get('revInt') or st.get('int') or '0'
        info = _station_lookup.get(code)
        if info:
            rows.append((event_id, code, info['name'],
                         float(info['lat']),
                         float(info['lon']),
                         intensity_val))
    if rows:
        with db.Database() as cursor:
            cursor.executemany(
                'INSERT OR IGNORE INTO quake_stations (event_id,code,name_jp,lat,lon,intensity) VALUES (?,?,?,?,?,?)',
                rows
            )
            changed = cursor.rowcount
        if changed:
            _invalidate_quake_points_index()

volcanic_origin_event_ids = set()  # Event IDs for eruption-triggered tsunami reports.

def _is_volcanic_origin_report(data):
    """Detect volcanic events in the VXSE53 distant-earthquake template.

    The free-text disclaimer identifies eruption-triggered tsunami advisories
    whose auto-generated headline still describes an overseas earthquake."""
    free_text = data.get('body', {}).get('body', {}).get('comments', {}).get('free', '') or ''
    return '噴火' in free_text


def process_data(data):
    output_data = {}
    if data['head']['type'] == 'VXSE53':  # Epicenter and observed intensity report.
        epi_location_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', ''), 'Unknown')]
        epi_location_jp = [data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('name', 'Unknown')]
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        _origin_time_str = data.get('body', {}).get('body', {}).get('earthquake', {}).get('originTime', '')
        quake_time = int(datetime.fromisoformat(_origin_time_str or data.get('body', {}).get('targetDateTime', '')).timestamp())
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        intensity_body = data.get('body', {}).get('body', {}).get('intensity', {})
        max_int = (intensity_body.get('forecastMaxInt', {}).get('to')
                   or intensity_body.get('forecastMaxInt', {}).get('from')
                   or intensity_body.get('maxInt')
                   or 0)
        if max_int == 'over':
            max_int = intensity_body.get('forecastMaxInt', {}).get('from', 0)
        if not max_int or max_int == '0':
            station_max = _max_shindo_from_stations(intensity_body.get('stations', []))
            if station_max:
                max_int = station_max
        max_lpgm = _resolve_bound(intensity_body.get('forecastMaxLgInt', {})) or 0
        magnitude = data.get('body', {}).get('body', {}).get('earthquake', {}).get('magnitude', {}).get('value', 0.0)
        depth = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('depth', {}).get('value', 0)
        prefectures_en = [eng_codes.prefecture.get(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])[i].get('code', ''), 'Unknown') 
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])))]
        prefectures_jp = [data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])[i].get('name', 'Unknown') 
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])))]
        regions_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])[i].get('code', ''), 'Unknown') 
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])))]
        regions_jp = [data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])[i].get('name', 'Unknown') 
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])))]
        if "0215" in data.get('body', {}).get('body', {}).get('comments', {}).get('forecast', {}).get('codes', []):
            no_tsunami = True 
        else:
            no_tsunami = False
        lat, lon = _extract_coord(data)
        is_volcanic = _is_volcanic_origin_report(data)
        if is_volcanic and event_id:
            volcanic_origin_event_ids.add(event_id)
        elif event_id in volcanic_origin_event_ids:
            # Keep the volcanic classification when later revisions omit the disclaimer.
            is_volcanic = True
        output_data = {
            'type': 'earthquake',
            'event_id': event_id,
            'epi_location_en': epi_location_en,
            'epi_location_jp': epi_location_jp,
            'report_time': report_time,
            'quake_time': quake_time,
            'max_int': max_int,
            'max_lpgm': max_lpgm,
            'magnitude': magnitude,
            'depth': depth,
            'is_volcanic': is_volcanic,
            'prefectures_en': prefectures_en,
            'prefectures_jp': prefectures_jp,
            'regions_en': regions_en,
            'regions_jp': regions_jp,
            'no_tsunami': no_tsunami,
            'lat': lat,
            'lon': lon,
            'area_intensities': _extract_area_intensities(data)
        }
        dmdws.logger.info('Data (VXSE53) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE45' and _is_eew_canceled(data):
        output_data = _eew_cancel_output(data)
        dmdws.logger.info('Data (VXSE45 cancellation) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE45':  # EEW forecast.
        epi_location_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', ''), 'Unknown')]
        epi_location_jp = [data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('name', 'Unknown')]
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        report_num = data.get('body', {}).get('serialNo', 0)
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        origin_time_str = data.get('body', {}).get('body', {}).get('earthquake', {}).get('originTime', '')
        origin_time = int(datetime.fromisoformat(origin_time_str).timestamp()) if origin_time_str else quake_time
        max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('to', 0)
        if max_int == 'over':
            max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('from', 0)
        max_lpgm = _resolve_bound(data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxLgInt', {})) or 0
        last_report = data.get('body', {}).get('body', {}).get('isLastInfo', False)
        magnitude = data.get('body', {}).get('body', {}).get('earthquake', {}).get('magnitude', {}).get('value', 0.0)
        depth = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('depth', {}).get('value', 0)
        _is_warning = data.get('body', {}).get('body', {}).get('isWarning', False)
        warning = _is_warning is True or _is_warning == "true"
        lat, lon = _extract_coord(data)
        output_data = {
            'type': 'earthquake',
            'is_plum': _is_plum_report(data),
            'is_flash': _is_flash_report(data),
            'event_id': event_id,
            'last_report': last_report,
            'epi_location_en': epi_location_en,
            'epi_location_jp': epi_location_jp,
            'report_time': report_time,
            'report_num': report_num,
            'quake_time': quake_time,
            'origin_time': origin_time,
            'max_int': max_int,
            'max_lpgm': max_lpgm,
            'magnitude': magnitude,
            'depth': depth,
            'warning': warning,
            'lat': lat,
            'lon': lon,
            'area_intensities': _extract_area_intensities(data)
        }
        dmdws.logger.info('Data (VXSE45) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE43' and _is_eew_canceled(data):
        output_data = _eew_cancel_output(data)
        dmdws.logger.info('Data (VXSE43 cancellation) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE43':  # EEW warning.
        epi_location_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', ''), 'Unknown')]
        epi_location_jp = [data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('name', 'Unknown')]
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        report_num = data.get('body', {}).get('serialNo', 0)
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        origin_time_str = data.get('body', {}).get('body', {}).get('earthquake', {}).get('originTime', '')
        origin_time = int(datetime.fromisoformat(origin_time_str).timestamp()) if origin_time_str else quake_time
        max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('to', 0)
        if max_int == 'over':
            max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('from', 0)
        max_lpgm = _resolve_bound(data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxLgInt', {})) or 0
        last_report = data.get('body', {}).get('body', {}).get('isLastInfo', False)
        magnitude = data.get('body', {}).get('body', {}).get('earthquake', {}).get('magnitude', {}).get('value', 0.0)
        depth = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('depth', {}).get('value', 0)
        warning = True  # VXSE43 is always a warning by definition
        lat, lon = _extract_coord(data)
        output_data = {
            'type': 'earthquake',
            'is_plum': _is_plum_report(data),
            'is_flash': _is_flash_report(data),
            'event_id': event_id,
            'last_report': last_report,
            'epi_location_en': epi_location_en,
            'epi_location_jp': epi_location_jp,
            'report_time': report_time,
            'report_num': report_num,
            'quake_time': quake_time,
            'origin_time': origin_time,
            'max_int': max_int,
            'max_lpgm': max_lpgm,
            'magnitude': magnitude,
            'depth': depth,
            'warning': warning,
            'lat': lat,
            'lon': lon,
            'area_intensities': _extract_area_intensities(data)
        }
        dmdws.logger.info('Data (VXSE43) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE47' and _is_eew_canceled(data):
        output_data = _eew_cancel_output(data)
        dmdws.logger.info('Data (VXSE47 cancellation) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE47':  # PLUM intensity prediction.
        epi_location_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', ''), 'Unknown')]
        epi_location_jp = [data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('name', 'Unknown')]
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        report_num = data.get('body', {}).get('serialNo', 0)
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        origin_time_str = data.get('body', {}).get('body', {}).get('earthquake', {}).get('originTime', '')
        origin_time = int(datetime.fromisoformat(origin_time_str).timestamp()) if origin_time_str else quake_time
        max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('to', 0)
        if max_int == 'over':
            max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxInt', {}).get('from', 0)
        max_lpgm = _resolve_bound(data.get('body', {}).get('body', {}).get('intensity', {}).get('forecastMaxLgInt', {})) or 0
        last_report = data.get('body', {}).get('body', {}).get('isLastInfo', False)
        magnitude = data.get('body', {}).get('body', {}).get('earthquake', {}).get('magnitude', {}).get('value', 0.0)
        depth = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('depth', {}).get('value', 0)
        _is_warning = data.get('body', {}).get('body', {}).get('isWarning', False)
        warning = _is_warning is True or _is_warning == "true"
        lat, lon = _extract_coord(data)
        output_data = {
            'type': 'earthquake',
            'is_plum': True,
            'event_id': event_id,
            'last_report': last_report,
            'epi_location_en': epi_location_en,
            'epi_location_jp': epi_location_jp,
            'report_time': report_time,
            'report_num': report_num,
            'quake_time': quake_time,
            'origin_time': origin_time,
            'max_int': max_int,
            'max_lpgm': max_lpgm,
            'magnitude': magnitude,
            'depth': depth,
            'warning': warning,
            'lat': lat,
            'lon': lon
        }
        dmdws.logger.info('Data (VXSE47) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE51':  # Earthquake intensity report
        max_int = data.get('body', {}).get('body', {}).get('intensity', {}).get('maxInt', 0)
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        prefectures_en = [eng_codes.prefecture.get(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])[i].get('code', ''), 'Unknown')
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])))]
        prefectures_jp = [data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])[i].get('name', 'Unknown')
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('prefectures', [])))]
        regions_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])[i].get('code', ''), 'Unknown')
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])))]
        regions_jp = [data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])[i].get('name', 'Unknown')
                for i in range(len(data.get('body', {}).get('body', {}).get('intensity', {}).get('regions', [])))]
        output_data = {
            'type': 'earthquake',
            'event_id': event_id,
            'max_int': max_int,
            'report_time': report_time,
            'quake_time': quake_time,
            'prefectures_en': prefectures_en,
            'prefectures_jp': prefectures_jp,
            'regions_en': regions_en,
            'regions_jp': regions_jp
        }
        dmdws.logger.info('Data (VXSE51) processed at %s', time.time())

    elif data['head']['type'] == 'VXSE52':  # Epicenter report.
        epi_location_en = [eng_codes.regions.get(data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', ''), 'Unknown')]
        epi_location_jp = [data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('name', 'Unknown')]
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        _origin_time_str = data.get('body', {}).get('body', {}).get('earthquake', {}).get('originTime', '')
        quake_time = int(datetime.fromisoformat(_origin_time_str or data.get('body', {}).get('targetDateTime', '')).timestamp())
        event_id = data.get('xmlReport', {}).get('head', {}).get('eventId', '')
        magnitude = data.get('body', {}).get('body', {}).get('earthquake', {}).get('magnitude', {}).get('value', 0.0)
        depth = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('depth', {}).get('value', 0)
        # 0215 is the code for no tsunami
        if '0215' in data.get('body', {}).get('body', {}).get('comments', {}).get('forecast', {}).get('codes', []):
            no_tsunami = True
        else:
            no_tsunami = False
        lat, lon = _extract_coord(data)
        output_data = {
            'type': 'earthquake',
            'event_id': event_id,
            'epi_location_en': epi_location_en,
            'epi_location_jp': epi_location_jp,
            'report_time': report_time,
            'quake_time': quake_time,
            'magnitude': magnitude,
            'depth': depth,
            'no_tsunami': no_tsunami,
            'lat': lat,
            'lon': lon
        }
        dmdws.logger.info('Data (VXSE52) processed at %s', time.time())

    elif data['head']['type'] == 'VTSE41':  # Tsunami warning/advisory/forecast
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        forecasts = _tsunami_forecasts_in_force(data)
        region_codes = [f.get('code', '') for f in forecasts]
        regions_en = [eng_codes.coastal_regions.get(f.get('code', ''), 'Unknown') for f in forecasts]
        regions_jp = [f.get('name', 'Unknown') for f in forecasts]
        heights = [_safe_float(f.get('maxHeight', {}).get('height', {}).get('value')) for f in forecasts]
        height_conditions = [f.get('maxHeight', {}).get('height', {}).get('condition', '') for f in forecasts]
        height_over = [bool(f.get('maxHeight', {}).get('height', {}).get('over')) for f in forecasts]
        kind_codes = [f.get('kind', {}).get('code', '') for f in forecasts]
        conditions = [(f.get('firstHeight') or {}).get('condition', '') for f in forecasts]
        arrival_times = [_safe_ts((f.get('firstHeight') or {}).get('arrivalTime')) for f in forecasts]
        warning_level = _tsunami_warning_level(kind_codes)

        output_data = {
            'type': 'tsunami',
            'report_time': report_time,
            'quake_time': quake_time,
            'region_codes': region_codes,
            'regions_en': regions_en,
            'regions_jp': regions_jp,
            'heights': heights,
            'height_conditions': height_conditions,
            'height_over': height_over,
            'kind_codes': kind_codes,
            'conditions': conditions,
            'arrival_times': arrival_times,
            'obs_region_codes': [],
            'obs_regions_en': [],
            'obs_regions_jp': [],
            'obs_heights': [],
            'obs_height_conditions': [],
            'obs_height_over': [],
            'warning_level': warning_level
        }
        if _is_tsunami_all_clear(data):
            output_data = {'type': 'tsunami_clear'}
        dmdws.logger.info('Data (VTSE41) processed at %s', time.time())

    elif data['head']['type'] == 'VTSE51':  # Tsunami information
        report_time = int(datetime.fromisoformat(data.get('body', {}).get('reportDateTime', '')).timestamp())
        quake_time = int(datetime.fromisoformat(data.get('body', {}).get('targetDateTime', '')).timestamp())
        forecasts = _tsunami_forecasts_in_force(data)
        observations = data.get('body', {}).get('body', {}).get('tsunami', {}).get('observations', [])
        region_codes = [f.get('code', '') for f in forecasts]
        regions_en = [eng_codes.coastal_regions.get(f.get('code', ''), 'Unknown') for f in forecasts]
        regions_jp = [f.get('name', 'Unknown') for f in forecasts]
        heights = [_safe_float(f.get('maxHeight', {}).get('height', {}).get('value')) for f in forecasts]
        height_conditions = [f.get('maxHeight', {}).get('height', {}).get('condition', '') for f in forecasts]
        height_over = [bool(f.get('maxHeight', {}).get('height', {}).get('over')) for f in forecasts]
        kind_codes = [f.get('kind', {}).get('code', '') for f in forecasts]
        conditions = [(f.get('firstHeight') or {}).get('condition', '') for f in forecasts]
        arrival_times = [_safe_ts((f.get('firstHeight') or {}).get('arrivalTime')) for f in forecasts]
        obs_region_codes = [o.get('code', '') for o in observations]
        obs_regions_en = [eng_codes.coastal_regions.get(o.get('code', ''), 'Unknown') for o in observations]
        obs_regions_jp = [o.get('name', 'Unknown') for o in observations]
        obs_summaries = [_summarize_obs_region(o) for o in observations]
        obs_heights = [s[0] for s in obs_summaries]
        obs_height_conditions = [s[1] for s in obs_summaries]
        obs_height_over = [s[2] for s in obs_summaries]
        warning_level = _tsunami_warning_level(kind_codes)

        output_data = {
            'type': 'tsunami',
            'report_time': report_time,
            'quake_time': quake_time,
            'region_codes': region_codes,
            'regions_en': regions_en,
            'regions_jp': regions_jp,
            'heights': heights,
            'height_conditions': height_conditions,
            'height_over': height_over,
            'kind_codes': kind_codes,
            'conditions': conditions,
            'arrival_times': arrival_times,
            'obs_region_codes': obs_region_codes,
            'obs_regions_en': obs_regions_en,
            'obs_regions_jp': obs_regions_jp,
            'obs_heights': obs_heights,
            'obs_height_conditions': obs_height_conditions,
            'obs_height_over': obs_height_over,
            'warning_level': warning_level
        }
        if _is_tsunami_all_clear(data):
            output_data = {'type': 'tsunami_clear'}
        dmdws.logger.info('Data (VTSE51) processed at %s', time.time())

    elif data['head']['type'] == 'VTSE52':  # Offshore tsunami observation
        report_time = _safe_ts(data.get('body', {}).get('reportDateTime', '')) or int(time.time())
        observations = data.get('body', {}).get('body', {}).get('tsunami', {}).get('observations', [])
        obs_station_names = [o.get('name', '') for o in observations]
        obs_codes = [o.get('code', '') for o in observations]
        obs_station_names_en = [eng_codes.offshore_stations.get(o.get('code', ''), o.get('name', '')) for o in observations]
        obs_heights = [_safe_float(o.get('maxHeight', {}).get('height', {}).get('value')) for o in observations]
        obs_height_conditions = [o.get('maxHeight', {}).get('height', {}).get('condition', '') for o in observations]
        obs_height_over = [bool(o.get('maxHeight', {}).get('height', {}).get('over')) for o in observations]
        obs_conditions = [(o.get('firstHeight') or {}).get('condition', '') for o in observations]
        # Telegrams omit coordinates; use surveyed or estimated locations in eng_codes.
        obs_locations = [eng_codes.offshore_station_coords.get(o.get('code', '')) for o in observations]
        obs_lats       = [loc[0] if loc else None for loc in obs_locations]
        obs_lons       = [loc[1] if loc else None for loc in obs_locations]
        obs_radii      = [loc[2] if loc else None for loc in obs_locations]
        obs_estimated  = [loc[3] if loc else None for loc in obs_locations]

        output_data = {
            'type': 'tsunami_obs',
            'report_time': report_time,
            'obs_station_names': obs_station_names,
            'obs_station_names_en': obs_station_names_en,
            'obs_codes': obs_codes,
            'obs_heights': obs_heights,
            'obs_height_conditions': obs_height_conditions,
            'obs_height_over': obs_height_over,
            'obs_conditions': obs_conditions,
            'obs_lats': obs_lats,
            'obs_lons': obs_lons,
            'obs_radii_km': obs_radii,
            'obs_loc_estimated': obs_estimated,
        }
        dmdws.logger.info('Data (VTSE52) processed at %s', time.time())

    else:
        dmdws.logger.info('Data type not recognised at %s', time.time())
    return output_data

def on_message(message: dict):
    dmdws.logger.info(message['type'])
    
    if message['type'] == 'data':
        dmdws.logger.info(message['head']['type'])
        dmdws.logger.info(message['body'])

        try:
            output_data = process_data(message)
        except Exception as e:
            dmdws.logger.error('process_data failed for %s: %s', message.get('head', {}).get('type', 'unknown'), e)
            return

        # Broadcast before disk writes or notifications can delay clients.
        store_recent_data(output_data, message)
        send_data_to_all_sockets(output_data)
        store_data(output_data, message)
        if message['head']['type'] in ('VXSE53', 'VXSE52'):
            send_data_to_all_sockets({'type': 'quake_points_index', 'events': _build_quake_points_index()})

        # Notifications and raw/decoded message persistence are off the broadcast path
        _bg_executor.submit(send_alert, output_data)
        _bg_executor.submit(_persist_message, message, output_data)


_persist_seq = itertools.count()  # tie-breaker so same-instant messages can't collide

def _persist_message(message: dict, output_data: dict):
    # Use a unique shared stem to pair raw and decoded files without overwrites.
    stem = f"{time.time():.6f}_{next(_persist_seq)}"
    output_path = os.path.join(BASE_DIR, 'data_messages', message['head']['type'], f"{stem}.json")
    with open(output_path, 'w', encoding='utf-8') as outfile:
        json.dump(message, outfile, ensure_ascii=False, indent=4)
    dmdws.logger.info('Data saved at %s', time.time())

    output_path = os.path.join(BASE_DIR, 'decoded_messages', f"{stem}.json")
    with open(output_path, 'w', encoding='utf-8') as outfile:
        json.dump(output_data, outfile, ensure_ascii=False, indent=4)
    dmdws.logger.info('Data saved at %s', time.time())

def _persisted_file_ts(path):
    """Unix timestamp encoded in a data_messages/decoded_messages filename, or
    None. Handles both the old `<sec>.json` and new `<sec.usec>_<n>.json` forms."""
    try:
        return float(os.path.splitext(os.path.basename(path))[0].split('_')[0])
    except ValueError:
        return None


os.makedirs(os.path.join(BASE_DIR, 'data_messages'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'decoded_messages'), exist_ok=True)
os.makedirs(os.path.join(BASE_DIR, 'logs'), exist_ok=True)
for msg_type in ["VXSE43", "VXSE45", "VXSE47", "VXSE51", "VXSE52", "VXSE53", "VTSE41", "VTSE51", "VTSE52"]:
    os.makedirs(os.path.join(BASE_DIR, 'data_messages', msg_type), exist_ok=True)

with db.Database() as cursor:
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE53 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            epi_location_en TEXT,
            epi_location_jp TEXT,
            report_time INTEGER,
            quake_time INTEGER,
            max_int INTEGER,
            max_lpgm INTEGER,
            magnitude REAL,
            depth INTEGER,
            prefectures_en TEXT,
            prefectures_jp TEXT,
            regions_en TEXT,
            regions_jp TEXT,
            no_tsunami INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE45 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_report INTEGER,
            epi_location_en TEXT,
            epi_location_jp TEXT,
            report_time INTEGER,
            report_num INTEGER,
            quake_time INTEGER,
            max_int INTEGER,
            max_lpgm INTEGER,
            magnitude REAL,
            depth INTEGER,
            sea INTEGER,
            warning INTEGER,
            tsunami_possible INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE43 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_report INTEGER,
            epi_location_en TEXT,
            epi_location_jp TEXT,
            report_time INTEGER,
            report_num INTEGER,
            quake_time INTEGER,
            max_int INTEGER,
            max_lpgm INTEGER,
            magnitude REAL,
            depth INTEGER,
            sea INTEGER,
            warning INTEGER,
            tsunami_possible INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE47 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            last_report INTEGER,
            epi_location_en TEXT,
            epi_location_jp TEXT,
            report_time INTEGER,
            report_num INTEGER,
            quake_time INTEGER,
            max_int INTEGER,
            max_lpgm INTEGER,
            magnitude REAL,
            depth INTEGER,
            sea INTEGER,
            warning INTEGER,
            tsunami_possible INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE51 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            max_int INTEGER,
            report_time INTEGER,
            quake_time INTEGER,
            prefectures_en TEXT,
            prefectures_jp TEXT,
            regions_en TEXT,
            regions_jp TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VXSE52 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            epi_location_en TEXT,
            epi_location_jp TEXT,
            report_time INTEGER,
            quake_time INTEGER,
            magnitude REAL,
            depth INTEGER,
            no_tsunami INTEGER
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VTSE41 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_time INTEGER,
            quake_time INTEGER,
            regions_en TEXT,
            regions_jp TEXT,
            heights TEXT,
            warning_level TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS VTSE51 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_time INTEGER,
            quake_time INTEGER,
            regions_en TEXT,
            regions_jp TEXT,
            heights TEXT,
            arrival_times TEXT,
            obs_regions_en TEXT,
            obs_regions_jp TEXT,
            obs_heights TEXT,
            warning_level TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quake_stations (
            event_id TEXT,
            code TEXT,
            name_jp TEXT,
            lat REAL,
            lon REAL,
            intensity TEXT,
            PRIMARY KEY (event_id, code)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS quake_epicenters (
            event_id TEXT PRIMARY KEY,
            lat REAL,
            lon REAL,
            timestamp INTEGER
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS backfill_meta (
            key TEXT PRIMARY KEY,
            value REAL
        )
    ''')
    # CWA reports use integer EarthquakeNo keys and explicit OriginTime values;
    # JMA tables derive timestamps from JST event IDs, so keep them separate.
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tw_quake_epicenters (
            earthquake_no INTEGER PRIMARY KEY,
            lat REAL,
            lon REAL,
            depth REAL,
            magnitude REAL,
            location TEXT,
            timestamp INTEGER,
            web TEXT,
            location_zh TEXT
        )
    ''')
    try:
        cursor.execute('ALTER TABLE tw_quake_epicenters ADD COLUMN web TEXT')
    except Exception:
        pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE tw_quake_epicenters ADD COLUMN location_zh TEXT')
    except Exception:
        pass  # Column already exists
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tw_quake_stations (
            earthquake_no INTEGER,
            station_id TEXT,
            name TEXT,
            lat REAL,
            lon REAL,
            intensity TEXT,
            PRIMARY KEY (earthquake_no, station_id)
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_tw_qe_ts ON tw_quake_epicenters(timestamp)')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS history_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL NOT NULL,
            msg_type TEXT NOT NULL,
            payload TEXT,
            marker_kind TEXT,
            marker_label TEXT
        )
    ''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_log_ts ON history_log(ts)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_log_type_ts ON history_log(msg_type, ts)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_history_log_marker_ts ON history_log(ts) WHERE marker_kind IS NOT NULL')

    # Migrations: add columns that may be missing from older databases
    for table in ('VXSE43', 'VXSE45', 'VXSE47'):
        try:
            cursor.execute(f'ALTER TABLE {table} ADD COLUMN report_num INTEGER')
        except Exception:
            pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE history_log ADD COLUMN event_ts REAL')
    except Exception:
        pass  # Column already exists
    try:
        cursor.execute('ALTER TABLE quake_epicenters ADD COLUMN timestamp INTEGER')
    except Exception:
        pass
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_qe_ts ON quake_epicenters(timestamp)')
    # Backfill timestamp for any rows that predate this column
    cursor.execute('SELECT event_id FROM quake_epicenters WHERE timestamp IS NULL')
    for (eid,) in cursor.fetchall():
        try:
            _ts = int(datetime.strptime(eid, '%Y%m%d%H%M%S').replace(tzinfo=JST).timestamp())
            cursor.execute('UPDATE quake_epicenters SET timestamp = ? WHERE event_id = ?', (_ts, eid))
        except Exception:
            pass

def _store_epicenter(event_id, lat, lon):
    if not event_id or lat is None or lon is None:
        return
    try:
        ts = int(datetime.strptime(event_id, '%Y%m%d%H%M%S').replace(tzinfo=JST).timestamp())
    except Exception:
        ts = None
    with db.Database() as cursor:
        cursor.execute(
            'INSERT OR IGNORE INTO quake_epicenters (event_id, lat, lon, timestamp) VALUES (?, ?, ?, ?)',
            (event_id, lat, lon, ts)
        )
        changed = cursor.rowcount
    if changed:
        _invalidate_quake_points_index()

# Backfill only files newer than each telegram type's saved high-water mark.
# Live inserts use OR IGNORE, so overlap with incoming messages is harmless.
def _backfill_stations():
    with db.Database() as cursor:
        cursor.execute('SELECT key, value FROM backfill_meta')
        marks = dict(cursor.fetchall())
    for msg_type in ('VXSE53', 'VXSE52'):
        mark_key = f'backfill_{msg_type}'
        mark = marks.get(mark_key) or 0
        newest = mark
        for path in glob.glob(os.path.join(BASE_DIR, 'data_messages', msg_type, '*.json')):
            file_ts = _persisted_file_ts(path)
            if file_ts is not None and file_ts <= mark:
                continue
            try:
                with open(path, encoding='utf-8') as f:
                    msg = json.load(f)
                event_id = msg.get('xmlReport', {}).get('head', {}).get('eventId', '')
                if msg_type == 'VXSE53':
                    _extract_and_store_stations(event_id, msg)
                _store_epicenter(event_id, *_extract_coord(msg))
            except Exception:
                continue
            if file_ts is not None:
                newest = max(newest, file_ts)
        if newest > mark:
            with db.Database() as cursor:
                cursor.execute('INSERT OR REPLACE INTO backfill_meta (key, value) VALUES (?, ?)',
                               (mark_key, newest))

threading.Thread(target=_backfill_stations, daemon=True).start()

def store_data(output_data, data):
    table_name = f"{data['head']['type'].upper()}"

    # Cancellations omit earthquake data; storing them would create phantom events.
    if output_data.get('type') == 'eew_clear':
        dmdws.logger.info('Skipping %s insert for cancellation report', table_name)
        return

    dmdws.logger.info(f"Storing data in table: {table_name}")

    if table_name == 'VXSE53':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE53 (
                    epi_location_en, epi_location_jp, report_time, quake_time, max_int, max_lpgm,
                    magnitude, depth, prefectures_en, prefectures_jp, regions_en, regions_jp, no_tsunami
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                ', '.join(output_data.get('epi_location_en', [])) if output_data.get('epi_location_en') else None,
                ', '.join(output_data.get('epi_location_jp', [])) if output_data.get('epi_location_jp') else None,
                output_data.get('report_time', None), output_data.get('quake_time', None),
                output_data.get('max_int', None), output_data.get('max_lpgm', None),
                output_data.get('magnitude', None), output_data.get('depth', None),
                ', '.join(output_data.get('prefectures_en', [])) if output_data.get('prefectures_en') else None,
                ', '.join(output_data.get('prefectures_jp', [])) if output_data.get('prefectures_jp') else None,
                ', '.join(output_data.get('regions_en', [])) if output_data.get('regions_en') else None,
                ', '.join(output_data.get('regions_jp', [])) if output_data.get('regions_jp') else None,
                output_data.get('no_tsunami', None)
            ))
        _extract_and_store_stations(output_data.get('event_id', ''), data)
        _store_epicenter(output_data.get('event_id', ''), output_data.get('lat'), output_data.get('lon'))
    elif table_name == 'VXSE45':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE45 (
                    last_report, epi_location_en, epi_location_jp, report_time, report_num, quake_time, max_int, max_lpgm,
                    magnitude, depth, warning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('last_report', None),
                ', '.join(output_data.get('epi_location_en', [])) if output_data.get('epi_location_en') else None,
                ', '.join(output_data.get('epi_location_jp', [])) if output_data.get('epi_location_jp') else None,
                output_data.get('report_time', None), output_data.get('report_num', None), output_data.get('quake_time', None),
                output_data.get('max_int', None), output_data.get('max_lpgm', None),
                output_data.get('magnitude', None), output_data.get('depth', None),
                output_data.get('warning', None)
            ))
    elif table_name == 'VXSE43':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE43 (
                    last_report, epi_location_en, epi_location_jp, report_time, report_num, quake_time, max_int, max_lpgm,
                    magnitude, depth, warning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('last_report', None),
                ', '.join(output_data.get('epi_location_en', [])) if output_data.get('epi_location_en') else None,
                ', '.join(output_data.get('epi_location_jp', [])) if output_data.get('epi_location_jp') else None,
                output_data.get('report_time', None), output_data.get('report_num', None), output_data.get('quake_time', None),
                output_data.get('max_int', None), output_data.get('max_lpgm', None),
                output_data.get('magnitude', None), output_data.get('depth', None),
                output_data.get('warning', None)
            ))
    elif table_name == 'VXSE47':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE47 (
                    last_report, epi_location_en, epi_location_jp, report_time, report_num, quake_time, max_int, max_lpgm,
                    magnitude, depth, warning
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('last_report', None),
                ', '.join(output_data.get('epi_location_en', [])) if output_data.get('epi_location_en') else None,
                ', '.join(output_data.get('epi_location_jp', [])) if output_data.get('epi_location_jp') else None,
                output_data.get('report_time', None), output_data.get('report_num', None), output_data.get('quake_time', None),
                output_data.get('max_int', None), output_data.get('max_lpgm', None),
                output_data.get('magnitude', None), output_data.get('depth', None),
                output_data.get('warning', None)
            ))
    elif table_name == 'VXSE51':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE51 (
                    max_int, report_time, quake_time, prefectures_en, prefectures_jp, regions_en, regions_jp
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('max_int', None), output_data.get('report_time', None),
                output_data.get('quake_time', None),
                ', '.join(output_data.get('prefectures_en', [])) if output_data.get('prefectures_en') else None,
                ', '.join(output_data.get('prefectures_jp', [])) if output_data.get('prefectures_jp') else None,
                ', '.join(output_data.get('regions_en', [])) if output_data.get('regions_en') else None,
                ', '.join(output_data.get('regions_jp', [])) if output_data.get('regions_jp') else None
            ))
    elif table_name == 'VXSE52':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VXSE52 (
                    epi_location_en, epi_location_jp, report_time, quake_time, magnitude, depth, no_tsunami
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                ', '.join(output_data.get('epi_location_en', [])) if output_data.get('epi_location_en') else None,
                ', '.join(output_data.get('epi_location_jp', [])) if output_data.get('epi_location_jp') else None,
                output_data.get('report_time', None), output_data.get('quake_time', None),
                output_data.get('magnitude', None), output_data.get('depth', None),
                output_data.get('no_tsunami', None)
            ))
        _store_epicenter(output_data.get('event_id', ''), output_data.get('lat'), output_data.get('lon'))
    elif table_name == 'VTSE41':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VTSE41 (
                    report_time, quake_time, regions_en, regions_jp, heights, warning_level
                ) VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('report_time', None), output_data.get('quake_time', None),
                ', '.join(output_data.get('regions_en', [])) if output_data.get('regions_en') else None,
                ', '.join(output_data.get('regions_jp', [])) if output_data.get('regions_jp') else None,
                ', '.join(str(h) for h in output_data.get('heights', [])) if output_data.get('heights') else None,
                output_data.get('warning_level', None)
            ))
    elif table_name == 'VTSE51':
        with db.Database() as cursor:
            cursor.execute('''
                INSERT INTO VTSE51 (
                    report_time, quake_time, regions_en, regions_jp, heights, arrival_times, obs_regions_en, obs_regions_jp,
                    obs_heights, warning_level
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                output_data.get('report_time', None), output_data.get('quake_time', None),
                ', '.join(output_data.get('regions_en', [])) if output_data.get('regions_en') else None,
                ', '.join(output_data.get('regions_jp', [])) if output_data.get('regions_jp') else None,
                ', '.join(str(h) for h in output_data.get('heights', [])) if output_data.get('heights') else None,
                ', '.join(str(h) for h in output_data.get('arrival_times', [])) if output_data.get('arrival_times') else None,
                ', '.join(output_data.get('obs_regions_en', [])) if output_data.get('obs_regions_en') else None,
                ', '.join(output_data.get('obs_regions_jp', [])) if output_data.get('obs_regions_jp') else None,
                ', '.join(str(h) for h in output_data.get('obs_heights', [])) if output_data.get('obs_heights') else None,
                output_data.get('warning_level', None)
            ))
    else:
        dmdws.logger.warning('No table_name was matched, got: %s', table_name)



app = Flask('webquake')
app.secret_key = SECRET_KEY
app.permanent_session_lifetime = timedelta(days=7)
app.config['COMPRESS_MIMETYPES'] = [
    'text/html', 'text/css', 'text/plain', 'text/xml', 'text/javascript',
    'application/javascript', 'application/json', 'application/manifest+json',
    'application/geo+json', 'image/svg+xml',
]
# Include gzip alongside br/zstd for older clients receiving streamed files.
app.config['COMPRESS_ALGORITHM_STREAMING'] = ['zstd', 'br', 'gzip', 'deflate']
Compress(app)
app.config['SOCK_SERVER_OPTIONS'] = {'max_message_size': websocket_hub.MAX_MESSAGE_SIZE}
sock = Sock(app)

open_sockets = websocket_hub.WebSocketHub()
# cloudflared connects locally and sets CF-Connecting-IP. Other immediate proxies
# must be explicitly configured; never trust visitor-supplied forwarding headers.
_ws_trusted_proxies = websocket_hub.proxy_networks(os.environ.get(
    'WEBQUAKE_WS_TRUSTED_PROXY_CIDRS', '127.0.0.1/32,::1/128'
))
_bg_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='bg-task')

# Reuse HTTP connections per thread without sharing mutable Session state.
_http_local = threading.local()

def _http_session() -> requests.Session:
    s = getattr(_http_local, 'session', None)
    if s is None:
        s = requests.Session()
        _http_local.session = s
    return s

_HISTORY_THROTTLE_TYPES = {'nied_stations', 'nied_stations_diff', 'snet_stations', 'snet_stations_diff', 'exptech_stations', 'exptech_stations_diff'}
_last_history_write: dict = {}  # msg_type -> last written ts, for throttled categories
HISTORY_RETENTION_SECONDS = 48 * 3600
# Record periodic full snapshots for replay without broadcasting them.
# This bounds reconstruction to one keyframe interval of station diffs.
HISTORY_STATION_KEYFRAME_SECONDS = 300
_last_station_keyframe: dict = {}  # msg_type -> ts of last recorded keyframe

# Serve replay reads from RAM to avoid contention with SQLite writes.
# Disable this cache on memory-constrained servers.
HISTORY_CACHE_ENABLED = True
_history_cache = history_cache.HistoryCache()

def _history_marker_for(data: dict):
    # event_ts is the event's origin time; the history row ts is its receipt time.
    if data.get('type') == 'earthquake':
        max_int = data.get('max_int')
        if max_int and max_int != '0':
            event_ts = data.get('origin_time') or data.get('quake_time')
            return 'quake', max_int, event_ts
    elif data.get('type') == 'tsunami':
        level = data.get('warning_level')
        if level:
            return 'tsunami', level, None  # tsunami markers jump to report time
    return None, None, None

def _record_history(data: dict, json_data: str):
    msg_type = data.get('type')
    if not msg_type:
        return  # e.g. plain jst_time ticks carry no 'type' and have no replay value
    now = time.time()
    if msg_type in _HISTORY_THROTTLE_TYPES:
        last = _last_history_write.get(msg_type, 0)
        if now - last < 1:
            return
        _last_history_write[msg_type] = now
    marker_kind, marker_label, event_ts = _history_marker_for(data)
    try:
        with db.Database() as cursor:
            cursor.execute(
                'INSERT INTO history_log (ts, msg_type, payload, marker_kind, marker_label, event_ts) VALUES (?, ?, ?, ?, ?, ?)',
                (now, msg_type, json_data, marker_kind, marker_label, event_ts)
            )
    except Exception as e:
        dmdws.logger.warning('Failed to record history_log entry: %s', e)
    if HISTORY_CACHE_ENABLED:
        _history_cache.add(now, msg_type, json_data, marker_kind, marker_label, event_ts)

def send_data_to_all_sockets(data: dict):
    json_data = json.dumps(data, separators=(',', ':'))
    _bg_executor.submit(_record_history, data, json_data)
    open_sockets.broadcast(json_data)

def _maybe_record_station_keyframe(msg_type: str, stations: list):
    """Periodically record a full station snapshot to the replay history without
    broadcasting it to clients, so state reconstruction only replays diffs since
    the most recent keyframe instead of all the way back to server start."""
    now = time.time()
    if now - _last_station_keyframe.get(msg_type, 0) < HISTORY_STATION_KEYFRAME_SECONDS:
        return
    _last_station_keyframe[msg_type] = now
    data = {'type': msg_type, 'stations': stations}
    _bg_executor.submit(_record_history, data, json.dumps(data, separators=(',', ':')))

def send_jst_time():
    while True:
        jst_time = time.strftime('%Y/%m/%d %H:%M:%S JST', time.localtime(time.time() + 9 * 3600))
        send_data_to_all_sockets({'jst_time': jst_time})
        time.sleep(1)

threading.Thread(target=send_jst_time, daemon=True).start()

# --- 48h history log: heartbeat (for gap detection) + pruning ---
def _history_heartbeat_loop():
    while True:
        try:
            now = time.time()
            with db.Database() as cursor:
                cursor.execute(
                    "INSERT INTO history_log (ts, msg_type, payload, marker_kind, marker_label) VALUES (?, 'heartbeat', NULL, NULL, NULL)",
                    (now,)
                )
            if HISTORY_CACHE_ENABLED:
                _history_cache.add(now, 'heartbeat', None)
        except Exception as e:
            dmdws.logger.warning('Failed to record history heartbeat: %s', e)
        time.sleep(10)

def _history_prune_loop():
    while True:
        try:
            cutoff = time.time() - HISTORY_RETENTION_SECONDS
            with db.Database() as cursor:
                cursor.execute('DELETE FROM history_log WHERE ts < ?', (cutoff,))
            if HISTORY_CACHE_ENABLED:
                _history_cache.prune(cutoff)
        except Exception as e:
            dmdws.logger.warning('Failed to prune history_log: %s', e)
        time.sleep(600)

def _load_history_cache():
    """Rebuilds the in-memory history cache from SQLite on startup so the
    48h-old replay window survives a process restart (the cache itself is
    volatile/RAM-only)."""
    if not HISTORY_CACHE_ENABLED:
        dmdws.logger.info('History replay cache disabled (HISTORY_CACHE_ENABLED=False); replay reads will use SQLite directly')
        return
    dmdws.logger.info('History replay cache: migrating up to %dh of history_log from SQLite into RAM...', HISTORY_RETENTION_SECONDS / 3600)
    started = time.time()
    try:
        cutoff = started - HISTORY_RETENTION_SECONDS
        with db.Database() as cursor:
            cursor.execute(
                'SELECT ts, msg_type, payload, marker_kind, marker_label, event_ts FROM history_log '
                'WHERE ts >= ? ORDER BY ts ASC',
                (cutoff,)
            )
            rows = cursor.fetchall()
        _history_cache.load_from_db(rows)
        elapsed = time.time() - started
        _history_cache.load_seconds = elapsed
        _history_cache.load_error = None
        _history_cache.loaded_at = time.time()
        dmdws.logger.info('History replay cache: migration complete — %d rows loaded in %.3fs', len(rows), elapsed)
    except Exception as e:
        _history_cache.load_seconds = time.time() - started
        _history_cache.load_error = str(e)
        dmdws.logger.warning('History replay cache: migration FAILED after %.3fs, falling back to SQLite for replay reads: %s', _history_cache.load_seconds, e)

threading.Thread(target=_load_history_cache, daemon=True).start()
threading.Thread(target=_history_heartbeat_loop, daemon=True).start()
threading.Thread(target=_history_prune_loop, daemon=True).start()

# --- NIED kmoni polling ---
KMONI_REALTIME = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_s/{date}/{ts}.jma_s.gif"
KMONI_PGA      = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/acmap_s/{date}/{ts}.acmap_s.gif"
KMONI_BOREHOLE = "http://www.kmoni.bosai.go.jp/data/map_img/RealTimeImg/jma_b/{date}/{ts}.jma_b.gif"

_prev_nied: dict = {}  # code -> (int_str, raw_float, pga_or_None)
_last_nied_full: list = []

_nied_stations_path = os.path.join(BASE_DIR, 'nied_stations.json')
with open(_nied_stations_path, encoding='utf-8') as _f:
    NIED_STATIONS = json.load(_f)
# Pre-computed pixel index arrays for vectorised gather (see _nied_decode_and_send).
# Note numpy indexes as arr[row, col] = arr[py, px], so PY/PX order is deliberate.
NIED_PX = np.array([s['px'] for s in NIED_STATIONS])
NIED_PY = np.array([s['py'] for s in NIED_STATIONS])

@lru_cache(maxsize=1024)
def _color_to_shindo(r, g, b):
    # Filter near-black (ocean/land background) and near-white (no-data marker)
    if r < 15 and g < 15 and b < 15: return None
    if r > 230 and g > 230 and b > 230: return None

    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    # Thresholds from the JQuake developer's algorithm:
    # https://qiita.com/NoneType1/items/a4d2cf932e20b56ca444
    if v <= 0.1 or s <= 0.75: return None

    # Piecewise polynomial: hue h (0-1) → position p (0-1) on the kmoni colour scale
    if h > 0.1476:
        p = (280.31*h**6 - 916.05*h**5 + 1142.6*h**4
             - 709.95*h**3 + 234.65*h**2 - 40.27*h + 3.2217)
    elif h > 0.001:
        p = 151.4*h**4 - 49.32*h**3 + 6.753*h**2 - 2.481*h + 0.9033
    else:
        # Very-dark-red region (near h=0): position determined by brightness
        p = -0.005171*v**2 - 0.3282*v + 1.2236

    p = max(0.0, min(1.0, p))

    # Instrumental intensity: I = 10p − 3  (range −3 to +7)
    intensity = 10 * p - 3

    # Map to discrete JMA Shindo scale
    # Threshold for Shindo 1 is 0.75 (not 0.5) to match JQuake's behaviour
    if intensity < 0.75: return '0', intensity
    if intensity < 1.5:  return '1', intensity
    if intensity < 2.5:  return '2', intensity
    if intensity < 3.5:  return '3', intensity
    if intensity < 4.5:  return '4', intensity
    if intensity < 5.0:  return '5-', intensity
    if intensity < 5.5:  return '5+', intensity
    if intensity < 6.0:  return '6-', intensity
    if intensity < 6.5:  return '6+', intensity
    return '7', intensity


@lru_cache(maxsize=1024)
def _color_to_pga(r, g, b):
    """Convert an acmap_s GIF pixel to PGA in gal.
    Uses the same hue polynomial as _color_to_shindo; position maps to
    log-scale gal: log10(pga) = -2 + 5*p → pga ∈ [0.01, 1000] gal.
    """
    if r < 15 and g < 15 and b < 15: return None
    if r > 230 and g > 230 and b > 230: return None
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v <= 0.1 or s <= 0.75: return None
    if h > 0.1476:
        p = (280.31*h**6 - 916.05*h**5 + 1142.6*h**4
             - 709.95*h**3 + 234.65*h**2 - 40.27*h + 3.2217)
    elif h > 0.001:
        p = 151.4*h**4 - 49.32*h**3 + 6.753*h**2 - 2.481*h + 0.9033
    else:
        p = -0.005171*v**2 - 0.3282*v + 1.2236
    p = max(0.0, min(1.0, p))
    return min(round(10 ** (-2 + 5 * p), 4), 1000.0)


def _get_jst_parts(offset=0):
    t = time.gmtime(time.time() + 9*3600 - offset)
    return time.strftime('%Y%m%d', t), time.strftime('%Y%m%d%H%M%S', t)

def _fetch_gif(url, timeout=3):
    try:
        r = _http_session().get(url, timeout=timeout)
        if r.status_code == 200:
            return Image.open(io.BytesIO(r.content)).convert('RGB')
    except Exception:
        pass
    return None

# Fetch PGA and borehole images concurrently with persistent HTTP sessions.
_nied_fetch_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix='nied-fetch')

# Latest jma_s result shared between the two NIED polling threads.
# Written by poll_nied_jma, read by poll_nied_pga.
# Tuple assignment is atomic in CPython so no lock needed.
_nied_jma_state = None  # (ts, date, img) or None
# highest successfully fetched ts as int; prevents backward time travel
_nied_latest_ts = 0

def _nied_decode_and_send(img, img_pga, img_borehole=None):
    global _prev_nied, _last_nied_full
    is_first = not _prev_nied
    stations_out = []
    changed = []
    current_codes = set()
    # Gather all station pixels in NumPy to avoid per-station PIL calls.
    pix = np.asarray(img)[NIED_PY, NIED_PX].tolist()

    def _gather(im):
        # Secondary maps (pga/borehole) are best-effort: a malformed or
        # wrong-sized image must not abort the whole shindo decode.
        if im is None:
            return None
        try:
            return np.asarray(im)[NIED_PY, NIED_PX].tolist()
        except Exception:
            return None
    pix_pga = _gather(img_pga)
    pix_bore = _gather(img_borehole)
    for i, st in enumerate(NIED_STATIONS):
        ri, gi, bi = pix[i][0], pix[i][1], pix[i][2]
        result = _color_to_shindo(ri, gi, bi)
        if result is None:
            continue
        shindo, raw = result
        raw_r = round(raw, 2)
        entry = {'code': st['code'], 'lat': st['lat'], 'lon': st['lon'], 'int': shindo, 'raw': raw_r}
        if pix_pga is not None:
            pga = _color_to_pga(pix_pga[i][0], pix_pga[i][1], pix_pga[i][2])
            if pga is not None:
                entry['pga'] = pga
        if pix_bore is not None:
            b_result = _color_to_shindo(pix_bore[i][0], pix_bore[i][1], pix_bore[i][2])
            if b_result is not None:
                b_shindo, b_raw = b_result
                entry['borehole_int'] = b_shindo
                entry['borehole_raw'] = round(b_raw, 2)
        stations_out.append(entry)
        current_codes.add(st['code'])
        curr_key = (shindo, raw_r, entry.get('pga'), entry.get('borehole_int'))
        if _prev_nied.get(st['code']) != curr_key:
            changed.append(entry)
            _prev_nied[st['code']] = curr_key
    removed = [code for code in list(_prev_nied) if code not in current_codes]
    for code in removed:
        del _prev_nied[code]
    _last_nied_full = stations_out
    if is_first:
        # the connect snapshot is the first keyframe
        _last_station_keyframe['nied_stations'] = time.time()
        send_data_to_all_sockets({'type': 'nied_stations', 'stations': stations_out})
    else:
        _maybe_record_station_keyframe('nied_stations', stations_out)
        if changed or removed:
            send_data_to_all_sockets({'type': 'nied_stations_diff', 'stations': changed, 'removed': removed})

def poll_nied_jma():
    global _nied_jma_state, _nied_latest_ts
    while True:
        # NIED frames lag their timestamps by ~2–4s. Log only if every attempt fails.
        got = False
        tried_ts = None
        for delta in range(6):
            date, ts = _get_jst_parts(delta)
            ts_int = int(ts)
            if ts_int <= _nied_latest_ts:
                got = True  # already have this second or newer; nothing to do
                break
            tried_ts = ts
            img = _fetch_gif(KMONI_REALTIME.format(date=date, ts=ts))
            if img is None:
                time.sleep(0.1)
                continue
            _nied_jma_state = (ts, date, img)
            _nied_latest_ts = ts_int
            got = True
            break
        if not got:
            print(f'[NIED] jma_s fetch failed for all recent timestamps (oldest tried ts={tried_ts})')
        time.sleep(0.5)

def poll_nied_pga():
    last_pga_ts = None
    while True:
        state = _nied_jma_state
        if state is None or state[0] == last_pga_ts:
            time.sleep(0.1)
            continue
        ts, date, img = state
        # Wait for NIED to publish pga after jma_s, then fetch
        time.sleep(0.5)
        # If jma_s has already moved to a newer timestamp, skip this one
        if _nied_jma_state and _nied_jma_state[0] != ts:
            last_pga_ts = ts
            continue
        pga_future = _nied_fetch_executor.submit(_fetch_gif, KMONI_PGA.format(date=date, ts=ts))
        borehole_future = _nied_fetch_executor.submit(_fetch_gif, KMONI_BOREHOLE.format(date=date, ts=ts))
        img_pga = pga_future.result()
        if img_pga is None:
            print(f'[NIED] pga fetch failed for ts={ts}')
        img_borehole = borehole_future.result()
        if img_borehole is None:
            print(f'[NIED] jma_b fetch failed for ts={ts}')
        _nied_decode_and_send(img, img_pga, img_borehole)
        last_pga_ts = ts

threading.Thread(target=poll_nied_jma, daemon=True).start()
threading.Thread(target=poll_nied_pga, daemon=True).start()

# --- S-net (MSIL smoni) polling ---
SNET_TIMES_URL = "https://www.msil.go.jp/data/tiles/smoni/targetTimes.json"
SNET_TILE_URL  = "https://www.msil.go.jp/data/tiles/smoni/tileimage/{bt}/{bt}/5/28/{y}.png"

_snet_stations_path = os.path.join(BASE_DIR, 'snet_stations.json')
with open(_snet_stations_path, encoding='utf-8') as _f:
    SNET_STATIONS = json.load(_f)

_prev_snet: dict = {}
_last_snet_full: list = []

def _snet_decode_and_send(img12, img11):
    global _prev_snet, _last_snet_full
    is_first = not _prev_snet
    tile_imgs = {11: img11, 12: img12}
    stations_out = []
    changed = []
    current_codes = set()
    for st in SNET_STATIONS:
        img = tile_imgs.get(st['tile_y'])
        if img is None:
            continue
        r, g, b = img.getpixel((st['px'], st['py']))
        result = _color_to_shindo(r, g, b)
        if result is None:
            continue
        shindo, raw = result
        raw_r = round(raw, 2)
        entry = {'code': st['code'], 'lat': st['lat'], 'lon': st['lon'], 'int': shindo, 'raw': raw_r}
        stations_out.append(entry)
        current_codes.add(st['code'])
        curr_key = (shindo, raw_r)
        if _prev_snet.get(st['code']) != curr_key:
            changed.append(entry)
            _prev_snet[st['code']] = curr_key
    removed = [code for code in list(_prev_snet) if code not in current_codes]
    for code in removed:
        del _prev_snet[code]
    _last_snet_full = stations_out
    if is_first:
        # the connect snapshot is the first keyframe
        _last_station_keyframe['snet_stations'] = time.time()
        send_data_to_all_sockets({'type': 'snet_stations', 'stations': stations_out})
    else:
        _maybe_record_station_keyframe('snet_stations', stations_out)
        if changed or removed:
            send_data_to_all_sockets({'type': 'snet_stations_diff', 'stations': changed, 'removed': removed})

def poll_snet():
    last_bt = None
    while True:
        try:
            r = _http_session().get(f"{SNET_TIMES_URL}?{int(time.time()*1000)}", timeout=5)
            if r.status_code == 200:
                now = int(time.time())
                valid = []
                for entry in r.json():
                    bt = entry.get('basetime', '')
                    try:
                        dt = datetime.strptime(bt, '%Y%m%d%H%M%S').replace(tzinfo=JST)
                        if int(dt.timestamp()) <= now:
                            valid.append((int(dt.timestamp()), bt))
                    except Exception:
                        pass
                if valid:
                    _, latest_bt = max(valid)
                    if latest_bt != last_bt:
                        last_bt = latest_bt
                        img12 = _fetch_gif(SNET_TILE_URL.format(bt=latest_bt, y=12))
                        img11 = _fetch_gif(SNET_TILE_URL.format(bt=latest_bt, y=11))
                        if img12 is not None or img11 is not None:
                            _snet_decode_and_send(img12, img11)
        except Exception as e:
            print(f'[SNET] poll failed: {e}')
        time.sleep(10)

threading.Thread(target=poll_snet, daemon=True).start()

# --- Taiwan: ExpTech TREM-Net live stations + live EEW, CWA earthquake reports ---
# ExpTech provides community TREM-Net stations and EEW via load-balanced hosts.
# Payload fields follow the published @exptechtw/api-wrapper types.
EXPTECH_HOSTS = ['lb-1.exptech.dev', 'lb-2.exptech.dev', 'lb-3.exptech.dev', 'lb-4.exptech.dev']
_exptech_host_idx = 0

def _exptech_get(path, timeout=5):
    """Round-robins ExpTech's load-balancer hosts, advancing to the next one
    whenever a request fails so one bad host doesn't stall every poll cycle."""
    global _exptech_host_idx
    for _ in range(len(EXPTECH_HOSTS)):
        host = EXPTECH_HOSTS[_exptech_host_idx % len(EXPTECH_HOSTS)]
        try:
            r = _http_session().get(f"https://{host}{path}", timeout=timeout)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
        _exptech_host_idx += 1
    return None

_tw_stations_path = os.path.join(BASE_DIR, 'tw_stations.json')
with open(_tw_stations_path, encoding='utf-8') as _f:
    TW_STATIONS = json.load(_f)
_tw_station_lookup = {s['code']: s for s in TW_STATIONS}

# ExpTech i/I values use a 0–9 intensity index; round to the matching JMA class.
_TW_INT_LABELS = ['0', '1', '2', '3', '4', '5-', '5+', '6-', '6+', '7']

def _tw_intensity_label(i):
    if i is None:
        return None
    idx = round(i)
    if idx < 0:
        idx = 0
    elif idx > 9:
        idx = 9
    return _TW_INT_LABELS[idx]

_prev_exptech: dict = {}
_last_exptech_full: list = []

def poll_exptech_rts():
    global _prev_exptech, _last_exptech_full
    while True:
        try:
            data = _exptech_get('/api/v1/trem/rts')
            if data and isinstance(data.get('station'), dict):
                is_first = not _prev_exptech
                stations_out = []
                changed = []
                current_codes = set()
                for code, st in data['station'].items():
                    info = _tw_station_lookup.get(code)
                    if not info:
                        continue  # No known station coordinates.
                    label = _tw_intensity_label(st.get('i'))
                    if label is None:
                        continue
                    entry = {'code': code, 'lat': info['lat'], 'lon': info['lon'], 'int': label, 'raw': round(st.get('i', 0), 2)}
                    pga = st.get('pga')
                    if pga is not None:
                        entry['pga'] = round(pga, 2)
                    stations_out.append(entry)
                    current_codes.add(code)
                    curr_key = (label, entry['raw'], entry.get('pga'))
                    if _prev_exptech.get(code) != curr_key:
                        changed.append(entry)
                        _prev_exptech[code] = curr_key
                removed = [code for code in list(_prev_exptech) if code not in current_codes]
                for code in removed:
                    del _prev_exptech[code]
                _last_exptech_full = stations_out
                if is_first:
                    _last_station_keyframe['exptech_stations'] = time.time()
                    send_data_to_all_sockets({'type': 'exptech_stations', 'stations': stations_out})
                else:
                    _maybe_record_station_keyframe('exptech_stations', stations_out)
                    if changed or removed:
                        send_data_to_all_sockets({'type': 'exptech_stations_diff', 'stations': changed, 'removed': removed})
        except Exception as e:
            print(f'[ExpTech] rts poll failed: {e}')
        time.sleep(1)

threading.Thread(target=poll_exptech_rts, daemon=True).start()

# Track Taiwan EEW separately: its nested ExpTech payload differs from JMA.
recent_tw_eew_data: dict = {}  # {id: payload}
_tw_eew_clear_timers: dict = {}
# {id: (serial, status, final)} - dedupes unchanged re-polls
_prev_tw_eew_state: dict = {}

def clear_recent_tw_eew_data(key):
    global recent_tw_eew_data, _prev_tw_eew_state
    recent_tw_eew_data.pop(key, None)
    _prev_tw_eew_state.pop(key, None)
    with _timers_lock:
        _tw_eew_clear_timers.pop(key, None)
    send_data_to_all_sockets({'type': 'tw_eew_clear', 'key': key})
    dmdws.logger.info('Taiwan EEW cleared (key=%s)', key)

def poll_exptech_eew():
    global recent_tw_eew_data, _prev_tw_eew_state
    while True:
        try:
            data = _exptech_get('/api/v1/eq/eew')
            if isinstance(data, list):
                for eew in data:
                    if eew.get('type') != 'eew':
                        continue
                    key = eew.get('id')
                    status = eew.get('status')
                    # EewStatus.Test - never surface to users
                    if not key or status == 3:
                        continue
                    if status == 2:  # EewStatus.Cancel
                        if key in recent_tw_eew_data:
                            clear_recent_tw_eew_data(key)
                        continue
                    state = (eew.get('serial'), status, eew.get('final'))
                    if _prev_tw_eew_state.get(key) == state:
                        # unchanged since the last poll - nothing new to broadcast
                        continue
                    _prev_tw_eew_state[key] = state
                    # Rename the upstream eew type for our dispatcher.
                    payload = {**eew, 'type': 'tw_eew'}
                    recent_tw_eew_data[key] = payload
                    with _timers_lock:
                        old = _tw_eew_clear_timers.get(key)
                        if old is not None:
                            old.cancel()
                        timer = threading.Timer(120, clear_recent_tw_eew_data, args=(key,))
                        _tw_eew_clear_timers[key] = timer
                    timer.start()
                    send_data_to_all_sockets(payload)
                    dmdws.logger.info('Taiwan EEW received (id=%s serial=%s status=%s)', key, eew.get('serial'), status)
        except Exception as e:
            print(f'[ExpTech] eew poll failed: {e}')
        time.sleep(1)

threading.Thread(target=poll_exptech_eew, daemon=True).start()

# Official CWA reports: E-A0015-001 (有感地震報告).
# OriginTime is ISO 8601 with an explicit offset. Intensity strings use N級
# for 0–4/7 and bare 5弱/5強/6弱/6強; see _TW_CWA_INT_MAP.
recent_tw_quake_data = None
_tw_quake_clear_timer = None
_cwa_seen_earthquake_nos: set = set()

_TW_CWA_INT_MAP = {
    '0級': '0', '1級': '1', '2級': '2', '3級': '3', '4級': '4',
    '5弱': '5-', '5強': '5+', '6弱': '6-', '6強': '6+', '7級': '7',
}

# Translate CWA's free-text Location template: county, direction and distance,
# then an optional area. Preserve Chinese text when a pattern is unrecognised.
_TW_COUNTY_EN = {
    '臺北市': 'Taipei City', '新北市': 'New Taipei City', '桃園市': 'Taoyuan City',
    '臺中市': 'Taichung City', '臺南市': 'Tainan City', '高雄市': 'Kaohsiung City',
    '基隆市': 'Keelung City', '新竹市': 'Hsinchu City', '新竹縣': 'Hsinchu County',
    '嘉義市': 'Chiayi City', '嘉義縣': 'Chiayi County', '苗栗縣': 'Miaoli County',
    '彰化縣': 'Changhua County', '南投縣': 'Nantou County', '雲林縣': 'Yunlin County',
    '屏東縣': 'Pingtung County', '宜蘭縣': 'Yilan County', '花蓮縣': 'Hualien County',
    '臺東縣': 'Taitung County', '澎湖縣': 'Penghu County', '金門縣': 'Kinmen County',
    '連江縣': 'Lienchiang County',
}
_TW_DIRECTION_EN = {
    '北北東': 'NNE', '東北東': 'ENE', '東南東': 'ESE', '南南東': 'SSE',
    '南南西': 'SSW', '西南西': 'WSW', '西北西': 'WNW', '北北西': 'NNW',
    '東北': 'NE', '東南': 'SE', '西南': 'SW', '西北': 'NW',
    '東': 'E', '南': 'S', '西': 'W', '北': 'N',
}
_TW_LOCATION_RE = re.compile(r'^(?P<county>\S+?)政府(?P<dir>[東南西北]+)方\s*(?P<dist>[\d.]+)\s*公里\s*(?:\(位於(?P<area>.+?)\))?$')
_TW_AREA_SEA_RE = re.compile(r'^臺灣([東南西北]+)部海域$')
_TW_AREA_OFFSHORE_RE = re.compile(r'^(\S+?)(近海|外海)$')

def _translate_tw_location(raw):
    if not raw:
        return raw
    m = _TW_LOCATION_RE.match(raw.strip())
    if not m:
        return raw
    county_en = _TW_COUNTY_EN.get(m.group('county'))
    dir_en = _TW_DIRECTION_EN.get(m.group('dir'))
    if not county_en or not dir_en:
        return raw
    en = f"{m.group('dist')} km {dir_en} of {county_en}"
    area = m.group('area')
    if area:
        sea_m = _TW_AREA_SEA_RE.match(area)
        offshore_m = _TW_AREA_OFFSHORE_RE.match(area)
        if sea_m:
            area_dir_en = _TW_DIRECTION_EN.get(sea_m.group(1))
            en += f" ({(area_dir_en + ' ') if area_dir_en else ''}waters off Taiwan)"
        elif offshore_m and offshore_m.group(1) in _TW_COUNTY_EN:
            kind = 'offshore' if offshore_m.group(2) == '近海' else 'far offshore from'
            en += f" ({kind} {_TW_COUNTY_EN[offshore_m.group(1)]})"
        else:
            en += f" ({area})"  # untranslated fallback - better than dropping it
    return en

def clear_recent_tw_quake_data():
    global recent_tw_quake_data, _tw_quake_clear_timer
    recent_tw_quake_data = None
    with _timers_lock:
        _tw_quake_clear_timer = None
    send_data_to_all_sockets({'type': 'tw_past_quake_clear'})
    dmdws.logger.info('Recent Taiwan quake data cleared')

def _load_cwa_seen_earthquake_nos():
    global _cwa_seen_earthquake_nos
    try:
        with db.Database() as cursor:
            cursor.execute('SELECT earthquake_no FROM tw_quake_epicenters')
            _cwa_seen_earthquake_nos = {row[0] for row in cursor.fetchall()}
    except Exception as e:
        dmdws.logger.warning('Failed to load seen CWA earthquake numbers: %s', e)

def _store_tw_quake(earthquake_no, lat, lon, depth, magnitude, location, stations, ts, web, location_zh):
    with db.Database() as cursor:
        cursor.execute(
            'INSERT OR IGNORE INTO tw_quake_epicenters (earthquake_no, lat, lon, depth, magnitude, location, timestamp, web, location_zh) VALUES (?,?,?,?,?,?,?,?,?)',
            (earthquake_no, lat, lon, depth, magnitude, location, ts, web, location_zh)
        )
        if stations:
            cursor.executemany(
                'INSERT OR IGNORE INTO tw_quake_stations (earthquake_no, station_id, name, lat, lon, intensity) VALUES (?,?,?,?,?,?)',
                [(earthquake_no, s['code'], s.get('name', ''), s['lat'], s['lon'], s['int']) for s in stations]
            )

# Keep the newest CWA reports for the sidebar and broadcast the full list
# when a report arrives, matching recent_jma_history.
recent_tw_history: list = []
_TW_HISTORY_CAP = 40

def _load_tw_history():
    """Seed Taiwan sidebar history from SQLite before the next CWA poll.

    _SHINDO_RANK is resolved when the polling thread calls this after startup."""
    global recent_tw_history
    try:
        with db.Database() as cursor:
            cursor.execute(
                'SELECT earthquake_no, lat, lon, depth, magnitude, location, timestamp, web, location_zh '
                'FROM tw_quake_epicenters ORDER BY timestamp DESC LIMIT ?',
                (_TW_HISTORY_CAP,)
            )
            rows = cursor.fetchall()
            history = []
            for eq_no, lat, lon, depth, magnitude, location, ts, web, location_zh in rows:
                cursor.execute('SELECT intensity FROM tw_quake_stations WHERE earthquake_no = ?', (eq_no,))
                max_int = None
                for (val,) in cursor.fetchall():
                    if val in _SHINDO_RANK and (max_int is None or _SHINDO_RANK[val] > _SHINDO_RANK[max_int]):
                        max_int = val
                history.append({
                    'earthquake_no': eq_no, 'lat': lat, 'lon': lon, 'depth': depth,
                    'magnitude': magnitude, 'location': location, 'location_zh': location_zh, 'ts': ts,
                    'max_int': max_int, 'web': web,
                })
        recent_tw_history = history
    except Exception as e:
        dmdws.logger.warning('Failed to load Taiwan quake history: %s', e)

def poll_cwa_reports():
    global recent_tw_quake_data, _tw_quake_clear_timer
    if not CWA_API_KEY:
        dmdws.logger.warning('poll_cwa_reports: WEBQUAKE_CWA_API_KEY not set - Taiwan earthquake reports disabled')
        return
    _load_cwa_seen_earthquake_nos()
    _load_tw_history()
    url = 'https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001'
    while True:
        try:
            r = _http_session().get(url, params={'Authorization': CWA_API_KEY, 'limit': 5}, timeout=10)
            if r.status_code == 200:
                records = r.json().get('records', {}).get('Earthquake', [])
                for rec in records:
                    eq_no = rec.get('EarthquakeNo')
                    if eq_no is None or eq_no in _cwa_seen_earthquake_nos:
                        continue  # Already processed this report.
                    _cwa_seen_earthquake_nos.add(eq_no)
                    info = rec.get('EarthquakeInfo', {}) or {}
                    epi = info.get('Epicenter', {}) or {}
                    origin_str = info.get('OriginTime', '')
                    try:
                        ts = int(datetime.fromisoformat(origin_str).timestamp())
                    except Exception:
                        ts = int(time.time())
                    try:
                        lat = float(epi.get('EpicenterLatitude'))
                        lon = float(epi.get('EpicenterLongitude'))
                    except (TypeError, ValueError):
                        lat = lon = None
                    try:
                        depth = float(info.get('FocalDepth'))
                    except (TypeError, ValueError):
                        depth = None
                    try:
                        magnitude = float((info.get('EarthquakeMagnitude') or {}).get('MagnitudeValue'))
                    except (TypeError, ValueError):
                        magnitude = None
                    location_zh = epi.get('Location')
                    location = _translate_tw_location(location_zh)
                    stations = []
                    for area in (rec.get('Intensity', {}) or {}).get('ShakingArea', []):
                        for st in area.get('EqStation', []):
                            label = _TW_CWA_INT_MAP.get(st.get('SeismicIntensity', ''))
                            if not label:
                                continue
                            try:
                                stations.append({
                                    'code': st.get('StationID', ''),
                                    'name': st.get('StationName', ''),
                                    'lat': float(st.get('StationLatitude')),
                                    'lon': float(st.get('StationLongitude')),
                                    'int': label,
                                })
                            except (TypeError, ValueError):
                                continue
                    web = rec.get('Web')
                    _store_tw_quake(eq_no, lat, lon, depth, magnitude, location, stations, ts, web, location_zh)
                    recent_tw_quake_data = {
                        'type': 'tw_earthquake',
                        'earthquake_no': eq_no,
                        'origin_time': origin_str,
                        'lat': lat, 'lon': lon, 'depth': depth, 'magnitude': magnitude,
                        'location': location, 'location_zh': location_zh, 'stations': stations,
                        'web': web,
                    }
                    with _timers_lock:
                        if _tw_quake_clear_timer is not None:
                            _tw_quake_clear_timer.cancel()
                        t = threading.Timer(180, clear_recent_tw_quake_data)
                        _tw_quake_clear_timer = t
                    t.start()
                    max_int = None
                    for s in stations:
                        if s['int'] in _SHINDO_RANK and (max_int is None or _SHINDO_RANK[s['int']] > _SHINDO_RANK[max_int]):
                            max_int = s['int']
                    recent_tw_history.insert(0, {
                        'earthquake_no': eq_no, 'lat': lat, 'lon': lon, 'depth': depth,
                        'magnitude': magnitude, 'location': location, 'location_zh': location_zh, 'ts': ts,
                        'max_int': max_int, 'web': web,
                    })
                    del recent_tw_history[_TW_HISTORY_CAP:]
                    send_data_to_all_sockets({'type': 'tw_history', 'quakes': recent_tw_history})
                    send_data_to_all_sockets(recent_tw_quake_data)
                    dmdws.logger.info('CWA earthquake report received (no=%s)', eq_no)
            else:
                dmdws.logger.warning('CWA poll got HTTP %s', r.status_code)
        except Exception as e:
            print(f'[CWA] poll failed: {e}')
        time.sleep(20)

threading.Thread(target=poll_cwa_reports, daemon=True).start()

# {key: output_data} — key is str(origin_time or quake_time)
recent_earthquake_data = {}
recent_tsunami_data = None
recent_offshore_obs_data = None
recent_past_quake_data = None
recent_jma_history = None
_past_quake_clear_timer = None
_tsunami_clear_timer = None
_offshore_obs_clear_timer = None
_eew_clear_timers = {}  # {key: threading.Timer}
_timers_lock = threading.Lock()
_jma_history_top = None

def _haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))

# Only match Around Taiwan (900) and East China Sea (901) reports to CWA.
# Nearby Okinawa-region codes remain separate JMA events.
_TW_MATCH_HYPOCENTER_CODES = {'900', '901'}
_TW_MATCH_TIME_WINDOW_S = 180
_TW_MATCH_DIST_KM = 100

def _find_matching_tw_report(output_data):
    """Match a Taiwan-area JMA report to cached CWA data within 180s and 100km.

    Only CWA reports already in recent_tw_history are considered. A later CWA
    arrival does not retroactively merge an unmatched pair."""
    lat, lon = output_data.get('lat'), output_data.get('lon')
    ts = output_data.get('quake_time')
    if lat is None or lon is None or ts is None:
        return None
    best, best_dt = None, None
    for q in recent_tw_history:
        if q.get('lat') is None or q.get('lon') is None or q.get('ts') is None:
            continue
        dt = abs(q['ts'] - ts)
        if dt > _TW_MATCH_TIME_WINDOW_S:
            continue
        if _haversine_km(lat, lon, q['lat'], q['lon']) > _TW_MATCH_DIST_KM:
            continue
        if best is None or dt < best_dt:
            best, best_dt = q, dt
    if best is None:
        return None
    return {'earthquake_no': best['earthquake_no'], 'web': best.get('web'), 'location': best.get('location')}

def store_recent_data(output_data, data):
    global recent_earthquake_data, recent_tsunami_data, recent_offshore_obs_data
    global _eew_clear_timers, _past_quake_clear_timer, _tsunami_clear_timer, _offshore_obs_clear_timer
    if output_data['type'] == 'earthquake' and 'last_report' in output_data:
        key = output_data.get('event_id') or str(output_data.get('origin_time') or output_data.get('quake_time') or '')
        recent_earthquake_data[key] = output_data
        dmdws.logger.info('Recent quake data stored (key=%s)', key)
        with _timers_lock:
            old = _eew_clear_timers.get(key)
            if old is not None:
                old.cancel()
            timer = threading.Timer(120, clear_recent_earthquake_data, args=(key,))
            _eew_clear_timers[key] = timer
        timer.start()
    elif output_data['type'] == 'eew_clear':
        # Cancel the cached EEW and timer. on_message broadcasts the clear payload;
        # calling clear_recent_earthquake_data here would broadcast it twice.
        key = output_data.get('key')
        recent_earthquake_data.pop(key, None)
        with _timers_lock:
            timer = _eew_clear_timers.pop(key, None)
        if timer is not None:
            timer.cancel()
        dmdws.logger.info('EEW cancellation telegram received; recent quake data cleared (key=%s)', key)
    elif output_data['type'] == 'tsunami_clear':
        recent_tsunami_data = None
        recent_offshore_obs_data = None
        with _timers_lock:
            if _tsunami_clear_timer is not None:
                _tsunami_clear_timer.cancel()
                _tsunami_clear_timer = None
            if _offshore_obs_clear_timer is not None:
                _offshore_obs_clear_timer.cancel()
                _offshore_obs_clear_timer = None
        dmdws.logger.info('Tsunami all-clear telegram received; recent tsunami data cleared')
    elif output_data['type'] == 'tsunami':
        recent_tsunami_data = output_data
        dmdws.logger.info('Recent tsunami data stored')
        # Only schedule expiry when JMA provides validDateTime; issuance reports
        # can omit it while the warning remains in force.
        valid_ts = _safe_ts(data.get('body', {}).get('validDateTime'))
        with _timers_lock:
            if _tsunami_clear_timer is not None:
                _tsunami_clear_timer.cancel()
                _tsunami_clear_timer = None
            if valid_ts:
                t = threading.Timer(max(0, valid_ts - int(time.time())), clear_recent_tsunami_data)
                _tsunami_clear_timer = t
                t.start()
    elif output_data['type'] == 'tsunami_obs':
        recent_offshore_obs_data = output_data
        dmdws.logger.info('Recent offshore obs data stored')
        valid_ts = _safe_ts(data.get('body', {}).get('validDateTime'))
        with _timers_lock:
            if _offshore_obs_clear_timer is not None:
                _offshore_obs_clear_timer.cancel()
                _offshore_obs_clear_timer = None
            if valid_ts:
                t = threading.Timer(max(0, valid_ts - int(time.time())), clear_recent_offshore_obs_data)
                _offshore_obs_clear_timer = t
                t.start()
    elif output_data['type'] == 'earthquake' and 'last_report' not in output_data:
        global recent_past_quake_data
        hypo_code = data.get('body', {}).get('body', {}).get('earthquake', {}).get('hypocenter', {}).get('code', '')
        if hypo_code in _TW_MATCH_HYPOCENTER_CODES:
            match = _find_matching_tw_report(output_data)
            if match:
                # Include the match in the next broadcast.
                output_data['matched_tw'] = match
                dmdws.logger.info('JMA report matched to CWA earthquake_no=%s (hypocenter code %s)', match['earthquake_no'], hypo_code)
        # Preserve intensity fields when a later epicenter-only VXSE52 omits them.
        if recent_past_quake_data and recent_past_quake_data.get('event_id') == output_data.get('event_id'):
            merged = {**recent_past_quake_data, **output_data}
            for field in ('max_int', 'max_lpgm', 'prefectures_en', 'prefectures_jp',
                          'regions_en', 'regions_jp', 'area_intensities', 'matched_tw'):
                if field not in output_data and field in recent_past_quake_data:
                    merged[field] = recent_past_quake_data[field]
            recent_past_quake_data = merged
        else:
            recent_past_quake_data = output_data
        dmdws.logger.info('Recent past quake data stored')
        with _timers_lock:
            if _past_quake_clear_timer is not None:
                _past_quake_clear_timer.cancel()
            t = threading.Timer(180, clear_recent_past_quake_data)
            _past_quake_clear_timer = t
        t.start()
    else:
        dmdws.logger.info('Recent data not stored as it is not eligible')

def clear_recent_earthquake_data(key):
    global recent_earthquake_data, _eew_clear_timers
    recent_earthquake_data.pop(key, None)
    with _timers_lock:
        _eew_clear_timers.pop(key, None)
    send_data_to_all_sockets({'type': 'eew_clear', 'key': key})
    dmdws.logger.info('Recent quake data cleared (key=%s)', key)

def clear_recent_tsunami_data():
    global recent_tsunami_data, recent_offshore_obs_data, _tsunami_clear_timer
    recent_tsunami_data = None
    recent_offshore_obs_data = None
    with _timers_lock:
        _tsunami_clear_timer = None
    send_data_to_all_sockets({'type': 'tsunami_clear'})
    dmdws.logger.info('Recent tsunami data cleared')

def clear_recent_offshore_obs_data():
    global recent_offshore_obs_data, _offshore_obs_clear_timer
    recent_offshore_obs_data = None
    with _timers_lock:
        _offshore_obs_clear_timer = None
    send_data_to_all_sockets({'type': 'tsunami_obs_clear'})
    dmdws.logger.info('Recent offshore obs data cleared')

def clear_recent_past_quake_data():
    global recent_past_quake_data, _past_quake_clear_timer
    recent_past_quake_data = None
    with _timers_lock:
        _past_quake_clear_timer = None
    send_data_to_all_sockets({'type': 'past_quake_clear'})
    dmdws.logger.info('Recent past quake data cleared')

_qpi_cache = None  # cached result of _compute_quake_points_index()
_qpi_lock = threading.Lock()

def _invalidate_quake_points_index():
    """Drop the cached index so it's rebuilt on next request. Called whenever a
    new epicenter or station row is actually inserted."""
    global _qpi_cache
    _qpi_cache = None

def _build_quake_points_index():
    """Return the quake-point index, cached until new quake data is inserted."""
    global _qpi_cache
    with _qpi_lock:
        if _qpi_cache is None:
            _qpi_cache = _compute_quake_points_index()
        return _qpi_cache

def _compute_quake_points_index():
    with db.Database() as cursor:
        cursor.execute(
            'SELECT qe.event_id, qe.lat, qe.lon, qe.timestamp, '
            'CASE WHEN qs.event_id IS NOT NULL THEN 1 ELSE 0 END as has_stations '
            'FROM quake_epicenters qe '
            'LEFT JOIN (SELECT DISTINCT event_id FROM quake_stations) qs ON qe.event_id = qs.event_id'
        )
        rows = cursor.fetchall()
    events = []
    for eid, lat, lon, ts, has_st in rows:
        if ts is None:
            try:
                ts = int(datetime.strptime(eid, '%Y%m%d%H%M%S').replace(tzinfo=JST).timestamp())
            except Exception:
                continue
        events.append({'ts': ts, 'lat': lat, 'lon': lon, 'st': bool(has_st)})
    return events

_SHINDO_ORDER = ['0', '1', '2', '3', '4', '5-', '5+', '6-', '6+', '7']
_SHINDO_RANK = {v: i for i, v in enumerate(_SHINDO_ORDER)}

def _max_station_intensity(event_id):
    """Fall back to the highest per-station intensity we recorded for this event
    when JMA's own list.json hasn't published a maxi yet (e.g. right after a
    large quake, before JMA finishes compiling the summary report)."""
    if not event_id:
        return None
    with db.Database() as cursor:
        cursor.execute('SELECT intensity FROM quake_stations WHERE event_id = ?', (event_id,))
        rows = cursor.fetchall()
    best = None
    for (val,) in rows:
        if val in _SHINDO_RANK and (best is None or _SHINDO_RANK[val] > _SHINDO_RANK[best]):
            best = val
    return best

def poll_jma_quake_history():
    global recent_jma_history, _jma_history_top
    while True:
        try:
            resp = _http_session().get('https://www.jma.go.jp/bosai/quake/data/list.json', timeout=10)
            if resp.ok:
                data = resp.json()
                resp.close()
                valid = [q for q in data if q.get('anm') and q.get('mag')]
                groups = {}
                for q in valid:
                    key = q.get('ctt') or q.get('at')
                    if not key:
                        continue
                    if key not in groups:
                        groups[key] = dict(q)
                    else:
                        g = groups[key]
                        if (not g.get('maxi') or g['maxi'] == '0') and q.get('maxi') and q['maxi'] != '0':
                            g['maxi'] = q['maxi']
                by_at = {}
                for q in groups.values():
                    at = q.get('at')
                    if not at:
                        by_at[q.get('ctt')] = q
                        continue
                    if at not in by_at:
                        by_at[at] = q
                    else:
                        g = by_at[at]
                        if (not g.get('maxi') or g['maxi'] == '0') and q.get('maxi') and q['maxi'] != '0':
                            g['maxi'] = q['maxi']
                        if not g.get('ctt') and q.get('ctt'):
                            g['ctt'] = q['ctt']
                deduped = list(by_at.values())[:40]
                for q in deduped:
                    if not q.get('maxi') or q['maxi'] == '0':
                        fallback = _max_station_intensity(q.get('ctt'))
                        if fallback:
                            q['maxi'] = fallback
                    if q.get('ctt') in volcanic_origin_event_ids:
                        q['is_volcanic'] = True
                top_at = deduped[0].get('at') if deduped else None
                # Broadcast late maxi updates even when no new quake appears.
                changed = top_at != _jma_history_top or (
                    recent_jma_history is not None and
                    {q.get('at'): q.get('maxi') for q in deduped} !=
                    {q.get('at'): q.get('maxi') for q in recent_jma_history}
                )
                if changed:
                    _jma_history_top = top_at
                    recent_jma_history = deduped
                    send_data_to_all_sockets({'type': 'jma_history', 'quakes': deduped})
        except Exception as e:
            dmdws.logger.info(f'[JMA] quake history fetch failed: {e}')
        time.sleep(30)

threading.Thread(target=poll_jma_quake_history, daemon=True).start()

def send_recent_data(ws: WebsocketBase):
    if _last_nied_full:
        send_data(ws, json.dumps({'type': 'nied_stations', 'stations': _last_nied_full}, separators=(',', ':')))
    if _last_snet_full:
        send_data(ws, json.dumps({'type': 'snet_stations', 'stations': _last_snet_full}, separators=(',', ':')))
    if _last_exptech_full:
        send_data(ws, json.dumps({'type': 'exptech_stations', 'stations': _last_exptech_full}, separators=(',', ':')))
    for eew_data in list(recent_earthquake_data.values()):
        send_data(ws, json.dumps(eew_data, separators=(',', ':')))
    for tw_eew_data in list(recent_tw_eew_data.values()):
        send_data(ws, json.dumps(tw_eew_data, separators=(',', ':')))
    if recent_tsunami_data:
        send_data(ws, json.dumps(recent_tsunami_data, separators=(',', ':')))
    if recent_offshore_obs_data:
        send_data(ws, json.dumps(recent_offshore_obs_data, separators=(',', ':')))
    if recent_past_quake_data:
        send_data(ws, json.dumps(recent_past_quake_data, separators=(',', ':')))
    if recent_tw_quake_data:
        send_data(ws, json.dumps(recent_tw_quake_data, separators=(',', ':')))
    if recent_jma_history is not None:
        send_data(ws, json.dumps({'type': 'jma_history', 'quakes': recent_jma_history}, separators=(',', ':')))
    if recent_tw_history:
        send_data(ws, json.dumps({'type': 'tw_history', 'quakes': recent_tw_history}, separators=(',', ':')))
    send_data(ws, json.dumps({'type': 'quake_points_index', 'events': _build_quake_points_index()}, separators=(',', ':')))

_last_warning_time = None
_last_forecast_time = None

def _first_or(value, default='Unknown'):
    """Unwrap the single-element location lists produced by process_data."""
    if isinstance(value, list):
        return value[0] if value else default
    return value if value is not None else default

def _fmt_magnitude(magnitude):
    try:
        m = float(magnitude)
    except (TypeError, ValueError):
        return None
    if m != m or m <= 0:  # NaN or unknown
        return None
    return f"M{m:.1f}"

def _fmt_depth(depth):
    try:
        d = float(depth)
    except (TypeError, ValueError):
        return None
    if d != d or d <= 0:  # NaN or unknown
        return None
    return f"{int(round(d))}km"

def _format_quake_alert(output_data, location_en, location_ja, max_intensity):
    """Builds richer EN/JA notification bodies: location, magnitude, depth, max
    intensity."""
    mag = _fmt_magnitude(output_data.get('magnitude'))
    depth = _fmt_depth(output_data.get('depth'))

    details_en = [d for d in (mag, f"Depth {depth}" if depth else None) if d]
    details_ja = [d for d in (mag, f"深さ{depth}" if depth else None) if d]

    body_en = f"Epicenter: {location_en}"
    if details_en:
        body_en += f" ({', '.join(details_en)})"
    body_en += f" — Max Int. {max_intensity}"

    body_ja = f"震源地: {location_ja}"
    if details_ja:
        body_ja += f"（{'　'.join(details_ja)}）"
    body_ja += f" 最大震度 {max_intensity}"

    return body_en, body_ja

def send_alert(output_data):
    global _last_warning_time, _last_forecast_time
    warning = output_data.get('warning', False)

    if warning == True:
        location_en = _first_or(output_data.get('epi_location_en'))
        location_ja = _first_or(output_data.get('epi_location_jp'))
        max_intensity = output_data.get('max_int', 'Unknown')
        body_en, body_ja = _format_quake_alert(output_data, location_en, location_ja, max_intensity)
        current_time = time.time()
        if _last_warning_time is None or current_time - _last_warning_time > 10:
            send_notification(
                "Earthquake Early Warning", "緊急地震速報",
                body_en,
                body_ja,
            )
            send_ntfy_notification(
                "Earthquake Early Warning",
                body_en,
                topic=NTFY_TOPIC_ALERTS_EN,
            )
            send_ntfy_notification(
                "緊急地震速報",
                body_ja,
                topic=NTFY_TOPIC_ALERTS_JA,
                click=SITE_LINK_JA,
            )
            image_bytes = render_quake_map(output_data)
            send_discord_notification("Earthquake Early Warning", body_en, DISCORD_WEBHOOK_EN, SITE_LINK_EN, image_bytes=image_bytes)
            send_discord_notification("緊急地震速報", body_ja, DISCORD_WEBHOOK_JA, SITE_LINK_JA, image_bytes=image_bytes)
            _last_warning_time = current_time
            dmdws.logger.info('Earthquake warning notification sent')
        else:
            dmdws.logger.info('Earthquake warning notification not sent as it has been less than 10 seconds since the last one')

    elif output_data['type'] == 'earthquake' and warning == False:
        # EEW (VXSE45); post-event (VXSE51/52/53) has no last_report
        is_forecast = 'last_report' in output_data
        location_en = _first_or(output_data.get('epi_location_en'))
        location_ja = _first_or(output_data.get('epi_location_jp'))
        max_intensity = output_data.get('max_int', 'Unknown')
        body_en, body_ja = _format_quake_alert(output_data, location_en, location_ja, max_intensity)
        current_time = time.time()
        if _last_forecast_time is None or current_time - _last_forecast_time > 10:
            title_en, title_ja = ("Earthquake Forecast", "地震動予測") if is_forecast else ("Earthquake Information", "地震情報")
            send_notification(
                title_en, title_ja,
                body_en,
                body_ja,
                tag="forecasts",
            )
            send_ntfy_notification(
                title_en,
                body_en,
                topic=NTFY_TOPIC_FORECASTS_EN,
            )
            send_ntfy_notification(
                title_ja,
                body_ja,
                topic=NTFY_TOPIC_FORECASTS_JA,
                click=SITE_LINK_JA,
            )
            image_bytes = render_quake_map(output_data)
            send_discord_notification(title_en, body_en, DISCORD_WEBHOOK_EN, SITE_LINK_EN, image_bytes=image_bytes)
            send_discord_notification(title_ja, body_ja, DISCORD_WEBHOOK_JA, SITE_LINK_JA, image_bytes=image_bytes)
            _last_forecast_time = current_time
            dmdws.logger.info('Earthquake %s notification sent', 'forecast' if is_forecast else 'information')
        else:
            dmdws.logger.info('Earthquake %s notification not sent as it has been less than 10 seconds since the last one', 'forecast' if is_forecast else 'information')

    elif output_data['type'] == 'tsunami':  # Tsunami notifications are always sent
        send_notification(
            "Tsunami Information", "津波情報",
            "New Tsunami Information issued. Open WebQuake for more details.",
            "新たな津波情報が発表された。詳細はWebQuakeをご覧ください。",
        )
        send_ntfy_notification(
            "Tsunami Information",
            "New Tsunami Information issued. Open WebQuake for more details.",
            topic=NTFY_TOPIC_ALERTS_EN,
        )
        send_ntfy_notification(
            "津波情報",
            "新たな津波情報が発表された。詳細はWebQuakeをご覧ください。",
            topic=NTFY_TOPIC_ALERTS_JA,
            click=SITE_LINK_JA,
        )
        send_discord_notification(
            "Tsunami Information",
            "New Tsunami Information issued. Open WebQuake for more details.",
            DISCORD_WEBHOOK_EN,
            SITE_LINK_EN,
        )
        send_discord_notification(
            "津波情報",
            "新たな津波情報が発表された。詳細はWebQuakeをご覧ください。",
            DISCORD_WEBHOOK_JA,
            SITE_LINK_JA,
        )
        dmdws.logger.info('Tsunami information notification sent')

    else:
        dmdws.logger.info('No notification sent as data type does not call for it')

def send_data(ws: WebsocketBase, data: str):
    # This helper is only used to build a new connection's initial snapshot.
    open_sockets.send(ws, data, initial=True)
    

_public = os.path.join(BASE_DIR, '..', 'public')

@app.route('/api/quake_points_index')
def quake_points_index():
    return json.dumps({'events': _build_quake_points_index()}, separators=(',', ':'))

@app.route('/api/quake_points/<event_id>')
def quake_points(event_id):
    with db.Database() as cursor:
        cursor.execute(
            'SELECT code, name_jp, lat, lon, intensity FROM quake_stations WHERE event_id = ?',
            (event_id,)
        )
        rows = cursor.fetchall()
    stations = [{'code': r[0], 'name': r[1], 'lat': r[2], 'lon': r[3], 'int': r[4]} for r in rows]
    return json.dumps({'stations': stations}, separators=(',', ':'))

@app.route('/api/quake_points_near/<int:ts>')
def quake_points_near(ts):
    # Find the nearest event timestamp with stored station data.
    with db.Database() as cursor:
        cursor.execute(
            'SELECT qe.event_id, abs(qe.timestamp - ?) as diff '
            'FROM quake_epicenters qe '
            'WHERE qe.event_id IN (SELECT DISTINCT event_id FROM quake_stations) '
            'AND qe.timestamp IS NOT NULL '
            'ORDER BY diff LIMIT 1',
            (ts,)
        )
        row = cursor.fetchone()
    if row is None or row[1] > 90:  # q.at is minute-truncated, max drift = 59s
        return json.dumps({'stations': []}, separators=(',', ':'))
    best_id = row[0]
    with db.Database() as cursor:
        cursor.execute(
            'SELECT code, name_jp, lat, lon, intensity FROM quake_stations WHERE event_id = ?',
            (best_id,)
        )
        rows = cursor.fetchall()
    stations = [{'code': r[0], 'name': r[1], 'lat': r[2], 'lon': r[3], 'int': r[4]} for r in rows]
    return json.dumps({'stations': stations}, separators=(',', ':'))

@app.route('/api/tw_quake_points/<int:earthquake_no>')
def tw_quake_points(earthquake_no):
    with db.Database() as cursor:
        cursor.execute(
            'SELECT station_id, name, lat, lon, intensity FROM tw_quake_stations WHERE earthquake_no = ?',
            (earthquake_no,)
        )
        rows = cursor.fetchall()
    stations = [{'code': r[0], 'name': r[1], 'lat': r[2], 'lon': r[3], 'int': r[4]} for r in rows]
    return json.dumps({'stations': stations}, separators=(',', ':'))

# --- 48h history / replay API ---

def _parse_ts_param(name, default):
    val = request.args.get(name)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        return default

def _history_cache_ready():
    return HISTORY_CACHE_ENABLED and _history_cache.ready

def _merge_station_snapshot(msg_type, diff_type, at):
    """Reconstructs a full station list as of `at` from the last full snapshot
    plus every diff broadcast since, mirroring the merge the client already
    does live (applyStationsDiff)."""
    if _history_cache_ready():
        base_row = _history_cache.latest_before(msg_type, at)
        if base_row is None:
            return None
        base_ts, base_payload = base_row
        try:
            base = json.loads(base_payload)
        except Exception:
            return None
        diff_payloads = [p for (_ts, p) in _history_cache.range(diff_type, base_ts, at, from_exclusive=True)]
    else:
        with db.Database() as cursor:
            cursor.execute(
                'SELECT ts, payload FROM history_log WHERE msg_type = ? AND ts <= ? ORDER BY ts DESC LIMIT 1',
                (msg_type, at)
            )
            base_row = cursor.fetchone()
            if base_row is None:
                return None
            base_ts, base_payload = base_row
            try:
                base = json.loads(base_payload)
            except Exception:
                return None
            cursor.execute(
                'SELECT payload FROM history_log WHERE msg_type = ? AND ts > ? AND ts <= ? ORDER BY ts ASC',
                (diff_type, base_ts, at)
            )
            diff_payloads = [r[0] for r in cursor.fetchall()]
    stations = {s['code']: s for s in base.get('stations', [])}
    for payload in diff_payloads:
        try:
            diff = json.loads(payload)
        except Exception:
            continue
        for s in diff.get('stations', []):
            stations[s['code']] = s
        for code in diff.get('removed', []):
            stations.pop(code, None)
    return {'type': msg_type, 'stations': list(stations.values())}

def _keyframes_for_gap(msg_type, diff_type, base_ts, end_ts):
    """Synthesise keyframes within a single (base_ts, end_ts] window, starting
    from the full snapshot at base_ts and replaying the diffs in between. Returns
    [(ts, snapshot_json), ...]. Only the diffs inside the gap are parsed."""
    row = _history_cache.latest_before(msg_type, base_ts)
    if row is None:
        return []
    try:
        stations = {s['code']: s for s in json.loads(row[1]).get('stations', [])}
    except Exception:
        return []
    last_kf_ts = base_ts
    out = []
    for i, (ts, payload) in enumerate(_history_cache.range(diff_type, base_ts, end_ts, from_exclusive=True)):
        try:
            diff = json.loads(payload)
        except Exception:
            continue
        for s in diff.get('stations', []):
            stations[s['code']] = s
        for code in diff.get('removed', []):
            stations.pop(code, None)
        if ts - last_kf_ts >= HISTORY_STATION_KEYFRAME_SECONDS:
            out.append((ts, json.dumps({'type': msg_type, 'stations': list(stations.values())}, separators=(',', ':'))))
            last_kf_ts = ts
        if i % 4000 == 3999:
            time.sleep(0)  # yield the GIL so live polling/WS threads aren't starved
    return out

def _backfill_station_keyframes(msg_type, diff_type):
    """Fill gaps larger than KEYFRAME between full station snapshots.

    Parse only diffs inside oversized gaps. Synthesised snapshots shrink those
    gaps, making later startup passes idempotent and inexpensive."""
    if not _history_cache_ready():
        return 0
    now = time.time()
    full_ts = [ts for ts, _p in _history_cache.rows_before(msg_type, now, ascending=True)]
    if not full_ts:
        return 0  # no full snapshot to anchor from; diffs alone can't be reconstructed
    # Fill oversized gaps between full snapshots, including the trailing gap.
    # Diffs before the first snapshot cannot be reconstructed.
    inserts = []
    boundaries = full_ts + [now]
    for base_ts, end_ts in zip(full_ts, boundaries[1:]):
        if end_ts - base_ts <= HISTORY_STATION_KEYFRAME_SECONDS:
            continue
        inserts += _keyframes_for_gap(msg_type, diff_type, base_ts, end_ts)
    if not inserts:
        return 0
    try:
        with db.Database() as cursor:
            cursor.executemany(
                'INSERT INTO history_log (ts, msg_type, payload, marker_kind, marker_label, event_ts) '
                'VALUES (?, ?, ?, NULL, NULL, NULL)',
                [(ts, msg_type, snap) for ts, snap in inserts]
            )
    except Exception as e:
        dmdws.logger.warning('History keyframe backfill DB insert failed for %s: %s', msg_type, e)
        return 0
    for ts, snap in inserts:
        _history_cache.add(ts, msg_type, snap)
    return len(inserts)

def _backfill_all_station_keyframes():
    if not HISTORY_CACHE_ENABLED:
        return
    started = time.time()
    try:
        n_nied = _backfill_station_keyframes('nied_stations', 'nied_stations_diff')
        n_snet = _backfill_station_keyframes('snet_stations', 'snet_stations_diff')
        dmdws.logger.info(
            'History keyframe backfill: inserted %d nied + %d snet keyframes in %.3fs',
            n_nied, n_snet, time.time() - started)
    except Exception as e:
        dmdws.logger.warning('History keyframe backfill failed: %s', e)

def _history_max_ts(msg_type, at):
    if _history_cache_ready():
        return _history_cache.max_ts_before(msg_type, at)
    with db.Database() as cursor:
        cursor.execute('SELECT MAX(ts) FROM history_log WHERE msg_type = ? AND ts <= ?', (msg_type, at))
        r = cursor.fetchone()
        return r[0] if r else None

def _history_rows_before(msg_type, at, ascending=True):
    if _history_cache_ready():
        return _history_cache.rows_before(msg_type, at, ascending=ascending)
    with db.Database() as cursor:
        cursor.execute(
            "SELECT ts, payload FROM history_log WHERE msg_type = ? AND ts <= ? ORDER BY ts %s" % ('ASC' if ascending else 'DESC'),
            (msg_type, at)
        )
        return cursor.fetchall()

def _history_latest_single(msg_type, clear_types, at):
    """Latest payload for a single-slot cache (tsunami, tsunami_obs) as of `at`,
    or None if a later clear superseded it."""
    if _history_cache_ready():
        row = _history_cache.latest_before(msg_type, at)
    else:
        with db.Database() as cursor:
            cursor.execute(
                'SELECT ts, payload FROM history_log WHERE msg_type = ? AND ts <= ? ORDER BY ts DESC LIMIT 1',
                (msg_type, at)
            )
            row = cursor.fetchone()
    if row is None:
        return None
    ts, payload = row
    clear_ts = None
    for ct in clear_types:
        ct_ts = _history_max_ts(ct, at)
        if ct_ts is not None:
            clear_ts = ct_ts if clear_ts is None else max(clear_ts, ct_ts)
    if clear_ts is not None and clear_ts >= ts:
        return None
    try:
        return json.loads(payload)
    except Exception:
        return None

def _history_latest_past_quake(at):
    """Latest post-event earthquake report (VXSE51/52/53, i.e. no 'last_report'
    field) as of `at`, or None if it has since been cleared (matches
    recent_past_quake_data)."""
    rows = _history_rows_before('earthquake', at, ascending=False)
    clear_ts = _history_max_ts('past_quake_clear', at)
    for ts, payload in rows:
        try:
            data = json.loads(payload)
        except Exception:
            continue
        if 'last_report' in data:
            continue  # EEW report, not a post-event one
        if clear_ts is not None and clear_ts >= ts:
            return None
        return data
    return None

def _history_active_eews(at):
    """All EEW reports (VXSE43/45/47, i.e. have a 'last_report' field) still active
    as of `at` — active means no eew_clear for that event's key arrived by then."""
    rows = _history_rows_before('earthquake', at, ascending=True)
    clear_rows = _history_rows_before('eew_clear', at, ascending=True)
    latest_by_key = {}
    for ts, payload in rows:
        try:
            data = json.loads(payload)
        except Exception:
            continue
        if 'last_report' not in data:
            continue
        key = data.get('event_id') or str(data.get('origin_time') or data.get('quake_time') or '')
        # ascending order: last write wins = latest report
        latest_by_key[key] = (ts, data)
    last_clear_ts = {}
    for ts, payload in clear_rows:
        try:
            key = json.loads(payload).get('key')
        except Exception:
            continue
        if key is not None:
            last_clear_ts[key] = ts  # ascending order: last write wins = latest clear
    return [data for key, (ts, data) in latest_by_key.items()
            if last_clear_ts.get(key) is None or last_clear_ts[key] < ts]

def _history_latest_jma_history(at):
    if _history_cache_ready():
        row = _history_cache.latest_before('jma_history', at)
    else:
        with db.Database() as cursor:
            cursor.execute(
                "SELECT ts, payload FROM history_log WHERE msg_type = 'jma_history' AND ts <= ? ORDER BY ts DESC LIMIT 1",
                (at,)
            )
            row = cursor.fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[1])
    except Exception:
        return None

def _history_reconstruct_state(at):
    """Builds the ordered list of messages that, if fed through displayData() in
    order, reproduce exactly what a live client would have seen at time `at` —
    the historical equivalent of send_recent_data()."""
    messages = []
    nied = _merge_station_snapshot('nied_stations', 'nied_stations_diff', at)
    if nied:
        messages.append(nied)
    snet = _merge_station_snapshot('snet_stations', 'snet_stations_diff', at)
    if snet:
        messages.append(snet)
    past_quake = _history_latest_past_quake(at)
    if past_quake:
        messages.append(past_quake)
    messages.extend(_history_active_eews(at))
    tsunami = _history_latest_single('tsunami', ['tsunami_clear'], at)
    if tsunami:
        messages.append(tsunami)
    tsunami_obs = _history_latest_single('tsunami_obs', ['tsunami_clear', 'tsunami_obs_clear'], at)
    if tsunami_obs:
        messages.append(tsunami_obs)
    jma_hist = _history_latest_jma_history(at)
    if jma_hist:
        messages.append(jma_hist)
    events = [e for e in _build_quake_points_index() if e.get('ts') is None or e['ts'] <= at]
    messages.append({'type': 'quake_points_index', 'events': events})
    return messages

@app.route('/api/history/markers')
def history_markers():
    now = time.time()
    ts_from = _parse_ts_param('from', now - HISTORY_RETENTION_SECONDS)
    ts_to = _parse_ts_param('to', now)
    if _history_cache_ready():
        rows = _history_cache.markers_in_range(ts_from, ts_to)
    else:
        with db.Database() as cursor:
            cursor.execute(
                'SELECT ts, msg_type, marker_kind, marker_label, event_ts FROM history_log '
                'WHERE marker_kind IS NOT NULL AND ts BETWEEN ? AND ? ORDER BY ts ASC',
                (ts_from, ts_to)
            )
            rows = cursor.fetchall()
    markers = [{'ts': r[0], 'type': r[1], 'kind': r[2], 'label': r[3], 'event_ts': r[4] or r[0]} for r in rows]
    return json.dumps({'markers': markers}, separators=(',', ':'))

@app.route('/api/history/gaps')
def history_gaps():
    now = time.time()
    ts_from = _parse_ts_param('from', now - HISTORY_RETENTION_SECONDS)
    ts_to = _parse_ts_param('to', now)
    gap_threshold = 20  # heartbeats are sent every 10s; anything wider means downtime
    if _history_cache_ready():
        heartbeats = [ts for (ts, _payload) in _history_cache.range('heartbeat', ts_from, ts_to, from_exclusive=False)]
    else:
        with db.Database() as cursor:
            cursor.execute(
                "SELECT ts FROM history_log WHERE msg_type = 'heartbeat' AND ts BETWEEN ? AND ? ORDER BY ts ASC",
                (ts_from, ts_to)
            )
            heartbeats = [r[0] for r in cursor.fetchall()]
    gaps = []
    if not heartbeats:
        gaps.append({'start': ts_from, 'end': ts_to})
    else:
        if heartbeats[0] - ts_from > gap_threshold:
            gaps.append({'start': ts_from, 'end': heartbeats[0]})
        for prev, cur in zip(heartbeats, heartbeats[1:]):
            if cur - prev > gap_threshold:
                gaps.append({'start': prev, 'end': cur})
        if ts_to - heartbeats[-1] > gap_threshold:
            gaps.append({'start': heartbeats[-1], 'end': ts_to})
    return json.dumps({'gaps': gaps}, separators=(',', ':'))

@app.route('/api/history/state')
def history_state():
    at = _parse_ts_param('at', time.time())
    return json.dumps({'at': at, 'messages': _history_reconstruct_state(at)}, separators=(',', ':'))

@app.route('/api/history/window')
def history_window():
    now = time.time()
    ts_from = _parse_ts_param('from', now - 600)
    ts_to = _parse_ts_param('to', now)
    # cap one request's span so a client can't pull the whole day at once
    if ts_to - ts_from > 1800:
        ts_to = ts_from + 1800
    try:
        limit = min(int(request.args.get('limit', 2000)), 2000)
    except ValueError:
        limit = 2000
    if _history_cache_ready():
        rows = _history_cache.window(ts_from, ts_to, limit, exclude_type='heartbeat')
    else:
        with db.Database() as cursor:
            cursor.execute(
                "SELECT ts, payload FROM history_log WHERE payload IS NOT NULL AND msg_type != 'heartbeat' "
                "AND ts BETWEEN ? AND ? ORDER BY ts ASC LIMIT ?",
                (ts_from, ts_to, limit)
            )
            rows = cursor.fetchall()
    # Splice stored JSON directly to avoid decoding and re-encoding large snapshots.
    parts = ['{"ts":%s,"data":%s}' % (json.dumps(ts), payload)
             for ts, payload in rows if payload and payload[0] in '{[']
    return '{"messages":[' + ','.join(parts) + ']}'

# --- Admin panel ---

ADMIN_TABLES = ['VXSE43', 'VXSE45', 'VXSE47', 'VXSE51', 'VXSE52', 'VXSE53', 'VTSE41', 'VTSE51']
ADMIN_RAW_TYPES = ADMIN_TABLES + ['VTSE52']

TEST_NOTIFICATIONS = {
    'warning': {
        'title_en': '[TEST] Earthquake Early Warning',
        'title_ja': '[TEST] 緊急地震速報',
        'message_en': 'This is a test of the EEW warning push notification.',
        'message_ja': 'これは緊急地震速報のテスト通知です。',
        'tag': 'alerts',
        'ntfy_topic_en': NTFY_TOPIC_ALERTS_EN,
        'ntfy_topic_ja': NTFY_TOPIC_ALERTS_JA,
    },
    'forecast': {
        'title_en': '[TEST] Earthquake Forecast',
        'title_ja': '[TEST] 地震動予測',
        'message_en': 'This is a test of the EEW forecast push notification.',
        'message_ja': 'これは地震動予測のテスト通知です。',
        'tag': 'forecasts',
        'ntfy_topic_en': NTFY_TOPIC_FORECASTS_EN,
        'ntfy_topic_ja': NTFY_TOPIC_FORECASTS_JA,
    },
    'tsunami': {
        'title_en': '[TEST] Tsunami Information',
        'title_ja': '[TEST] 津波情報',
        'message_en': 'This is a test of the tsunami information push notification.',
        'message_ja': 'これは津波情報のテスト通知です。',
        'tag': 'alerts',
        'ntfy_topic_en': NTFY_TOPIC_ALERTS_EN,
        'ntfy_topic_ja': NTFY_TOPIC_ALERTS_JA,
    },
}

# Rate-limit failed logins per IP for the shared admin password.
_LOGIN_MAX_FAILURES = 5
_LOGIN_WINDOW_SECONDS = 300
_LOGIN_LOCKOUT_SECONDS = 900
_login_failures = {}
_login_failures_lock = threading.Lock()

def _login_lockout_remaining(ip):
    """Seconds this caller must wait before trying again, or 0 if it may try now."""
    now = time.time()
    with _login_failures_lock:
        entry = _login_failures.get(ip)
        if not entry:
            return 0
        count, stamp = entry
        if count < _LOGIN_MAX_FAILURES:
            return 0
        remaining = _LOGIN_LOCKOUT_SECONDS - (now - stamp)
        if remaining <= 0:
            del _login_failures[ip]
            return 0
        return int(remaining) + 1

def _record_login_failure(ip):
    now = time.time()
    with _login_failures_lock:
        # Drop stale login failures to bound memory use across client IPs.
        if len(_login_failures) > 1024:
            for stale_ip, (_, stale_stamp) in list(_login_failures.items()):
                if now - stale_stamp > _LOGIN_LOCKOUT_SECONDS:
                    del _login_failures[stale_ip]
        count, stamp = _login_failures.get(ip, (0, now))
        if count < _LOGIN_MAX_FAILURES and now - stamp > _LOGIN_WINDOW_SECONDS:
            count, stamp = 0, now
        count += 1
        if count >= _LOGIN_MAX_FAILURES:
            # Each further failure restarts the lockout clock from now.
            stamp = now
        _login_failures[ip] = (count, stamp)

def _clear_login_failures(ip):
    with _login_failures_lock:
        _login_failures.pop(ip, None)

def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not ADMIN_ENABLED:
            abort(404)
        if not session.get('admin'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'unauthorized'}), 401
            return redirect('/admin/login')
        return f(*args, **kwargs)
    return wrapper

@app.route('/admin/login')
def admin_login_page():
    if not ADMIN_ENABLED:
        abort(404)
    if session.get('admin'):
        return redirect('/admin')
    return send_from_directory(ADMIN_DIR, 'login.html')

@app.route('/api/admin/login', methods=['POST'])
def admin_login_submit():
    if not ADMIN_ENABLED:
        abort(404)

    ip = request.remote_addr or 'unknown'
    wait = _login_lockout_remaining(ip)
    if wait:
        return jsonify({
            'ok': False,
            'error': f'Too many failed attempts. Try again in {wait} seconds.',
        }), 429

    data = request.get_json(silent=True) or {}
    supplied = data.get('password')
    # Use constant-time comparison; UTF-8 bytes also support non-ASCII passwords.
    if isinstance(supplied, str) and secrets.compare_digest(
        supplied.encode('utf-8'), ADMIN_PASSWORD.encode('utf-8')
    ):
        _clear_login_failures(ip)
        session.permanent = True
        session['admin'] = True
        return jsonify({'ok': True})

    _record_login_failure(ip)
    dmdws.logger.warning(f"Failed admin login attempt from {ip}")
    return jsonify({'ok': False, 'error': 'Invalid password'}), 401

@app.route('/admin/logout')
def admin_logout():
    if not ADMIN_ENABLED:
        abort(404)
    session.pop('admin', None)
    return redirect('/admin/login')

@app.route('/admin')
@admin_required
def admin_page():
    return send_from_directory(ADMIN_DIR, 'admin.html')

@app.route('/admin/<path:filename>')
@admin_required
def admin_static(filename):
    return send_from_directory(ADMIN_DIR, filename)

@app.route('/api/admin/status')
@admin_required
def admin_status():
    eew_summary = []
    for key, ev in recent_earthquake_data.items():
        eew_summary.append({
            'key': key,
            'event_id': ev.get('event_id'),
            'is_plum': ev.get('is_plum'),
            'warning': ev.get('warning'),
            'last_report': ev.get('last_report'),
            'report_num': ev.get('report_num'),
            'max_int': ev.get('max_int'),
            'magnitude': ev.get('magnitude'),
            'epi_location_en': ev.get('epi_location_en'),
            'report_time': ev.get('report_time'),
        })
    cache_stats = _history_cache.stats()
    if _history_cache_ready():
        # Use incremental cache stats to avoid a full-table scan on every admin poll.
        history_log_count = cache_stats['row_count']
        history_log_oldest = cache_stats['oldest_ts']
        history_log_newest = cache_stats['newest_ts']
        history_log_size_bytes = cache_stats['size_bytes']
    else:
        with db.Database() as cursor:
            cursor.execute('SELECT COUNT(*), MIN(ts), MAX(ts) FROM history_log')
            history_log_count, history_log_oldest, history_log_newest = cursor.fetchone()
            cursor.execute('''SELECT SUM(LENGTH(msg_type) + COALESCE(LENGTH(payload), 0) +
                COALESCE(LENGTH(marker_kind), 0) + COALESCE(LENGTH(marker_label), 0))
                FROM history_log''')
            history_log_size_bytes = cursor.fetchone()[0] or 0

    return jsonify({
        'open_sockets': len(open_sockets),
        'server_time': int(time.time()),
        'recent_earthquake_data': eew_summary,
        'recent_tsunami_data': recent_tsunami_data,
        'recent_offshore_obs_data': recent_offshore_obs_data,
        'recent_past_quake_data': recent_past_quake_data,
        'jma_history_count': len(recent_jma_history) if recent_jma_history else 0,
        'nied_station_count': len(_last_nied_full),
        'snet_station_count': len(_last_snet_full),
        'history_log_count': history_log_count or 0,
        'history_log_oldest_ts': history_log_oldest,
        'history_log_newest_ts': history_log_newest,
        'history_log_size_bytes': history_log_size_bytes,
        'history_log_retention_seconds': HISTORY_RETENTION_SECONDS,
        'history_cache_enabled': HISTORY_CACHE_ENABLED,
        'history_cache_ready': cache_stats['ready'],
        'history_cache_row_count': cache_stats['row_count'],
        'history_cache_size_bytes': cache_stats['size_bytes'],
        'history_cache_oldest_ts': cache_stats['oldest_ts'],
        'history_cache_newest_ts': cache_stats['newest_ts'],
        'history_cache_load_seconds': cache_stats['load_seconds'],
        'history_cache_load_error': cache_stats['load_error'],
        'history_cache_loaded_at': cache_stats['loaded_at'],
    })

@app.route('/api/admin/recent')
@admin_required
def admin_recent():
    hours = request.args.get('hours', default=24, type=int)
    hours = max(1, min(hours, 24 * 30))
    cutoff = int(time.time()) - hours * 3600

    tables = {}
    with db.Database() as cursor:
        for table in ADMIN_TABLES:
            cursor.execute(
                f'SELECT * FROM {table} WHERE report_time >= ? ORDER BY id DESC LIMIT 50',
                (cutoff,)
            )
            cols = [d[0] for d in cursor.description]
            tables[table] = [dict(zip(cols, row)) for row in cursor.fetchall()]

    file_counts = {}
    for msg_type in ADMIN_RAW_TYPES:
        dir_path = os.path.join(BASE_DIR, 'data_messages', msg_type)
        total = 0
        recent = 0
        latest = 0
        for path in glob.glob(os.path.join(dir_path, '*.json')):
            total += 1
            ts = _persisted_file_ts(path)
            if ts is None:
                continue
            latest = max(latest, ts)
            if ts >= cutoff:
                recent += 1
        file_counts[msg_type] = {'total': total, 'recent': recent, 'latest': latest}

    return jsonify({'tables': tables, 'file_counts': file_counts, 'cutoff': cutoff, 'hours': hours})

@app.route('/api/admin/test_notification', methods=['POST'])
@admin_required
def admin_test_notification():
    data = request.get_json(silent=True) or {}
    kind = data.get('kind', '')
    cfg = TEST_NOTIFICATIONS.get(kind)
    if not cfg:
        return jsonify({'ok': False, 'error': 'Unknown notification kind'}), 400
    result = send_notification(cfg['title_en'], cfg['title_ja'], cfg['message_en'], cfg['message_ja'], tag=cfg['tag'])
    return jsonify(result)

@app.route('/api/admin/test_ntfy_notification', methods=['POST'])
@admin_required
def admin_test_ntfy_notification():
    data = request.get_json(silent=True) or {}
    kind = data.get('kind', '')
    cfg = TEST_NOTIFICATIONS.get(kind)
    if not cfg:
        return jsonify({'ok': False, 'error': 'Unknown notification kind'}), 400
    result_en = send_ntfy_notification(cfg['title_en'], cfg['message_en'], topic=cfg['ntfy_topic_en'])
    result_ja = send_ntfy_notification(cfg['title_ja'], cfg['message_ja'], topic=cfg['ntfy_topic_ja'], click=SITE_LINK_JA)
    return jsonify({
        'ok': result_en.get('ok') and result_ja.get('ok'),
        'en': result_en,
        'ja': result_ja,
    })

# Sample Kumamoto quake for warning/forecast map-image tests.
_TEST_QUAKE_MAP_SAMPLE = {
    'lat': 32.507, 'lon': 130.686, 'max_int': '5-',
    'area_intensities': [{'code': '741', 'max_int': '5-'}],
}

@app.route('/api/admin/test_discord_notification', methods=['POST'])
@admin_required
def admin_test_discord_notification():
    data = request.get_json(silent=True) or {}
    kind = data.get('kind', '')
    cfg = TEST_NOTIFICATIONS.get(kind)
    if not cfg:
        return jsonify({'ok': False, 'error': 'Unknown notification kind'}), 400
    image_bytes = render_quake_map(_TEST_QUAKE_MAP_SAMPLE) if kind in ('warning', 'forecast') else None
    result_en = send_discord_notification(cfg['title_en'], cfg['message_en'], DISCORD_WEBHOOK_EN, SITE_LINK_EN, image_bytes=image_bytes)
    result_ja = send_discord_notification(cfg['title_ja'], cfg['message_ja'], DISCORD_WEBHOOK_JA, SITE_LINK_JA, image_bytes=image_bytes)
    return jsonify({
        'ok': result_en.get('ok') and result_ja.get('ok'),
        'en': result_en,
        'ja': result_ja,
    })


_LONG_CACHE_EXTS = ('.geojson', '.mp3', '.png', '.ico', '.webmanifest', '.woff', '.woff2')

_index_cache: dict = {}  # lang -> {'key': (mtimes), 'html': str}
_index_lock = threading.Lock()

# Per-language head overrides for the English source index.html.
_INDEX_LOCALIZED = {
    'ja': {
        '<html lang="en">': '<html lang="ja">',
        '<title>WebQuake - Japan EEW & Tsunami Map</title>':
            '<title>WebQuake - 緊急地震速報・津波警報マップ</title>',
        'content="Live Japan earthquake early warning (EEW) and tsunami warning map. Real-time JMA alerts, seismic intensity readings, and a 48-hour replay of past earthquakes.">':
            'content="日本の緊急地震速報（EEW）と津波警報のリアルタイムマップ。気象庁(JMA)の速報、観測点の震度情報、過去48時間のリプレイ機能を提供。">',
        'content="WebQuake - Japan Earthquake Early Warning & Tsunami Map">':
            'content="WebQuake - 緊急地震速報・津波警報マップ">',
        '<link rel="canonical" href="https://URL/">': '<link rel="canonical" href="https://URL/ja/">',
        'content="https://URL/">': 'content="https://URL/ja/">',
        "window.__WEBQUAKE_LANG = 'en';": "window.__WEBQUAKE_LANG = 'ja';",
    },
    'zh': {
        '<html lang="en">': '<html lang="zh">',
        '<title>WebQuake - Japan EEW & Tsunami Map</title>':
            '<title>WebQuake - 緊急地震速報與海嘯警報地圖</title>',
        'content="Live Japan earthquake early warning (EEW) and tsunami warning map. Real-time JMA alerts, seismic intensity readings, and a 48-hour replay of past earthquakes.">':
            'content="即時日本緊急地震速報（EEW）與海嘯警報地圖，同時提供台灣地震測站資訊。提供氣象廳（JMA）速報、觀測點震度資訊，以及過去48小時的重播功能。">',
        'content="WebQuake - Japan Earthquake Early Warning & Tsunami Map">':
            'content="WebQuake - 緊急地震速報與海嘯警報地圖">',
        '<link rel="canonical" href="https://URL/">': '<link rel="canonical" href="https://URL/zh/">',
        'content="https://URL/">': 'content="https://URL/zh/">',
        "window.__WEBQUAKE_LANG = 'en';": "window.__WEBQUAKE_LANG = 'zh';",
    },
}

def _render_index(lang='en', public_dir=None, dev=False):
    """Render localised index.html with mtime-based CSS/JS cache busting.

    Cache by (public_dir, lang) until a source file changes.
"""
    public_dir = public_dir or _public
    idx_path = os.path.join(public_dir, 'index.html')
    js_path = os.path.join(public_dir, 'app.js')
    js_mtime = os.path.getmtime(js_path)
    key = (os.path.getmtime(idx_path), js_mtime)
    cache_key = (public_dir, lang)
    with _index_lock:
        cached = _index_cache.get(cache_key)
        if cached is not None and cached['key'] == key:
            return cached['html']
        with open(idx_path, encoding='utf-8') as f:
            html = f.read()
        version = str(int(js_mtime))
        html = html.replace('href="/style.css"', f'href="/style.css?v={version}"')
        html = html.replace('src="/app.js"', f'src="/app.js?v={version}"')
        for old, new in _INDEX_LOCALIZED.get(lang, {}).items():
            html = html.replace(old, new)
        _index_cache[cache_key] = {'key': key, 'html': html}
        return html

@app.route('/')
def index():
    response = app.response_class(_render_index('en'), mimetype='text/html')
    response.headers['Cache-Control'] = 'no-cache, max-age=0'
    return response

@app.route('/ja/')
def index_ja():
    response = app.response_class(_render_index('ja'), mimetype='text/html')
    response.headers['Cache-Control'] = 'no-cache, max-age=0'
    return response

@app.route('/zh/')
def index_zh():
    response = app.response_class(_render_index('zh'), mimetype='text/html')
    response.headers['Cache-Control'] = 'no-cache, max-age=0'
    return response

# Cache compressed assets by file version to avoid per-request compression.
# Content-Encoding makes flask-compress skip these precompressed responses.
_PRECOMPRESS_EXTS = ('.geojson', '.js', '.css', '.json', '.webmanifest', '.svg')
_PRECOMPRESS_MIN_SIZE = 8192
_precompressed: dict = {}  # (path, mtime, size, algo) -> compressed bytes
_precompress_lock = threading.Lock()

def _accepted_encodings():
    out = set()
    for part in request.headers.get('Accept-Encoding', '').lower().split(','):
        name, _, q = part.strip().partition(';')
        try:
            quality = float(q.split('=', 1)[1]) if q else 1.0
        except (IndexError, ValueError):
            quality = 1.0
        if name and quality > 0:
            out.add(name.strip())
    return out

def _try_precompressed(filename, max_age):
    """Serve a cached compressed copy of a large static asset, or None to fall
    back to send_from_directory (small file, unsupported encoding, missing, ...)."""
    if not filename.endswith(_PRECOMPRESS_EXTS):
        return None
    accepted = _accepted_encodings()
    algo = 'br' if 'br' in accepted else ('gzip' if 'gzip' in accepted else None)
    if algo is None:
        return None
    path = safe_join(_public, filename)
    if path is None or not os.path.isfile(path):
        return None
    stat = os.stat(path)
    if stat.st_size < _PRECOMPRESS_MIN_SIZE:
        return None
    key = (path, stat.st_mtime, stat.st_size, algo)
    with _precompress_lock:
        body = _precompressed.get(key)
    if body is None:
        with open(path, 'rb') as f:
            raw = f.read()
        body = brotli.compress(raw, quality=9) if algo == 'br' else gzip.compress(raw, compresslevel=9)
        with _precompress_lock:
            for stale in [k for k in _precompressed if k[0] == path and k != key]:
                del _precompressed[stale]  # file changed on disk; drop old versions
            _precompressed[key] = body
    resp = app.response_class(body, mimetype=mimetypes.guess_type(filename)[0] or 'application/octet-stream')
    resp.headers['Content-Encoding'] = algo
    resp.headers['Cache-Control'] = f'public, max-age={max_age}'
    resp.set_etag(f'{stat.st_mtime}-{stat.st_size}-{algo}')
    resp.make_conditional(request)
    return resp

@app.route('/<path:filename>')
def static_files(filename):
    # Long cache for assets that rarely change; short for app.js/style.css/manifest etc.
    max_age = 86400 if filename.endswith(_LONG_CACHE_EXTS) else 3600
    resp = _try_precompressed(filename, max_age)
    if resp is not None:
        return resp
    return send_from_directory(_public, filename, max_age=max_age)

@sock.route('/ws')
def websocket_recv_open(ws: WebsocketBase):
    try:
        ip = websocket_hub.client_ip(
            request.remote_addr or '0.0.0.0',
            request.headers.get('CF-Connecting-IP'), _ws_trusted_proxies,
        )
        if not open_sockets.admit(ws, ip):
            return
        send_recent_data(ws)
        open_sockets.activate(ws)
        ping_window = time.monotonic()
        ping_count = 0
        while open_sockets.contains(ws):
            message = ws.receive(timeout=12)
            if message is None:
                break
            # Only the existing heartbeat protocol is accepted. Bound reply work
            # as well as message size; real clients send one ping every 6 seconds.
            now = time.monotonic()
            if now - ping_window >= 1:
                ping_window, ping_count = now, 0
            ping_count += 1
            if message != '!ping' or ping_count > 10:
                break
            open_sockets.send(ws, '!pong')
    finally:
        open_sockets.disconnect(ws)


def send_notification(title_en, title_ja, message_en, message_ja, tag="alerts"):
    if not ONESIGNAL_APP_ID or not ONESIGNAL_REST_API_KEY:
        dmdws.logger.warning("OneSignal notification skipped: OneSignal is not configured")
        return {'ok': False, 'error': 'not configured'}

    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": f"Key {ONESIGNAL_REST_API_KEY}",
    }

    # Target users subscribed to the given alert tag; OneSignal picks the
    # en/ja content based on each subscriber's stored language preference.
    filters = [
        {"field": "tag", "key": tag, "relation": "=", "value": "subscribed"},
    ]

    payload = {
        "app_id": ONESIGNAL_APP_ID,
        "filters": filters,
        "headings": {"en": title_en, "ja": title_ja},
        "contents": {"en": message_en, "ja": message_ja},
    }

    url = "https://api.onesignal.com/notifications"

    try:
        response = _http_session().post(url, headers=headers, json=payload, timeout=10)
        if response.ok:
            notif_id = response.json().get('id')
            dmdws.logger.info(f"OneSignal notification sent: {notif_id}")
            return {'ok': True, 'id': notif_id}
        else:
            dmdws.logger.warning(f"OneSignal notification failed ({response.status_code}): {response.text}")
            return {'ok': False, 'error': f"{response.status_code}: {response.text}"}
    except requests.RequestException as e:
        dmdws.logger.error(f"OneSignal notification request failed: {e}")
        return {'ok': False, 'error': str(e)}


def send_ntfy_notification(title, message, topic, click=NTFY_CLICK_URL):
    # Reject an empty topic to avoid posting to the bare server URL.
    if not NTFY_URL or not NTFY_TOKEN or not topic:
        dmdws.logger.warning("ntfy notification skipped: ntfy is not configured")
        return {'ok': False, 'error': 'not configured'}

    headers = {
        "Authorization": f"Bearer {NTFY_TOKEN}",
        "Title": f"WebQuake: {title}".encode("utf-8"),
        "Content-Type": "text/plain; charset=utf-8",
    }
    if click:
        headers["Click"] = click
    if NTFY_ICON_URL:
        headers["Icon"] = NTFY_ICON_URL

    url = f"{NTFY_URL.rstrip('/')}/{topic}"

    try:
        response = _http_session().post(url, headers=headers, data=message.encode("utf-8"), timeout=10)
        if response.ok:
            dmdws.logger.info(f"ntfy notification sent to topic '{topic}'")
            return {'ok': True}
        else:
            dmdws.logger.warning(f"ntfy notification failed ({response.status_code}): {response.text}")
            return {'ok': False, 'error': f"{response.status_code}: {response.text}"}
    except requests.RequestException as e:
        dmdws.logger.error(f"ntfy notification request failed: {e}")
        return {'ok': False, 'error': str(e)}


def _crosspost_discord_message(channel_id, message_id):
    """Publish a message so servers following the (announcement-type) channel get a
    copy."""
    if not DISCORD_BOT_TOKEN or not channel_id or not message_id:
        return
    url = f"https://discord.com/api/v10/channels/{channel_id}/messages/{message_id}/crosspost"
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    try:
        response = _http_session().post(url, headers=headers, timeout=10)
        if not response.ok:
            dmdws.logger.warning(f"Discord crosspost failed ({response.status_code}): {response.text}")
    except requests.RequestException as e:
        dmdws.logger.error(f"Discord crosspost request failed: {e}")

def send_discord_notification(title, message, webhook_url, link, image_bytes=None):
    if not webhook_url:
        dmdws.logger.warning("Discord notification skipped: webhook URL is not configured")
        return {'ok': False, 'error': 'not configured'}

    embed = {
        "title": title,
        "description": message,
    }
    if link:
        embed["url"] = link
    if image_bytes:
        embed["image"] = {"url": "attachment://quake.png"}
    payload = {"embeds": [embed]}

    # Request the created message ID/channel ID for crossposting.
    sep = '&' if '?' in webhook_url else '?'
    post_url = f"{webhook_url}{sep}wait=true"

    try:
        if image_bytes:
            response = _http_session().post(
                post_url,
                data={"payload_json": json.dumps(payload)},
                files={"file": ("quake.png", image_bytes, "image/png")},
                timeout=10,
            )
        else:
            response = _http_session().post(post_url, json=payload, timeout=10)
        if response.ok:
            dmdws.logger.info("Discord notification sent")
            sent_message = response.json()
            _crosspost_discord_message(sent_message.get('channel_id'), sent_message.get('id'))
            return {'ok': True}
        else:
            dmdws.logger.warning(f"Discord notification failed ({response.status_code}): {response.text}")
            return {'ok': False, 'error': f"{response.status_code}: {response.text}"}
    except requests.RequestException as e:
        dmdws.logger.error(f"Discord notification request failed: {e}")
        return {'ok': False, 'error': str(e)}
conn.set_message_listener(on_message, threading=False)

conn.start()

# Backfill replay keyframes in the background without delaying startup.
threading.Thread(target=_backfill_all_station_keyframes, daemon=True).start()

app.run('0.0.0.0', 8000)
