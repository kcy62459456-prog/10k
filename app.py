
import os
import math
import datetime as dt
from dataclasses import dataclass

import requests
import streamlit as st
from zoneinfo import ZoneInfo
from timezonefinder import TimezoneFinder
import swisseph as swe
from textwrap import dedent

# ---------- Constants ----------
STEMS = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
BRANCHES = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]
# ---------- UI/명리 보조 데이터 ----------
# 오행 색상 (요청: 수=검정) — 타일 배경색(채움) 기준
# 목=초록, 화=빨강, 토=노랑, 금=흰색(테두리/글자), 수=검정
ELEM_FILL = ["#34C759", "#FF3B30", "#FFCC00", "#F2F2F2", "#000000"]
ELEM_TEXT = ["#FFFFFF", "#FFFFFF", "#FFFFFF", "#111111", "#FFFFFF"]

# 지장간(藏干) — 지지 아래 표기용(대표적 배치)
HIDDEN_STEMS = {
    # 사용자 지정: 초기(여기) → 중기 → 정기(본기) 순서
    "子": ["壬","癸"],
    "丑": ["癸","辛","己"],
    "寅": ["戊","丙","甲"],
    "卯": ["甲","乙"],
    "辰": ["乙","癸","戊"],
    "巳": ["戊","庚","丙"],
    "午": ["丙","己","丁"],
    "未": ["丁","乙","己"],
    "申": ["戊","壬","庚"],
    "酉": ["庚","辛"],
    "戌": ["辛","丁","戊"],
    "亥": ["戊","甲","壬"],
}


def fmt_age_year_month(age_years: float) -> str:
    """예: 8.33 -> '8년 4개월' (개월은 반올림하지 않고 내림으로 표시)"""
    if age_years is None:
        return ""
    y = int(math.floor(age_years))
    m = int(math.floor((age_years - y) * 12.0 + 1e-9))
    return f"{y}년 {m}개월"

# ---------- Advanced (오행/십신/십이운성) ----------
# 오행 (0:목, 1:화, 2:토, 3:금, 4:수)
STEM_ELEMENTS = [0, 0, 1, 1, 2, 2, 3, 3, 4, 4]  # 甲乙(목), 丙丁(화), 戊己(토), 庚辛(금), 壬癸(수)
BRANCH_ELEMENTS = [4, 2, 0, 0, 2, 1, 1, 2, 3, 3, 2, 4]  # 子(수), 丑(토), 寅(목), 卯(목), 辰(토), 巳(화), 午(화), 未(토), 申(금), 酉(금), 戌(토), 亥(수)

ELEM_NAMES = ["목", "화", "토", "금", "수"]
ELEM_COLORS = ["#2266CC", "#DD4444", "#DDAA00", "#777777", "#222222"]

# 십신 명칭 (관계 0~4: 비겁/식상/재성/관성/인성, [음양같음, 음양다름])
SIPSIN_NAMES = {
    0: ["비견", "겁재"],
    1: ["식신", "상관"],
    2: ["편재", "정재"],
    3: ["편관", "정관"],
    4: ["편인", "정인"],
}

# 십이운성 순서 (절, 태, 양, 장생, 목욕, 관대, 건록, 제왕, 쇠, 병, 사, 묘)
UNSEONG_ORDER = ["절","태","양","장생","목욕","관대","건록","제왕","쇠","병","사","묘"]

def get_element_idx(char: str) -> int:
    if char in STEMS:
        return STEM_ELEMENTS[STEMS.index(char)]
    if char in BRANCHES:
        return BRANCH_ELEMENTS[BRANCHES.index(char)]
    return 0

def get_polarity(char: str) -> int:
    """0: 양, 1: 음 (간지의 기본 홀짝 규칙)"""
    if char in STEMS:
        return STEMS.index(char) % 2
    if char in BRANCHES:
        return BRANCHES.index(char) % 2
    return 0

def get_sipsin(day_stem: str, target: str) -> str:
    """일간(day_stem) 기준 target(천간/지지)의 십신"""
    d_elem = get_element_idx(day_stem)
    t_elem = get_element_idx(target)
    relation = (t_elem - d_elem) % 5  # 0~4

    d_pol = get_polarity(day_stem)
    t_pol = get_polarity(target)

    # 지지의 체/용 논쟁은 많으나, 여기서는 기본 홀짝 규칙을 유지합니다.
    is_diff = 1 if d_pol != t_pol else 0
    return SIPSIN_NAMES[relation][is_diff]

def get_12unseong(stem: str, branch: str) -> str:
    """해당 천간(stem)의 장생 위치를 기준으로 지지(branch)의 십이운성"""
    start_map = {
        "甲": ("亥", 1), "丙": ("寅", 1), "戊": ("寅", 1), "庚": ("巳", 1), "壬": ("申", 1),
        "乙": ("午", -1), "丁": ("酉", -1), "己": ("酉", -1), "辛": ("子", -1), "癸": ("卯", -1),
    }
    start_branch, direction = start_map[stem]
    start_idx = BRANCHES.index(start_branch)
    target_idx = BRANCHES.index(branch)

    if direction == 1:
        diff = (target_idx - start_idx) % 12
    else:
        diff = (start_idx - target_idx) % 12

    final_idx = (3 + diff) % 12  # 장생이 index=3
    return UNSEONG_ORDER[final_idx]

