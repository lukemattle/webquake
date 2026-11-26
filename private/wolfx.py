"""Wolfx JMA feeds adapted to the telegram subset consumed by main.py.

These are normalized records, not original DM-DSS telegrams. See README.md for provider setup and limitations.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import re
import unicodedata
import threading
import time

import requests
import websocket
from enums import eng_codes

logger = logging.getLogger(__name__)
JST = timezone(timedelta(hours=9))
REGION_CODES = {}  # Filled from the same local geometry the frontend uses.


def area_name(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def timestamp(value):
    if not value:
        return None
    try:
        if '/' in value:
            return datetime.strptime(value, '%Y/%m/%d %H:%M:%S').replace(tzinfo=JST).timestamp()
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return parsed.replace(tzinfo=parsed.tzinfo or JST).timestamp()
    except (ValueError, TypeError):
        return None


def iso(value):
    ts = timestamp(value)
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None


def intensity(value):
    value = unicodedata.normalize('NFKC', str(value)).replace('弱', '-').replace('強', '+')
    value = value.replace('程度以上', '').replace('以上', '').replace('程度', '')
    return value if value in ('0', '1', '2', '3', '4', '5-', '5+', '6-', '6+', '7') else None


def envelope(product, event_id, issued, target, body, **metadata):
    return {
        'type': 'data', 'source': 'wolfx', 'head': {'type': product},
        'xmlReport': {'head': {'eventId': event_id}},
        'body': {'reportDateTime': issued, 'targetDateTime': target,
                 'body': body, **metadata},
    }


def earthquake(hypo, magnitude, origin):
    return {
        'originTime': origin,
        'hypocenter': {
            'code': hypo.get('code') or '', 'name': hypo.get('name') or 'Unknown',
            'coordinate': {'latitude': {'value': hypo.get('latitude')},
                           'longitude': {'value': hypo.get('longitude')}},
            'depth': {'value': hypo.get('depth')},
        },
        'magnitude': {'value': magnitude},
    }


def adapt_eew(data):
    if data.get('isTraining') or (data.get('Issue') or {}).get('Status') not in (None, '通常'):
        return None
    issued, origin = iso(data.get('AnnouncedTime')), iso(data.get('OriginTime'))
    event_id = data.get('EventID')
    if not issued or not event_id or (not origin and not data.get('isCancel')):
        return None
    body = {'isCanceled': data.get('isCancel') is True,
            'isLastInfo': data.get('isFinal') is True, 'isWarning': data.get('isWarn') is True}
    if not body['isCanceled']:
        # Names carry no official code in this feed; use local JMA geometry names.
        name = data.get('Hypocenter') or 'Unknown'
        raw_code = re.search(r'\s(\d{3})\s+[NS]\d', data.get('OriginalText') or '')
        code = raw_code.group(1) if raw_code and raw_code.group(1) in eng_codes.regions else REGION_CODES.get(area_name(name), '')
        hypo = {'name': name, 'code': code,
                'latitude': data.get('Latitude'), 'longitude': data.get('Longitude'),
                'depth': data.get('Depth')}
        magnitude = data.get('Magnitude', data.get('Magunitude'))
        level_method = '最大予測震度のみ' in str(data.get('CodeType', ''))
        if level_method or (hypo['latitude'] == 0 and hypo['longitude'] == 0):
            hypo.update(latitude=None, longitude=None, depth=None)
            magnitude = None
        elif hypo['depth'] == 0:
            hypo['depth'] = None  # The compatibility zero may mean unknown.
        body['earthquake'] = earthquake(hypo, magnitude or None, origin)
        if data.get('isAssumption') is True:
            body['earthquake']['condition'] = '仮定震源要素'
        maximum = intensity(data.get('MaxIntensity'))
        regions = []
        for area in data.get('WarnArea') or []:
            code = REGION_CODES.get(area_name(area.get('Chiiki')))
            if code:
                regions.append({'code': code, 'name': area['Chiiki'],
                                'forecastMaxInt': {'from': intensity(area.get('Shindo2')),
                                                   'to': intensity(area.get('Shindo1'))},
                                'isWarning': area.get('Type') == 'Warning',
                                'isPlum': 'PLUM' in str(area.get('Arrive', '')).upper()})
        if maximum or regions:
            body['intensity'] = {'forecastMaxInt': {'from': maximum, 'to': maximum}, 'regions': regions}
    message = envelope('VXSE45', event_id, issued, origin or issued, body, serialNo=data.get('Serial', 0))
    message['wolfx_payload'] = data
    return message


def adapt_report(event, report):
    product = report.get('product')
    if product not in ('VXSE51', 'VXSE52', 'VXSE53', 'VXSE61', 'VXSE62') or report.get('status') == 'cancelled':
        return None
    issued = iso(report.get('time'))
    origin = iso(report.get('originTime'))
    target = origin or iso(report.get('arrivalTime')) or iso(report.get('targetTime'))
    if not issued or not target or not event.get('eventId'):
        return None
    body = {}
    index = report.get('hypocenter')
    hypos = event.get('hypocenters') or []
    if isinstance(index, int) and not isinstance(index, bool) and 0 <= index < len(hypos):
        body['earthquake'] = earthquake(hypos[index], report.get('magnitude'), origin)
    if product in ('VXSE52', 'VXSE53', 'VXSE61', 'VXSE62') and 'earthquake' not in body:
        return None  # Do not invent a hypocenter from another product's fields.
    obs = report.get('observation') or {}
    stations = {}
    for parent in (obs.get('areas') or []) + (obs.get('cities') or []):
        for station in parent.get('stations') or []:
            if station.get('code') and intensity(station.get('int')):
                stations[station['code']] = {**station, 'int': intensity(station['int'])}
    body['intensity'] = {
        'maxInt': intensity(obs.get('maxIntensity')),
        'prefectures': [{'code': p['code'], 'name': p.get('name', '')} for p in obs.get('prefs') or []],
        'regions': [{'code': a['code'], 'name': a.get('name', ''), 'maxInt': intensity(a.get('int'))}
                    for a in obs.get('areas') or []],
        'stations': list(stations.values()),
        'forecastMaxLgInt': {'to': obs.get('maxLongPeriod'), 'from': obs.get('maxLongPeriod')},
    }
    # Some detail products publish the maximum only on their individual areas.
    if not obs.get('maxLongPeriod'):
        classes = [str(a.get('lg')) for a in (obs.get('areas') or []) + (obs.get('prefs') or [])]
        maximum = max((int(v) for v in classes if v in ('1', '2', '3', '4')), default=0)
        body['intensity']['forecastMaxLgInt'] = {'to': str(maximum) if maximum else None}
    comments = report.get('comments') or {}
    body['comments'] = {'free': '\n'.join(str(v) for v in comments.values() if isinstance(v, str)),
                        'forecast': {'codes': ['0215'] if 'この地震による津波の心配はありません' in comments.get('forecast', '') else []}}
    # The existing event schema already has max_lpgm; no new tables are needed.
    message = envelope('VXSE53' if product in ('VXSE61', 'VXSE62') else product,
                       event['eventId'], issued, target, body)
    message['wolfx_product'] = product
    message['wolfx_payload'] = {'event': event, 'report': report}
    return message


def wave(value):
    value = value or {}
    text = value.get('text') or value.get('condition') or ''
    conditions = value.get('conditions') or []
    if isinstance(conditions, str):
        conditions = [conditions]
    text = text or ' '.join(conditions)
    qualitative = next((label for label in ('巨大', '高い', '微弱', '欠測') if label in text), text)
    return {'height': {'value': value.get('height'), 'condition': qualitative,
                       'over': bool(value.get('over') or '超' in text or '以上' in text)},
            'condition': text, 'arrivalTime': iso(value.get('time'))}


def tsunami_area(area):
    return {'code': area.get('code', ''), 'name': area.get('name', ''),
            'kind': {'code': area.get('kind', '')},
            'firstHeight': wave(area.get('firstWave')),
            'maxHeight': wave(area.get('maxWave')),
            'stations': [tsunami_area(station) for station in area.get('stations') or []]}


def adapt_tsunami(data, now=None):
    now = time.time() if now is None else now
    deadline = timestamp(data.get('validUntil'))
    if data.get('trusted') is not True or deadline is None or deadline <= now:
        return []
    report = data.get('report') or {}
    issued = iso(report.get('time'))
    if not issued:
        return []
    status = data.get('status')
    if status not in ('active', 'forecast_only', 'cleared', 'none_reported'):
        return []  # Unknown/expired is never an official all-clear.
    body = {'tsunami': {'forecasts': [tsunami_area(a) for a in data.get('areas') or []]}}
    clear = status in ('cleared', 'none_reported')
    if clear:
        body['comments'] = {'warning': {'codes': ['0107']}}
    elif not body['tsunami']['forecasts']:
        return []
    event_id = next(iter(report.get('eventIds') or []), '')
    target = iso(report.get('targetTime') or report.get('originTime')) or issued
    messages = [envelope('VTSE41', event_id, issued, target, body)]
    if clear:
        messages[0]['wolfx_payload'] = data
        return messages
    official_deadline = timestamp(report.get('validUntil'))
    if official_deadline is not None:
        deadline = min(deadline, official_deadline)
        if deadline <= now:
            return []
    for observation in data.get('observations') or []:
        product = observation.get('product')
        observed_time = iso(observation.get('time'))
        if product not in ('VTSE51', 'VTSE52') or observation.get('status') == 'cancelled' or not observed_time:
            continue
        observed = [tsunami_area(a) for a in observation.get('observed') or []]
        # VTSE52 groups offshore stations in areas; WebQuake expects stations.
        if product == 'VTSE52':
            observed = [station for area in observed for station in (area.get('stations') or [area])]
        messages.append(envelope(product, event_id, observed_time, target,
                                 {'tsunami': {'forecasts': body['tsunami']['forecasts'], 'observations': observed}}))
    for message in messages:
        message['source_valid_until'] = deadline
        message['wolfx_payload'] = data
    return messages


class Connection:
    """One aggregate WebSocket plus HTTP-only earthquake-detail polling.

    Cached old reports are baselined, not replayed as new alerts on startup.
    The seen set survives reconnects and is bounded to the feed retention size.
    """

    def __init__(self):
        import os
        with open(os.path.join(os.path.dirname(__file__), '..', 'public', 'japan-regions.geojson'), encoding='utf-8') as stream:
            for feature in json.load(stream)['features']:
                props = feature['properties']
                for field in ('name', 'name_ja', 'name_jp', 'nam_ja'):
                    if props.get(field):
                        REGION_CODES[area_name(props[field])] = str(props['code'])
        self._listener = None
        self._seen = {}
        self._delivery_lock = threading.RLock()
        self._stop = threading.Event()
        self._details_wake = threading.Event()
        self._last_eew = {}
        self._ws = None

    def set_message_listener(self, listener, threading=False):
        self._listener = listener

    def _deliver(self, key, revision, message, recent=True):
        with self._delivery_lock:
            if self._seen.get(key) == revision:
                return
            if recent:
                self._listener(message)
            self._seen[key] = revision
            while len(self._seen) > 2000:
                self._seen.pop(next(iter(self._seen)))

    def _on_message(self, ws, raw):
        with self._delivery_lock:
            self._handle_message(ws, raw)

    def _handle_message(self, ws, raw):
        try:
            data = json.loads(raw)
            kind = data.get('type')
            if kind == 'heartbeat':
                ws.send('ping')
            elif kind == 'jma_eew':
                message = adapt_eew(data)
                if message:
                    # Cached/manual-query responses must not roll a live event back.
                    order = (int(data.get('Serial') or 0), timestamp(data['AnnouncedTime']))
                    if order < self._last_eew.get(data['EventID'], (-1, 0)):
                        return
                    self._last_eew[data['EventID']] = order
                    if len(self._last_eew) > 100:
                        self._last_eew.pop(next(iter(self._last_eew)))
                    age = time.time() - timestamp(data['AnnouncedTime'])
                    revision = json.dumps(message, sort_keys=True)
                    self._deliver(('eew', data['EventID']), revision, message, -30 <= age <= 120)
            elif kind == 'jma_eqlist':
                revision = data.get('md5') or json.dumps(data, sort_keys=True)
                if self._seen.get('eqlist') != revision:
                    self._seen['eqlist'] = revision
                    self._details_wake.set()
            elif kind == 'jma_tsunami':
                messages = adapt_tsunami(data)
                if not messages:
                    self._listener({'type': 'source_status', 'source': 'wolfx', 'source_valid_until': 0})
                for message in messages:
                    # Trust renewals must refresh expiry without duplicate alerts.
                    key = ('tsunami', message['head']['type'])
                    revision = json.dumps({k: v for k, v in message.items() if k not in ('source_valid_until', 'wolfx_payload')}, sort_keys=True)
                    with self._delivery_lock:
                        if self._seen.get(key) == revision and message.get('source_valid_until'):
                            self._listener({**message, '_source_refresh_only': True})
                        else:
                            self._deliver(key, revision, message)
        except Exception:
            logger.exception('Wolfx message failed')

    def _on_open(self, ws):
        ws.send('query_jmaeew')
        ws.send('query_jmatsunami')
        ws.send('query_jmaeqlist')

    def _run_ws(self):
        delay = 5
        while not self._stop.is_set():
            opened = time.monotonic()
            self._ws = websocket.WebSocketApp('wss://ws-api.wolfx.jp/all_eew',
                                             on_open=self._on_open, on_message=self._on_message,
                                             on_error=lambda ws, err: logger.warning('Wolfx WebSocket: %s', err))
            self._ws.run_forever(ping_interval=30, ping_timeout=10)
            if time.monotonic() - opened > 60:
                delay = 5
            if self._stop.wait(delay):
                return
            delay = min(delay * 2, 60)

    def _poll_details(self):
        with requests.Session() as session:
            while not self._stop.is_set():
                try:
                    # Avoid the Brotli decoder path: some server installations mix
                    # distro urllib3 with a pip Brotli version it cannot stream.
                    response = session.get(
                        'https://api.wolfx.jp/jma_earthquake_detail.json',
                        headers={'Accept-Encoding': 'gzip, deflate'}, timeout=15)
                    response.raise_for_status()
                    self._handle_details(response.json())
                except Exception:
                    logger.exception('Wolfx earthquake detail poll failed')
                if not self._stop.is_set():
                    self._details_wake.wait(5)
                    self._details_wake.clear()

    def _poll_snapshots(self):
        # Recovery runs independently: a slow HTTP snapshot must not hold up
        # earthquake-detail polling or the primary WebSocket receiver.
        with requests.Session() as session:
            while not self._stop.is_set():
                for feed in ('jma_eew', 'jma_tsunami'):
                    if self._stop.is_set():
                        return
                    try:
                        response = session.get('https://api.wolfx.jp/' + feed + '.json',
                                               headers={'Accept-Encoding': 'gzip, deflate'}, timeout=15)
                        response.raise_for_status()
                        self._on_message(None, json.dumps({**response.json(), 'type': feed}))
                    except (requests.RequestException, ValueError):
                        logger.warning('Wolfx %s HTTP recovery failed', feed, exc_info=True)
                self._stop.wait(30)

    def _handle_details(self, data):
        reports = []
        for event in data.get('events') or []:
            if event.get('status') == 'cancelled':
                self._deliver(('cancelled', event.get('eventId')), json.dumps(event, sort_keys=True),
                              {'type': 'wolfx_retraction', 'event_id': event.get('eventId'), 'wolfx_payload': event})
                continue
            for report in event.get('reports') or []:
                try:
                    message = adapt_report(event, report)
                except (ValueError, TypeError, KeyError):
                    logger.warning('Ignoring malformed Wolfx product %s', report.get('id'), exc_info=True)
                    continue
                if message:
                    reports.append((event, report, message))
        reports.sort(key=lambda item: timestamp(item[1]['time']))
        for event, report, message in reports:
            age = time.time() - timestamp(report['time'])
            if age < -30:
                continue
            # Archive old available reports and recover their station maps without
            # announcing old earthquakes or rewriting receipt-time replay history.
            if age > 180:
                message['_historical'] = True
            self._deliver(('report', event['eventId'], report['product']),
                          json.dumps({k: v for k, v in message.items() if k != 'wolfx_payload'}, sort_keys=True), message)

    def start(self):
        if not self._listener:
            raise RuntimeError('No message listener set')
        threading.Thread(target=self._run_ws, daemon=True).start()
        threading.Thread(target=self._poll_details, daemon=True).start()
        threading.Thread(target=self._poll_snapshots, daemon=True).start()

    def stop(self):
        self._stop.set()
        self._details_wake.set()
        if self._ws:
            self._ws.close()
