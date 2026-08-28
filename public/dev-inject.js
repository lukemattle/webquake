// WebQuake dev injector — load via browser console:
//   fetch('/dev-inject.js').then(r=>r.text()).then(eval)
// Or temporarily add <script src="dev-inject.js"></script> to index.html.
// displayData() is already a global so no backend needed.

const _STATIONS = [
    // Hokkaido
    {lat:43.06,lon:141.35,int:'0'},{lat:43.77,lon:142.36,int:'1'},
    {lat:42.32,lon:140.98,int:'2'},{lat:44.35,lon:142.85,int:'0'},
    // Tohoku
    {lat:38.27,lon:140.87,int:'3'},{lat:39.70,lon:141.15,int:'4'},
    {lat:37.75,lon:140.47,int:'5-'},{lat:38.92,lon:141.56,int:'2'},
    // Kanto
    {lat:35.69,lon:139.69,int:'5+'},{lat:35.44,lon:139.64,int:'6-'},
    {lat:36.39,lon:139.06,int:'6+'},{lat:35.86,lon:140.23,int:'5-'},
    {lat:35.13,lon:137.02,int:'7'},
    // Chubu
    {lat:36.70,lon:137.21,int:'3'},{lat:35.66,lon:138.57,int:'4'},
    {lat:34.97,lon:138.38,int:'5-'},
    // Kansai
    {lat:34.69,lon:135.50,int:'2'},{lat:34.69,lon:135.18,int:'1'},
    {lat:35.02,lon:135.75,int:'3'},
    // Chugoku / Shikoku
    {lat:34.40,lon:132.46,int:'1'},{lat:33.56,lon:133.53,int:'2'},
    // Kyushu
    {lat:33.59,lon:130.42,int:'0'},{lat:31.56,lon:130.55,int:'1'},
    {lat:32.75,lon:129.87,int:'0'},
];

// Region codes spread across Japan, each assigned a fake intensity
const _AREA_INTENSITIES = [
    // Hokkaido
    {code:'100',max_int:'1',is_warning:false},{code:'101',max_int:'0',is_warning:false},{code:'102',max_int:'2',is_warning:false},
    // Tohoku
    {code:'210',max_int:'3',is_warning:false},{code:'220',max_int:'4',is_warning:false},{code:'230',max_int:'5-',is_warning:false},
    {code:'240',max_int:'4',is_warning:false},{code:'250',max_int:'3',is_warning:false},
    // Kanto
    {code:'310',max_int:'6-',is_warning:true},{code:'320',max_int:'5+',is_warning:true},{code:'330',max_int:'5-',is_warning:false},
    {code:'340',max_int:'6+',is_warning:true},{code:'350',max_int:'7',is_warning:true},{code:'360',max_int:'5+',is_warning:true},
    // Chubu
    {code:'430',max_int:'4',is_warning:false},{code:'440',max_int:'3',is_warning:false},{code:'450',max_int:'2',is_warning:false},
    // Kansai
    {code:'540',max_int:'3',is_warning:false},{code:'550',max_int:'2',is_warning:false},{code:'560',max_int:'1',is_warning:false},
    // Chugoku
    {code:'610',max_int:'1',is_warning:false},{code:'620',max_int:'2',is_warning:false},
    // Shikoku
    {code:'670',max_int:'2',is_warning:false},{code:'680',max_int:'1',is_warning:false},
    // Kyushu
    {code:'730',max_int:'1',is_warning:false},{code:'740',max_int:'0',is_warning:false},
];