def sexagenary_for_year(year: int) -> tuple[str, str, str]:
    """서기 year의 연간지(절기 기준과 무관한 단순 연간지; 세운 표시에 사용)"""
    idx60 = (year - 1984) % 60  # 1984=甲子
    s = STEMS[idx60 % 10]
    b = BRANCHES[idx60 % 12]
    return s, b, f"{s}{b}"


# 12 "절"(節) that start the BaZi solar months (월주 기준 절입)
MAJOR_TERMS = [
    ("입춘(立春)", 315.0, "寅"),
    ("경칩(驚蟄)", 345.0, "卯"),
    ("청명(清明)", 15.0,  "辰"),
    ("입하(立夏)", 45.0,  "巳"),
    ("망종(芒種)", 75.0,  "午"),
    ("소서(小暑)", 105.0, "未"),
    ("입추(立秋)", 135.0, "申"),
    ("백로(白露)", 165.0, "酉"),
    ("한로(寒露)", 195.0, "戌"),
    ("입동(立冬)", 225.0, "亥"),
    ("대설(大雪)", 255.0, "子"),
    ("소한(小寒)", 285.0, "丑"),
]


# 24 solar terms (태양 황경 15° 경계). Used for Luck Pillar starting age (기운).
SOLAR_TERMS_24 = [
    ("입춘(立春)", 315.0),
    ("우수(雨水)", 330.0),
    ("경칩(驚蟄)", 345.0),
    ("춘분(春分)", 0.0),
    ("청명(清明)", 15.0),
    ("곡우(穀雨)", 30.0),
    ("입하(立夏)", 45.0),
    ("소만(小滿)", 60.0),
    ("망종(芒種)", 75.0),
    ("하지(夏至)", 90.0),
    ("소서(小暑)", 105.0),
    ("대서(大暑)", 120.0),
    ("입추(立秋)", 135.0),
    ("처서(處暑)", 150.0),
    ("백로(白露)", 165.0),
    ("추분(秋分)", 180.0),
    ("한로(寒露)", 195.0),
    ("상강(霜降)", 210.0),
    ("입동(立冬)", 225.0),
    ("소설(小雪)", 240.0),
    ("대설(大雪)", 255.0),
    ("동지(冬至)", 270.0),
    ("소한(小寒)", 285.0),
    ("대한(大寒)", 300.0),
]

# Year stem -> month stem of 寅 month (입춘~경칩)
Y_STEM_TO_YIN_MONTH_STEM = {
    "甲": "丙", "己": "丙",
    "乙": "戊", "庚": "戊",
    "丙": "庚", "辛": "庚",
    "丁": "壬", "壬": "壬",
    "戊": "甲", "癸": "甲",
}

# Day stem -> 子 hour stem
D_STEM_TO_ZI_HOUR_STEM = {
    "甲": "甲", "己": "甲",
    "乙": "丙", "庚": "丙",
    "丙": "戊", "辛": "戊",
    "丁": "庚", "壬": "庚",
    "戊": "壬", "癸": "壬",
}

TF = TimezoneFinder()
def draw_pillar_card(title: str, stem: str, branch: str, day_stem: str):
    s_elem = get_element_idx(stem)
    b_elem = get_element_idx(branch)
    s_color = ELEM_COLORS[s_elem]
    b_color = ELEM_COLORS[b_elem]

    s_sipsin = "본원" if title.startswith("일주") else get_sipsin(day_stem, stem)
    b_sipsin = get_sipsin(day_stem, branch)
    unseong = get_12unseong(stem, branch)

    html = f"""
    <div class="card">
      <div class="card-title">{title}</div>
      <div class="kv">천간 십신: <b>{s_sipsin}</b> / 지지 십신: <b>{b_sipsin}</b></div>
      <hr class="soft" />
      <div class="big"><span style="color:{s_color}">{stem}</span><span style="opacity:0.5"> </span><span style="color:{b_color}">{branch}</span></div>
      <div class="small">십이운성: <b>{unseong}</b></div>
    </div>
    """
    st.markdown(dedent(html), unsafe_allow_html=True)


# ---------- Helpers ----------
def parse_hms(s: str) -> dt.time:
    s = (s or "").strip()
    parts = s.split(":")
    if len(parts) not in (2, 3):
        raise ValueError("시간 형식은 HH:MM 또는 HH:MM:SS 여야 합니다.")
    try:
        hh = int(parts[0])
        mm = int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
    except Exception:
        raise ValueError("시간은 숫자로 입력해 주세요. (예: 09:30 또는 09:30:00)")
    if not (0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
        raise ValueError("시간 값 범위가 올바르지 않습니다.")
    return dt.time(hh, mm, ss)

def jd_ut_from_utc(dt_utc: dt.datetime) -> float:
    # swisseph.julday takes decimal hour in UT
    hour = dt_utc.hour + dt_utc.minute/60 + dt_utc.second/3600 + dt_utc.microsecond/3.6e9
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day, hour, swe.GREG_CAL)