// Coastal tsunami forecast data — Pacific coast, mixed warning levels (numeric heights)
const _TSUNAMI_REGION_CODES      = ['210',   '220',   '250',       '300',      '310',                  '380',       '400',          '610',    '760'];
const _TSUNAMI_REGIONS_EN        = ['Iwate', 'Miyagi','Fukushima', 'Ibaraki',  'Chiba Kujukuri',       'Shizuoka',  'Southern Mie', 'Kochi',  'Miyazaki'];
const _TSUNAMI_REGIONS_JP        = ['岩手県', '宮城県', '福島県',    '茨城県',   '千葉県九十九里・外房', '静岡県',    '三重県南部',   '高知県', '宮崎県'];
const _TSUNAMI_HEIGHTS           = [3,        5,        2,           1,          0.5,                    3,           10,             1,        1];
const _TSUNAMI_HEIGHT_CONDITIONS = ['',       '',       '',          '',         '',                     '',          '',             '',       ''];
// JMA kind codes: 53/52 大津波警報, 51 津波警報, 62 津波注意報, 71 津波予報.
// Iwate carries 53 (大津波警報：発表) so the "first issuance" code is exercised;
// Southern Mie carries over=true at 10m so the 10m超 band is exercised.
const _TSUNAMI_KIND_CODES        = ['53',     '52',     '51',        '51',       '62',                   '52',        '52',           '62',     '71'];
const _TSUNAMI_HEIGHT_OVER       = [false,    false,    false,       false,      false,                  false,       true,           false,    false];
const _TSUNAMI_CONDITIONS        = ['津波到達中と推測', '第１波到達を確認', '', '', '', '', '', '', ''];

// Non-numeric height scenario (巨大/高い) — like the 2016 Kumamoto scenario
const _TSUNAMI_HUGE_CODES      = ['210',  '220',  '250',       '380',       '400',          '610',    '760',    '530',    '580'];
const _TSUNAMI_HUGE_EN         = ['Iwate','Miyagi','Fukushima', 'Shizuoka',  'Southern Mie', 'Kochi',  'Miyazaki','Wakayama','Tokushima'];
const _TSUNAMI_HUGE_JP         = ['岩手県','宮城県','福島県',    '静岡県',    '三重県南部',   '高知県', '宮崎県',  '和歌山県','徳島県'];
const _TSUNAMI_HUGE_HEIGHTS    = [0,       0,       0,           0,           0,              0,        0,         0,        0];
const _TSUNAMI_HUGE_HCOND      = ['巨大',  '巨大',  '高い',      '高い',      '巨大',         '高い',   '高い',    '',       ''];
// 巨大 only accompanies a 大津波警報 (53/52); 高い accompanies a 津波警報 (51).
const _TSUNAMI_HUGE_KINDS      = ['53',    '52',    '51',        '51',        '52',           '51',     '51',      '62',     '62'];

function _nowJst() {
    return Math.floor(Date.now() / 1000) - 9 * 3600;
}

function _jstNow() {
    return new Date().toLocaleTimeString('ja-JP', {timeZone:'Asia/Tokyo', hour12:false});
}

function _eewBase(extra) {
    const quakeTime  = _nowJst() - 30;
    const originTime = quakeTime - 5;
    return Object.assign({
        type: 'earthquake',
        event_id: 'DEV' + Date.now(),
        last_report: false,
        report_num: 3,
        quake_time:  quakeTime,
        origin_time: originTime,
        report_time: _nowJst(),
        area_intensities: _AREA_INTENSITIES,
        jst_time: _jstNow(),
    }, extra);
}

// ── Public test functions ─────────────────────────────────────────────

window.testNied = function() {
    displayData({type:'nied_stations', stations:_STATIONS});
    console.log('[test] NIED stations injected');
};

const _TW_STATIONS = [
    {code:'tw-taipei',   lat:25.04, lon:121.56, int:'4',  raw:4.0},
    {code:'tw-keelung',  lat:25.13, lon:121.74, int:'3',  raw:3.0},
    {code:'tw-taoyuan',  lat:24.99, lon:121.30, int:'2',  raw:2.0},
    {code:'tw-hsinchu',  lat:24.80, lon:120.97, int:'1',  raw:1.0},
    {code:'tw-taichung', lat:24.15, lon:120.68, int:'5-', raw:5.0},
    {code:'tw-changhua', lat:24.05, lon:120.54, int:'3',  raw:3.2},
    {code:'tw-chiayi',   lat:23.48, lon:120.45, int:'2',  raw:2.1},
    {code:'tw-tainan',   lat:22.99, lon:120.21, int:'1',  raw:0.8},
    {code:'tw-kaohsiung',lat:22.63, lon:120.30, int:'0',  raw:-0.5},
    {code:'tw-pingtung', lat:22.55, lon:120.55, int:'0',  raw:-1.2},
    {code:'tw-hualien',  lat:23.98, lon:121.60, int:'6-', raw:6.2},
    {code:'tw-taitung',  lat:22.76, lon:121.14, int:'4',  raw:4.4},
    {code:'tw-yilan',    lat:24.76, lon:121.75, int:'2',  raw:2.3},
    {code:'tw-penghu',   lat:23.57, lon:119.58, int:'0',  raw:-2.0},
    {code:'tw-lanyu',    lat:22.05, lon:121.55, int:'1',  raw:0.5},
];