def utc_from_jd_ut(jd_ut: float) -> dt.datetime:
    y, m, d, hour = swe.revjul(jd_ut, swe.GREG_CAL)
    hh = int(hour)
    minute = int((hour - hh) * 60)
    sec = int(round((((hour - hh) * 60) - minute) * 60))
    # normalize
    if sec == 60:
        sec = 0
        minute += 1
    if minute == 60:
        minute = 0
        hh += 1
    # hour may become 24
    base = dt.datetime(y, m, d, 0, 0, 0, tzinfo=dt.timezone.utc)
    return base + dt.timedelta(hours=hh, minutes=minute, seconds=sec)

def get_timezone_name(lat: float, lon: float) -> str | None:
    return TF.timezone_at(lat=lat, lng=lon)

def geocode(place: str, limit: int = 5):
    """
    Geocoding with minimal user setup.

    Priority:
    1) Geoapify (if GEOAPIFY_KEY is set)
    2) Open-Meteo geocoding (no key)
    3) Nominatim (OSM) as last resort

    Returns list of {label, lat, lon, country, city}.
    """
    place = place.strip()
    if not place:
        return []

    key = os.environ.get("GEOAPIFY_KEY", "").strip()
    results = []

    # Small retry helper (network can be flaky)
    def _get(url, *, params=None, headers=None, timeout=20, tries=3):
        last = None
        for i in range(tries):
            try:
                return requests.get(url, params=params, headers=headers, timeout=timeout)
            except Exception as e:
                last = e
        raise last

    # 1) Geoapify (key-based, best quality)
    if key:
        url = "https://api.geoapify.com/v1/geocode/search"
        params = {"text": place, "limit": limit, "format": "json", "apiKey": key}
        r = _get(url, params=params, timeout=20, tries=2)
        r.raise_for_status()
        data = r.json()
        for feat in data.get("results", []):
            results.append({
                "label": feat.get("formatted") or feat.get("name") or place,
                "lat": float(feat["lat"]),
                "lon": float(feat["lon"]),
                "country": feat.get("country"),
                "city": feat.get("city") or feat.get("state"),
            })
        return results

    # 2) Open-Meteo geocoding (no key, usually reliable)
    try:
        url = "https://geocoding-api.open-meteo.com/v1/search"
        params = {"name": place, "count": limit, "language": "en", "format": "json"}
        r = _get(url, params=params, timeout=20, tries=3)
        r.raise_for_status()
        data = r.json()
        for feat in (data.get("results") or []):
            label_parts = [feat.get("name"), feat.get("admin1"), feat.get("country")]
            label = ", ".join([p for p in label_parts if p])
            results.append({
                "label": label or place,
                "lat": float(feat["latitude"]),
                "lon": float(feat["longitude"]),
                "country": feat.get("country"),
                "city": feat.get("name"),
            })
        if results:
            return results
    except Exception:
        # fall through to Nominatim
        pass

    # 3) Nominatim fallback (may timeout / rate-limit on some networks)
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "manseryeok-prototype/0.1"}
    params = {"q": place, "format": "json", "limit": str(limit)}
    r = _get(url, params=params, headers=headers, timeout=25, tries=3)
    r.raise_for_status()
    data = r.json()
    for feat in data:
        results.append({
            "label": feat.get("display_name", place),
            "lat": float(feat["lat"]),
            "lon": float(feat["lon"]),
            "country": None,
            "city": None,
        })
    return results

    # Nominatim fallback
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "manseryeok-prototype/0.1 (contact: example@example.com)"}
    params = {"q": place, "format": "json", "limit": str(limit)}
    r = requests.get(url, params=params, headers=headers, timeout=10)
    r.raise_for_status()
    data = r.json()
    for feat in data:
        results.append({
            "label": feat.get("display_name", place),
            "lat": float(feat["lat"]),
            "lon": float(feat["lon"]),
            "country": None,
            "city": None,
        })
    return results

def apparent_solar_datetime(utc_dt: dt.datetime, lon_deg: float) -> tuple[dt.datetime, float]:
    """
    Compute Local Apparent Time (LAT, 진태양시) at given longitude, using:
    LMT = UTC + lon*240s
    EoT = LAT - LMT (equation of time), from swe.time_equ(jd_ut) [days]
    LAT = UTC + lon*240s + EoT*86400s
    Returns (lat_dt_as_utc_tzaware, eot_minutes)
    """
    jd_ut = jd_ut_from_utc(utc_dt)
    eot_days = swe.time_equ(jd_ut)  # days
    eot_sec = eot_days * 86400.0
    lon_sec = lon_deg * 240.0  # 1 degree = 4 minutes = 240 seconds
    lat_dt = utc_dt + dt.timedelta(seconds=(lon_sec + eot_sec))
    return lat_dt, eot_days * 24 * 60



def is_yang_stem(stem: str) -> bool:
    # 甲丙戊庚壬 = 양, 乙丁己辛癸 = 음
    return stem in ("甲","丙","戊","庚","壬")

def luck_direction(year_stem: str, gender: str) -> str:
    """
    Return '순행' or '역행' using a common rule:
      - 양년(甲丙戊庚壬) 남 / 음년(乙丁己辛癸) 여 => 순행
      - 음년 남 / 양년 여 => 역행
    gender: '남' or '여'
    """
    yang = is_yang_stem(year_stem)
    if (gender == "남" and yang) or (gender == "여" and (not yang)):
        return "순행"
    return "역행"