window.testTwStations = function() {
    displayData({type:'exptech_stations', stations:_TW_STATIONS});
    console.log('[test] Taiwan/ExpTech stations injected');
};

// CWA-style post-event report — mirrors the shape poll_cwa_reports() will
// eventually broadcast (earthquake_no, lat/lon, depth, magnitude, location,
// per-station stations[]). Station coords are spread across a few counties
// so computeTwRegionIntensitiesFromStations has something to bucket.
window.testTwQuake = function() {
    displayData({
        type: 'tw_earthquake',
        earthquake_no: 115999,
        origin_time: new Date().toISOString(),
        lat: 23.7,
        lon: 121.6,
        depth: 15,
        magnitude: 5.8,
        location: 'Hualien County',
        location_zh: '花蓮縣',
        stations: [
            {code:'tw-hualien1', lat:23.98, lon:121.60, int:'6-'},
            {code:'tw-hualien2', lat:23.75, lon:121.55, int:'5+'},
            {code:'tw-yilan1',   lat:24.76, lon:121.75, int:'4'},
            {code:'tw-taitung1', lat:22.76, lon:121.14, int:'4'},
            {code:'tw-nantou1',  lat:23.90, lon:120.98, int:'3'},
            {code:'tw-taipei1',  lat:25.04, lon:121.56, int:'2'},
        ],
    });
    console.log('[test] Taiwan quake injected');
};

window.testTwQuakeClear = function() {
    displayData({type: 'tw_past_quake_clear'});
    console.log('[test] Taiwan quake cleared');
};

// Earthquake History sidebar — Taiwan entries. Mirrors what poll_cwa_reports()
// broadcasts (earthquake_no/lat/lon/depth/magnitude/location/ts/max_int/web),
// spread across a few timestamps so they interleave with live JMA history
// entries when testing the Both/JP/TW sidebar filter buttons.
window.testTwHistory = function() {
    const now = Math.floor(Date.now() / 1000);
    displayData({
        type: 'tw_history',
        quakes: [
            {earthquake_no: 115999, lat: 23.7, lon: 121.6, depth: 15, magnitude: 5.8,
             location: '15.0 km ENE of Hualien County', location_zh: '花蓮縣政府東北東方 15.0 公里', ts: now - 300, max_int: '6-',
             web: 'https://scweb.cwa.gov.tw/zh-tw/earthquake/details/2026999'},
            {earthquake_no: 115990, lat: 22.9, lon: 121.2, depth: 30, magnitude: 4.2,
             location: '32.1 km SE of Taitung County', location_zh: '臺東縣政府東南方 32.1 公里', ts: now - 7200, max_int: '3',
             web: 'https://scweb.cwa.gov.tw/zh-tw/earthquake/details/2026990'},
            {earthquake_no: 115980, lat: 24.1, lon: 121.7, depth: 60, magnitude: 3.5,
             location: '5.2 km N of Yilan County', location_zh: '宜蘭縣政府北方 5.2 公里', ts: now - 86400, max_int: null,
             web: null},
        ],
    });
    console.log('[test] Taiwan quake history injected');
};

// Live Taiwan EEW — field shape (author/serial/eq.{time,loc,lat,lon,mag,depth,max})
// confirmed from ExpTech's own live production trem.js, not guessed. eq.time is
// epoch milliseconds (unlike JMA's unix-seconds convention).
window.testTwEew = function(cwa = true) {
    displayData({
        type: 'tw_eew',
        id: 'DEVTW' + Date.now(),
        author: cwa ? 'cwa' : 'trem',
        serial: 2,
        eq: {
            time: Date.now() - 8000,
            loc: 'Hualien County',
            lat: 23.98,
            lon: 121.60,
            mag: 6.2,
            depth: 12,
            max: 7, // 6-
        },
    });
    console.log('[test] Taiwan EEW injected —', cwa ? 'CWA' : 'non-CWA author');
};