def adjacent_solar_term(jd_ut_birth: float, forward: bool) -> tuple[str, float, float]:
    """
    Find the next/previous solar-term crossing (15° boundaries) around birth, in UT.
    Returns (term_name, term_lon, term_jd_ut).
    """
    if forward:
        best = None
        for name, lon in SOLAR_TERMS_24:
            jx = swe.solcross_ut(lon, jd_ut_birth, swe.FLG_SWIEPH)
            if jx > jd_ut_birth:
                if (best is None) or (jx < best[2]):
                    best = (name, lon, jx)
        if best is None:
            name, lon = SOLAR_TERMS_24[0]
            best = (name, lon, swe.solcross_ut(lon, jd_ut_birth + 1.0, swe.FLG_SWIEPH))
        return best
    else:
        best = None
        for name, lon in SOLAR_TERMS_24:
            jx = swe.solcross_ut(lon, jd_ut_birth - 40.0, swe.FLG_SWIEPH)
            if jx <= jd_ut_birth:
                if (best is None) or (jx > best[2]):
                    best = (name, lon, jx)
        if best is None:
            name, lon = SOLAR_TERMS_24[-1]
            best = (name, lon, swe.solcross_ut(lon, jd_ut_birth - 80.0, swe.FLG_SWIEPH))
        return best