window.testTwEewClear = function() {
    clearTwEewDisplay();
    console.log('[test] Taiwan EEW cleared');
};


// lpgm: 長周期地震動階級 1–4, or 0/null for the (usual) "no long-period motion" case.
window.testEew = function(warning = false, lpgm = 0) {
    displayData(_eewBase({
        is_plum: false,
        warning,
        max_int: warning ? '6-' : '4',
        max_lpgm: lpgm,
        epi_location_en: ['Off the coast of Ibaraki'],
        epi_location_jp: ['茨城県沖'],
        magnitude: 6.8,
        depth: 40,
        lat: 36.4,
        lon: 140.7,
        tsunami_possible: warning,
    }));
    console.log('[test] EEW injected —', warning ? 'Warning' : 'Forecast', lpgm ? `(LPGM class ${lpgm})` : '');
};

window.testPlum = function() {
    displayData(_eewBase({
        is_plum: true,
        warning: true,
        max_int: '5+',
        epi_location_en: ['Hypothetical source (PLUM)'],
        epi_location_jp: ['仮定震源要素（PLUM法）'],
        magnitude: null,
        depth: null,
        lat: null,
        lon: null,
        tsunami_possible: false,
    }));
    console.log('[test] PLUM EEW injected');
};

window.testPastQuake = function() {
    const quakeTime = _nowJst() - 120;
    displayData({
        type: 'earthquake',
        event_id: 'PAST' + Date.now(),
        quake_time: quakeTime,
        report_time: _nowJst(),
        max_int: '5-',
        magnitude: 5.4,
        depth: 60,
        lat: 35.7,
        lon: 139.8,
        epi_location_en: ['Northern Chiba'],
        epi_location_jp: ['千葉県北部'],
        area_intensities: _AREA_INTENSITIES,
        jst_time: _jstNow(),
    });
    console.log('[test] Past quake injected');
};

// VTSE41-style: forecast only, numeric heights
window.testTsunami = function() {
    displayData({
        type: 'tsunami',
        quake_time:  _nowJst() - 60,
        report_time: _nowJst(),
        region_codes:      _TSUNAMI_REGION_CODES,
        regions_en:        _TSUNAMI_REGIONS_EN,
        regions_jp:        _TSUNAMI_REGIONS_JP,
        heights:           _TSUNAMI_HEIGHTS,
        height_conditions: _TSUNAMI_HEIGHT_CONDITIONS,
        height_over:       _TSUNAMI_HEIGHT_OVER,
        kind_codes:        _TSUNAMI_KIND_CODES,
        conditions:        _TSUNAMI_CONDITIONS,
        obs_regions_en:        [],
        obs_regions_jp:        [],
        obs_heights:           [],
        obs_height_conditions: [],
        warning_level: 'Major Warning',
    });
    console.log('[test] Tsunami injected — Major Warning numeric (VTSE41-style)');
};

// VTSE41-style: non-numeric heights (巨大/高い) — large event, no metre estimates yet
window.testTsunamiHuge = function() {
    displayData({
        type: 'tsunami',
        quake_time:  _nowJst() - 60,
        report_time: _nowJst(),
        region_codes:      _TSUNAMI_HUGE_CODES,
        regions_en:        _TSUNAMI_HUGE_EN,
        regions_jp:        _TSUNAMI_HUGE_JP,
        heights:           _TSUNAMI_HUGE_HEIGHTS,
        height_conditions: _TSUNAMI_HUGE_HCOND,
        kind_codes:        _TSUNAMI_HUGE_KINDS,
        conditions:        _TSUNAMI_HUGE_CODES.map(() => ''),
        obs_regions_en:        [],
        obs_regions_jp:        [],
        obs_heights:           [],
        obs_height_conditions: [],
        warning_level: 'Major Warning',
    });
    console.log('[test] Tsunami injected — Major Warning 巨大/高い (VTSE41-style)');
};

// VTSE51-style: forecast + coastal observations
window.testTsunamiWithObs = function() {
    displayData({
        type: 'tsunami',
        quake_time:  _nowJst() - 120,
        report_time: _nowJst(),
        region_codes:      _TSUNAMI_REGION_CODES,
        regions_en:        _TSUNAMI_REGIONS_EN,
        regions_jp:        _TSUNAMI_REGIONS_JP,
        heights:           _TSUNAMI_HEIGHTS,
        height_conditions: _TSUNAMI_HEIGHT_CONDITIONS,
        height_over:       _TSUNAMI_HEIGHT_OVER,
        kind_codes:        _TSUNAMI_KIND_CODES,
        conditions:        _TSUNAMI_CONDITIONS,
        obs_regions_en:        ['Miyagi', 'Fukushima', 'Ibaraki'],
        obs_regions_jp:        ['宮城県', '福島県', '茨城県'],
        obs_heights:           [4.2, 2.8, 1.1],
        obs_height_conditions: ['', '', ''],
        warning_level: 'Major Warning',
    });
    console.log('[test] Tsunami injected — Major Warning + coastal observations (VTSE51-style)');
};

// VTSE52-style: offshore GPS/pressure buoy observations only
window.testTsunamiObs = function() {
    displayData({
        type: 'tsunami_obs',
        report_time: _nowJst(),
        obs_station_names:    ['静岡御前崎沖',            '三重尾鷲沖',       '和歌山白浜沖',              '高知沖100kmA',         '高知足摺岬沖',               '日南沖'],
        obs_station_names_en: ['Off Omaezaki, Shizuoka', 'Off Owase, Mie', 'Off Shirahama, Wakayama', '100km off Kochi A', 'Off Ashizurimisaki, Kochi', 'Off Nichinan, Miyazaki'],
        obs_codes:            ['38090',                   '40090',           '53090',                    '61050',              '61090',                       '76090'],
        obs_heights:          [1.8,                        2.0,               1.2,                        0.8,                  1.7,                           0.5],
        obs_conditions:       ['第１波到達を確認', '第１波到達を確認', '第１波到達を確認', '第１波到達を確認', '第１波到達を確認', '第１波到達を確認'],
        // lat/lon/radius come from eng_codes.offshore_station_coords (surveyed buoy positions, except 76090 which is a geocoded estimate)
        obs_lats:           [34.4033, 33.9022, 33.6422, 33.0792, 32.6311, 31.55],
        obs_lons:           [138.2750, 136.2594, 135.1567, 134.1864, 133.1558, 131.85],
        obs_radii_km:       [5, 5, 5, 8, 5, 25],
        obs_loc_estimated:  [false, false, false, false, false, true],
    });
    console.log('[test] Offshore tsunami obs injected (VTSE52-style)');
};

window.testTsunamiClear = function() {
    displayData({type: 'tsunami_clear'});
    console.log('[test] Tsunami cleared');
};

window.testClock = function() {
    const id = setInterval(() => displayData({jst_time:_jstNow()}), 1000);
    console.log('[test] Fake clock started (id=' + id + ', stop with clearInterval(' + id + '))');
    return id;
};

window.testAll = function() {
    testNied();
    setTimeout(() => testEew(true), 150);
    setTimeout(testTsunami, 300);
    setTimeout(testTsunamiObs, 450);
    const clockId = testClock();
    console.log('[test] All data injected. Clock id:', clockId);
};

console.log(
    '%c WebQuake dev injector ready ',
    'background:#1a1a1a;color:#8f8;padding:4px 8px;border-radius:3px'
);
console.log('testAll() | testNied() | testTwStations() | testTwQuake() | testTwQuakeClear() | testTwHistory() | testTwEew() | testTwEew(false) | testTwEewClear() | testEew() | testEew(true) | testEew(true, 3) | testPlum() | testPastQuake() | testTsunami() | testTsunamiHuge() | testTsunamiWithObs() | testTsunamiObs() | testTsunamiClear() | testClock()');