def minutes_to_luck_ymd(total_minutes: float) -> tuple[int, int, int]:
    """
    Traditional conversion commonly used in BaZi luck pillar starting age:
      3 days = 1 year  -> 4320 minutes = 1 year
      1 day  = 4 months -> 360 minutes = 1 month
      1 hour = 5 days  -> 12 minutes  = 1 day (luck-days)
    Returns (years, months, days).
    """
    if total_minutes < 0:
        total_minutes = -total_minutes

    years = int(total_minutes // 4320)
    rem = total_minutes - years * 4320

    months = int(rem // 360)
    rem = rem - months * 360

    days = int(round(rem / 12.0))

    # normalize
    if days >= 30:
        months += days // 30
        days = days % 30
    if months >= 12:
        years += months // 12
        months = months % 12
    return years, months, days

def build_luck_pillars(month_stem: str, month_branch: str, direction: str, count: int = 10):
    """
    Generate 10-year Luck Pillars (대운) from the month pillar.
    By convention, the first luck pillar starts from the *next* (or previous) stem-branch after the month pillar.
    """
    step = 1 if direction == "순행" else -1
    si = STEMS.index(month_stem)
    bi = BRANCHES.index(month_branch)
    pillars = []
    for i in range(1, count + 1):
        s = STEMS[(si + step * i) % 10]
        b = BRANCHES[(bi + step * i) % 12]
        pillars.append((s, b, f"{s}{b}"))
    return pillars

def early_zi_shift(date_dt: dt.datetime) -> dt.date:
    """
    Apply early Zi (자시=23:00부터 다음 날) rule to a datetime already expressed in the clock you want to use.
    """
    if date_dt.time() >= dt.time(23, 0, 0):
        return (date_dt.date() + dt.timedelta(days=1))
    return date_dt.date()

def sexagenary_from_jdn(jdn: int) -> tuple[str, str]:
    """
    Using the widely-cited formula: (JDN + 49) mod 60, where 0 => 甲子.
    """
    idx = (jdn + 49) % 60
    stem = STEMS[idx % 10]
    branch = BRANCHES[(idx % 12)]  # 0=子 ... 11=亥
    return stem, branch

def jdn_from_gregorian_date(y: int, m: int, d: int) -> int:
    jd0 = swe.julday(y, m, d, 0.0, swe.GREG_CAL)
    return int(math.floor(jd0 + 0.5))

def year_pillar(jd_ut_birth: float) -> tuple[str, str, int, float]:
    """
    Year pillar changes at LiChun (315°). Compare in UT.
    Returns (stem, branch, pillar_year, lichun_jd_ut)
    """
    birth_utc = utc_from_jd_ut(jd_ut_birth)
    y = birth_utc.year
    jd_start = swe.julday(y, 1, 1, 0.0, swe.GREG_CAL) - 5.0
    lichun = swe.solcross_ut(315.0, jd_start, swe.FLG_SWIEPH)
    if jd_ut_birth < lichun:
        y -= 1
        jd_start = swe.julday(y, 1, 1, 0.0, swe.GREG_CAL) - 5.0
        lichun = swe.solcross_ut(315.0, jd_start, swe.FLG_SWIEPH)
    idx = (y - 1984) % 60  # 1984 = 甲子
    return STEMS[idx % 10], BRANCHES[idx % 12], y, lichun

def month_pillar(jd_ut_birth: float, year_stem: str) -> tuple[str, str, str, float]:
    """
    Month pillar changes at the 12 major terms (절입) listed in MAJOR_TERMS.
    Returns (stem, branch, term_name, term_jd_ut)
    """
    best = None
    for name, lon, branch in MAJOR_TERMS:
        jx = swe.solcross_ut(lon, jd_ut_birth - 40.0, swe.FLG_SWIEPH)
        if jx <= jd_ut_birth:
            if (best is None) or (jx > best[3]):
                best = (name, lon, branch, jx)
    if best is None:
        # should never happen, but guard anyway
        best = (MAJOR_TERMS[0][0], MAJOR_TERMS[0][1], MAJOR_TERMS[0][2], swe.solcross_ut(MAJOR_TERMS[0][1], jd_ut_birth - 80.0, swe.FLG_SWIEPH))
    term_name, lon, m_branch, term_jd = best

    # month branch index where 寅 month = 0
    order = ["寅","卯","辰","巳","午","未","申","酉","戌","亥","子","丑"]
    m_idx = order.index(m_branch)

    yin_month_stem = Y_STEM_TO_YIN_MONTH_STEM[year_stem]
    base = STEMS.index(yin_month_stem)
    m_stem = STEMS[(base + m_idx) % 10]
    return m_stem, m_branch, term_name, term_jd

def day_pillar(lat_dt: dt.datetime, use_early_zi: bool = True) -> tuple[str, str, dt.date, int]:
    """
    Day pillar is computed from the *local apparent* date (LAT) by default,
    then optionally shifted by early Zi (23:00 boundary).
    """
    day_date = early_zi_shift(lat_dt) if use_early_zi else lat_dt.date()
    jdn = jdn_from_gregorian_date(day_date.year, day_date.month, day_date.day)
    s, b = sexagenary_from_jdn(jdn)
    return s, b, day_date, jdn

def hour_pillar(lat_dt: dt.datetime, day_stem: str) -> tuple[str, str, str]:
    """
    Hour branch based on LAT.
    Hour stem determined from day stem.
    """
    minutes = lat_dt.hour * 60 + lat_dt.minute + lat_dt.second / 60.0
    h_branch_idx = int(((minutes + 60) // 120) % 12)  # 0=子
    h_branch = BRANCHES[h_branch_idx]

    zi_stem = D_STEM_TO_ZI_HOUR_STEM[day_stem]
    base = STEMS.index(zi_stem)
    h_stem = STEMS[(base + h_branch_idx) % 10]
    return h_stem, h_branch, f"{h_stem}{h_branch}"

# ---------- Streamlit UI ----------
st.set_page_config(page_title="만세력 변환 명식 찾기", layout="wide")

st.markdown(
    """
<style>
/* --- Pillar tiles (mobile manseoryeok-ish) --- */
.pillars-wrap{display:flex; justify-content:center; gap:18px; margin-top:10px; margin-bottom:12px;}
.pillar-col{width:120px; text-align:center;}
.pillar-toplabel{font-size:14px; opacity:0.65; margin-bottom:4px;}
.pillar-sipsin{font-size:14px; font-weight:700; margin-bottom:8px;}
.pillarcol{width:120px; text-align:center;}
.pillarlabel{font-size:14px; opacity:0.65; margin-bottom:4px;}
.sipsin-top{font-size:14px; font-weight:700; margin-bottom:8px;}
.hiddenstems{font-size:13px; opacity:0.8; margin-top:8px; letter-spacing:1px;}
.sipsin-bot{font-size:13px; opacity:0.85; margin-top:4px;}

.tile{width:86px; height:86px; border-radius:18px; display:flex; align-items:center; justify-content:center;
      margin:0 auto; border:1px solid rgba(255,255,255,0.10); background: rgba(255,255,255,0.02);}
.tile.char{font-size:52px; font-weight:900; line-height:1;}
.tile.branch{margin-top:10px; font-size:50px;}
.hidden{font-size:14px; opacity:0.75; margin-top:8px; letter-spacing:1px;}
.subinfo{font-size:14px; margin-top:6px; opacity:0.8;}
.subinfo b{opacity:1.0;}
/* --- Luck rows --- */
.scrollrow{display:flex; gap:10px; overflow-x:auto; padding:10px 6px; border-radius:14px;
           border:1px solid rgba(255,255,255,0.10); background: rgba(255,255,255,0.03);}
.scrollcell{min-width:92px; border-radius:16px; padding:10px 10px; text-align:center;
            border:1px solid rgba(255,255,255,0.10); background: rgba(255,255,255,0.04);}
.scrollcell .top{font-size:12px; opacity:0.75; margin-bottom:6px;}
.scrollcell .gj{font-size:30px; font-weight:900; line-height:1.05; margin-bottom:6px;}
.scrollcell .meta{font-size:12px; opacity:0.8; line-height:1.2;}
</style>
""",
    unsafe_allow_html=True
)


# ---------- UI style ----------
st.markdown(
    """
<style>
.card {
  background: rgba(255,255,255,0.04);
  border: 1px solid rgba(255,255,255,0.10);
  border-radius: 14px;
  padding: 14px 16px;
}
.card-title { font-size: 0.95rem; opacity: 0.85; margin-bottom: 6px; font-weight: 600; }
.big { font-size: 2.6rem; font-weight: 800; line-height: 1.05; }
.small { font-size: 0.95rem; opacity: 0.85; margin-top: 6px; }
.kv { font-size: 0.9rem; opacity: 0.80; margin-top: 2px; }
hr.soft { border: none; border-top: 1px solid rgba(255,255,255,0.08); margin: 10px 0; }
</style>
""",
    unsafe_allow_html=True
)


BUILD_TAG = "easy27-2026-01-14"
def render_pillars_tiles(y_stem, y_branch, m_stem, m_branch, d_stem, d_branch, h_stem, h_branch):
    """타일형 4주 표시: (왼쪽→오른쪽) 시주/일주/월주/연주 (연주가 맨 오른쪽)"""
    pillars = [
        ("시주", h_stem, h_branch),
        ("일주", d_stem, d_branch),
        ("월주", m_stem, m_branch),
        ("연주", y_stem, y_branch),
    ]

    cols_html = []
    for label, s, b in pillars:
        s_elem = get_element_idx(s)
        b_elem = get_element_idx(b)

        s_fill = ELEM_FILL[s_elem]
        b_fill = ELEM_FILL[b_elem]
        s_text = ELEM_TEXT[s_elem]
        b_text = ELEM_TEXT[b_elem]

        # 십신(일간 기준)
        if label == "일주":
            stem_sipsin = "본원"
        else:
            stem_sipsin = get_sipsin(d_stem, s)
        branch_sipsin = get_sipsin(d_stem, b)

        # 지장간(사용자 지정 순서: 초기→중기→정기)
        hidden = "".join(HIDDEN_STEMS.get(b, []))

        cols_html.append(dedent(f"""
<div class="pillarcol">
  <div class="pillarlabel">{label}</div>
  <div class="sipsin-top">{stem_sipsin}</div>

  <div class="tile" style="background:{s_fill}; color:{s_text};">{s}</div>
  <div class="tile" style="background:{b_fill}; color:{b_text};">{b}</div>

  <div class="hiddenstems">{hidden}</div>
  <div class="sipsin-bot">{branch_sipsin}</div>
</div>
""").strip())

    st.markdown(f'<div class="pillars-wrap">{"".join(cols_html)}</div>', unsafe_allow_html=True)


def render_daewoon_scroll(rows, selected_idx: int):
    cells = []
    for i, r in enumerate(rows):
        age = r.get("시작(세)")
        try:
            age_f = float(age)
            age_str = fmt_age_year_month(age_f)
        except Exception:
            age_str = str(age)
        gj = r.get("대운")
        border = "2px solid rgba(52,199,89,0.9)" if i == selected_idx else "1px solid rgba(255,255,255,0.10)"
        cells.append(dedent(f'''
<div class="scrollcell" style="border:{border};">
  <div class="top">{r.get("순서")}대운</div>
  <div class="gj">{gj}</div>
  <div class="meta">{age_str} 시작</div>
</div>
''').strip())
    st.markdown(f'<div class="scrollrow">{"".join(cells)}</div>', unsafe_allow_html=True)


def build_10year_seun_table(luck_row: dict, birth_solar_year: int, day_stem: str) -> list[dict]:
    """Build a 10-year 세운 표 for the selected 대운 row.

    - birth_solar_year: the (solar) year used for 연주 계산 기준
    - day_stem: 일간(일주 천간) for 십신 산출
    """
    start_age = float(luck_row.get("start_age", 0.0))
    # 세운의 "연도"는 보통 시작 나이의 정수 부분을 출생 기준 연도에 더해 잡습니다.
    start_year = int(birth_solar_year + math.floor(start_age + 1e-9))

    rows: list[dict] = []
    for i in range(10):
        y = start_year + i
        age = start_age + i
        ganji = sexagenary_for_year(y)
        y_stem, y_branch = ganji[0], ganji[1]
        rows.append({
            "연도": y,
            "나이": fmt_age_year_month(age),
            "세운": ganji,
            "천간십신": get_sipsin(day_stem, y_stem),
            "지지십신": get_sipsin(day_stem, y_branch),
            "십이운성(천간→지지)": get_unseong_12(y_stem, y_branch),
            "지장간": "".join(HIDDEN_STEMS.get(y_branch, [])),
        })
    return rows

def render_sewoon_scroll(seun_rows):
    """세운(10년) 가로 스크롤 — 키 이름이 달라도 동작하도록 유연하게 처리"""
    cells = []
    for r in seun_rows:
        year = r.get("연도")
        age = r.get("나이(대략)", r.get("나이", ""))
        ganji = r.get("세운", r.get("세운(연간지)", ""))
        tg = r.get("천간십신", "")
        bg = r.get("지지십신", "")
        un = r.get("십이운성(일간기준)", "")
        cells.append(dedent(f"""
        <div class="scrollcell">
          <div class="top">{year} / {age}세</div>
          <div class="gj">{ganji}</div>
          <div class="meta">{tg}<br/>{bg}<br/>{un}</div>
        </div>
        """).strip())
    st.markdown(f'<div class="scrollrow">{"".join(cells)}</div>', unsafe_allow_html=True)


st.title("만세력 변환 명식 찾기")
st.caption(f"Build: {BUILD_TAG}")
st.caption("입력은 양력(그레고리력) 기준이며, 월주는 절입(태양 황경 15° 경계)·시주는 균시차까지 보정한 진태양시(LAT)로 계산합니다.")

with st.expander("중요: 법정시(시간대)·LMT(지역평균태양시) 선택에 관하여", expanded=False):
    st.write(
        "출생 시각이 출생증명서 등 '시계 시간'으로 기록된 경우에는 보통 법정시(시간대)를 의미합니다. "
        "19세기 등 표준시 이전 기록(예: 'LMT m10e0' 같은 표기)은 지역평균태양시(LMT)일 가능성이 높습니다. "
        "여기서는 사용자가 '입력 시각이 어떤 기준으로 기록되었는지'를 직접 선택하도록 되어 있습니다."
    )

col1, col2 = st.columns(2)
with col1:
    birth_date = st.date_input("출생일(양력)", value=dt.date(1990, 1, 1))
with col2:
    time_str = st.text_input("출생시각 (HH:MM 또는 HH:MM:SS)", value="12:00")

basis = st.selectbox("입력 시각의 기준", ["표준시(시간대 적용)", "지역평균태양시(LMT)"], index=0)


gender = st.selectbox("성별(대운 방향에 사용)", ["여성", "남성"], index=0)
gender_short = "여" if gender.startswith("여") else "남"

st.subheader("출생지(좌표)")
place = st.text_input("장소 검색(도시/주소/지명)", value="")
if st.button("검색"):
    try:
        st.session_state["geocode_results"] = geocode(place, limit=6)
    except Exception as e:
        st.error(f"검색 실패: {e}")

results = st.session_state.get("geocode_results", [])
chosen = None
if results:
    labels = [r["label"] for r in results]
    idx = st.selectbox("검색 결과 선택", range(len(labels)), format_func=lambda i: labels[i])
    chosen = results[idx]
    st.session_state["lat"] = chosen["lat"]
    st.session_state["lon"] = chosen["lon"]

lat = st.number_input("위도(lat)", value=float(st.session_state.get("lat", 37.5665)))
lon = st.number_input("경도(lon, 동경+ / 서경-)", value=float(st.session_state.get("lon", 126.9780)))

use_early_zi = True  # 고정: 자시(23:00)부터 다음 날(진태양시 LAT 기준)
st.info("일자 경계는 **진태양시(LAT) 기준 23:00(자시)**부터 다음 날로 고정되어 있습니다.")

if st.button("명식 계산"):
    try:
        birth_time = parse_hms(time_str)
        naive = dt.datetime.combine(birth_date, birth_time)

        tz_name = get_timezone_name(lat, lon)
        if tz_name is None:
            st.warning("해당 좌표의 시간대(IANA)를 찾지 못했습니다. 표준시 입력은 정확도가 떨어질 수 있습니다.")
            tz_name = "UTC"

        if basis.startswith("표준시"):
            local = naive.replace(tzinfo=ZoneInfo(tz_name))
            utc_dt = local.astimezone(dt.timezone.utc)
            basis_note = f"표준시({tz_name}) → UTC 변환"
        else:
            # Interpret naive as local mean time at longitude
            utc_dt = naive.replace(tzinfo=dt.timezone.utc) - dt.timedelta(seconds=lon * 240.0)
            basis_note = "LMT(지역평균태양시) → UTC 변환(경도 보정)"

        jd_ut = jd_ut_from_utc(utc_dt)
        lat_dt, eot_min = apparent_solar_datetime(utc_dt, lon)  # tz-aware UTC, but represents LAT

        # Year / Month (UT 비교)
        y_stem, y_branch, pillar_year, lichun_jd = year_pillar(jd_ut)
        m_stem, m_branch, term_name, term_jd = month_pillar(jd_ut, y_stem)

        # Day / Hour (LAT 기준)
        d_stem, d_branch, day_label_date, day_jdn = day_pillar(lat_dt, use_early_zi=use_early_zi)
        h_stem, h_branch, h_sb = hour_pillar(lat_dt, d_stem)

        st.success("계산 완료")
        # ---- 4 Pillars (명식) : 타일형 ----
        st.write("### 명식(타일)")
        render_pillars_tiles(y_stem, y_branch, m_stem, m_branch, d_stem, d_branch, h_stem, h_branch)

        show_detail = st.toggle("상세 표시(십신/십이운성 표)", value=False)
        if show_detail:
            st.write("### 상세 정보(검증/참고용)")
            detail_rows = [
                {
                    "주": "연주",
                    "천간": y_stem,
                    "지지": y_branch,
                    "천간십신": get_sipsin(d_stem, y_stem),
                    "지지십신": get_sipsin(d_stem, y_branch),
                    "십이운성(천간→지지)": get_unseong_12(y_stem, y_branch),
                    "지장간": "".join(HIDDEN_STEMS.get(y_branch, [])),
                },
                {
                    "주": "월주",
                    "천간": m_stem,
                    "지지": m_branch,
                    "천간십신": get_sipsin(d_stem, m_stem),
                    "지지십신": get_sipsin(d_stem, m_branch),
                    "십이운성(천간→지지)": get_unseong_12(m_stem, m_branch),
                    "지장간": "".join(HIDDEN_STEMS.get(m_branch, [])),
                },
                {
                    "주": "일주",
                    "천간": d_stem,
                    "지지": d_branch,
                    "천간십신": "본원",
                    "지지십신": get_sipsin(d_stem, d_branch),
                    "십이운성(천간→지지)": get_unseong_12(d_stem, d_branch),
                    "지장간": "".join(HIDDEN_STEMS.get(d_branch, [])),
                },
                {
                    "주": "시주",
                    "천간": h_stem,
                    "지지": h_branch,
                    "천간십신": get_sipsin(d_stem, h_stem),
                    "지지십신": get_sipsin(d_stem, h_branch),
                    "십이운성(천간→지지)": get_unseong_12(h_stem, h_branch),
                    "지장간": "".join(HIDDEN_STEMS.get(h_branch, [])),
                },
            ]
            st.dataframe(detail_rows, use_container_width=True, hide_index=True)


        with st.expander("텍스트 결과(4주) / 검증용 정보", expanded=False):
            st.write(f"연주: **{y_stem}{y_branch}**  (기준 연도: {pillar_year}, 입춘 기준)")
            st.write(f"월주: **{m_stem}{m_branch}**  (기준 절기: {term_name})")
            st.write(f"일주: **{d_stem}{d_branch}**  (LAT 날짜: {day_label_date.isoformat()}, JDN={day_jdn})")
            st.write(f"시주: **{h_stem}{h_branch}**  (LAT 시각 기준)")


        # ---- Luck Pillars (대운) ----
        try:
            direction = luck_direction(y_stem, gender_short)
            term2_name, term2_lon, term2_jd = adjacent_solar_term(jd_ut, forward=(direction == "순행"))
            term2_utc = utc_from_jd_ut(term2_jd)

            if basis.startswith("표준시"):
                birth_clock = utc_dt.astimezone(ZoneInfo(tz_name))
                term_clock = term2_utc.astimezone(ZoneInfo(tz_name))
                clock_note = f"표준시({tz_name}) 기준"
            else:
                birth_clock = utc_dt + dt.timedelta(seconds=lon * 240.0)
                term_clock = term2_utc + dt.timedelta(seconds=lon * 240.0)
                clock_note = "LMT(경도 보정) 기준"

            # 순행: next term - birth, 역행: birth - prev term
            delta_min = (term_clock - birth_clock).total_seconds() / 60.0
            if direction != "순행":
                delta_min = -delta_min

            years0, months0, days0 = minutes_to_luck_ymd(delta_min)
            luck_pillars = build_luck_pillars(m_stem, m_branch, direction, count=10)

            st.write("### 대운")
            st.write(f"- 대운 방향: **{direction}** (연간 음양 + 성별 기준)")
            st.write(f"- 기운 기준 절기: **{term2_name}** (λ={term2_lon:.0f}°)")
            st.write(f"- 출생~절기 간격({clock_note}): **{abs(delta_min)/1440.0:.3f}일**")
            st.write(f"- 대운수(기운 나이): **{years0}년 {months0}개월 {days0}일**  (표기: {years0}.{months0:02d})")

            rows = []
            start_age = years0 + months0/12.0 + days0/360.0
            for i, (s, b, sb) in enumerate(luck_pillars):
                st_age = start_age + i * 10.0
                ed_age = st_age + 10.0
                rows.append({
                    "순서": i+1,
                    "대운": sb,
                    "시작(표기)": fmt_age_year_month(st_age),
                    "끝(표기)": fmt_age_year_month(ed_age),
                    "시작(세)": round(st_age, 2),
                    "끝(세)": round(ed_age, 2),
                })

            st.subheader("대운")
            if rows:
                sel = st.selectbox(
                    "보고 싶은 대운(순서)",
                    options=[r["순서"] for r in rows],
                    index=0,
                    format_func=lambda x: f"{x} — {rows[x-1]['대운']} ({rows[x-1]['시작(표기)']} ~ {rows[x-1]['끝(표기)']})",
                )
                render_daewoon_scroll(rows, sel-1)

                with st.expander("대운 표(원본)", expanded=False):
                    st.dataframe(rows, use_container_width=True, hide_index=True)

                st.subheader("세운(선택 대운 10년)")
                seun = build_10year_seun_table(rows[sel-1], solar_year)
                render_sewoon_scroll(seun)

                with st.expander("세운 표(원본)", expanded=False):
                    st.dataframe(seun, use_container_width=True, hide_index=True)
        except Exception as e:
            st.warning(f"대운 계산은 실패했습니다: {e}")


        st.write("### 중간 값(검증용)")
        st.write(f"- 좌표: lat={lat:.6f}, lon={lon:.6f}")
        st.write(f"- 시간대(IANA): {tz_name}")
        st.write(f"- 입력 기준: {basis_note}")
        st.write(f"- 출생 시각(UTC): {utc_dt.isoformat()}")
        # standard time display (for transparency)
        try:
            local_std = utc_dt.astimezone(ZoneInfo(tz_name))
            st.write(f"- 출생 시각(표준시): {local_std.isoformat()}")
        except Exception:
            pass

        st.write(f"- 균시차(EoT): {eot_min:+.3f} 분  (LAT = LMT + EoT)")
        st.write(f"- 진태양시(LAT): {lat_dt.replace(tzinfo=None).isoformat(sep=' ')} (표기상 UTC tz를 떼고 '현지 LAT'로 해석)")
        # term times
        term_utc = utc_from_jd_ut(term_jd)
        st.write(f"- 월주 기준 절입시각(UTC): {term_utc.isoformat()}")
        try:
            st.write(f"- 월주 기준 절입시각(표준시): {term_utc.astimezone(ZoneInfo(tz_name)).isoformat()}")
        except Exception:
            pass
        lichun_utc = utc_from_jd_ut(lichun_jd)
        st.write(f"- 입춘(연주 경계) 시각(UTC): {lichun_utc.isoformat()}")

    except Exception as e:
        st.error(f"오류: {e}")
