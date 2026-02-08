import os
import math
import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

import requests
import streamlit as st
from timezonefinder import TimezoneFinder
import swisseph as swe

# =========================================================
# 만세력(명식) 프로토타입 — 전 세계/정확 시각/절입·진태양시 기준
# =========================================================
# - 입력: 양력(그레고리력) 기준
# - 월주: 절입(태양 황경 15° 경계, 12절) 기준
# - 시주: 진태양시(LAT) + 야자시(23:00~) 적용
# - 좌표: Nominatim(OpenStreetMap) 장소 검색 -> 위/경도
# - 시간대: timezonefinder (IANA tz)
#
# UI:
# - 카드(오행/십신/십이운성)
# - 지장간(지지 아래) 표시
# - 대운(순/역행 + 대운수(년·월·일))
# - 세운(선택 대운의 10년)

# ---------------------------------------------------------
# 1) 기초 데이터
# ---------------------------------------------------------
STEMS = ["甲","乙","丙","丁","戊","己","庚","辛","壬","癸"]
BRANCHES = ["子","丑","寅","卯","辰","巳","午","未","申","酉","戌","亥"]

# 오행 인덱스: 0=목, 1=화, 2=토, 3=금, 4=수
STEM_ELEMENTS = [0,0,1,1,2,2,3,3,4,4]
BRANCH_ELEMENTS = [4,2,0,0,2,1,1,2,3,3,2,4]

# 채영님 지정 지장간(초기-중기-정기(본기)) 순서 반영
# 子/卯/酉는 중기 없음(초기+정기)
HIDDEN_STEMS = {
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

# 오행 색(타일) — 수=검정 타일 + 흰 글자(금(회색)과 구분)
ELEM_FG = {
    0: "#2266CC",  # 목(파랑)
    1: "#DD4444",  # 화(빨강)
    2: "#DDAA00",  # 토(노랑)
    3: "#999999",  # 금(회색)
    4: "#FFFFFF",  # 수(글자 흰색)
}
ELEM_BG = {
    0: "#0E2A4A",  # 목 타일 배경(짙은 파랑)
    1: "#4A1111",  # 화
    2: "#3D3300",  # 토
    3: "#2A2A2A",  # 금(회색 계열)
    4: "#000000",  # 수(검정)
}

# 십신
# relation = (target_elem - day_elem) % 5
SIPSIN_NAMES = {
    0: ["비견","겁재"],
    1: ["식신","상관"],
    2: ["편재","정재"],
    3: ["편관","정관"],
    4: ["편인","정인"],
}

# 십이운성
UNSEONG_ORDER = ["절","태","양","장생","목욕","관대","건록","제왕","쇠","병","사","묘"]

# 월주: 12절(節) 기준(황경, 지지)
MAJOR_TERMS = [
    ("입춘", 315.0, "寅"), ("경칩", 345.0, "卯"), ("청명", 15.0,  "辰"),
    ("입하", 45.0,  "巳"), ("망종", 75.0,  "午"), ("소서", 105.0, "未"),
    ("입추", 135.0, "申"), ("백로", 165.0, "酉"), ("한로", 195.0, "戌"),
    ("입동", 225.0, "亥"), ("대설", 255.0, "子"), ("소한", 285.0, "丑"),
]

# 연간 -> 인월(입춘) 월간
Y_STEM_TO_YIN_MONTH_STEM = {
    "甲": "丙", "己": "丙",
    "乙": "戊", "庚": "戊",
    "丙": "庚", "辛": "庚",
    "丁": "壬", "壬": "壬",
    "戊": "甲", "癸": "甲",
}

# 일간 -> 자시 천간
D_STEM_TO_ZI_HOUR_STEM = {
    "甲": "甲", "己": "甲",
    "乙": "丙", "庚": "丙",
    "丙": "戊", "辛": "戊",
    "丁": "庚", "壬": "庚",
    "戊": "壬", "癸": "壬",
}

TF = TimezoneFinder()

# ---------------------------------------------------------
# 2) 유틸: 오행/음양/십신/십이운성
# ---------------------------------------------------------
def get_element_idx(char: str) -> int:
    if char in STEMS:
        return STEM_ELEMENTS[STEMS.index(char)]
    if char in BRANCHES:
        return BRANCH_ELEMENTS[BRANCHES.index(char)]
    return 0

def get_polarity(char: str) -> int:
    """0=양, 1=음"""
    if char in STEMS:
        return STEMS.index(char) % 2
    if char in BRANCHES:
        return BRANCHES.index(char) % 2
    return 0

def get_sipsin(day_stem: str, target: str) -> str:
    d_elem = get_element_idx(day_stem)
    t_elem = get_element_idx(target)
    relation = (t_elem - d_elem) % 5

    d_pol = get_polarity(day_stem)
    t_pol = get_polarity(target)

    # 지지의 일부 음양 보정(요청 반영: 기본은 인덱스 홀짝 + 예외 보정)
    if target == "子": t_pol = 0
    elif target == "亥": t_pol = 1
    elif target == "午": t_pol = 1
    elif target == "巳": t_pol = 0

    is_diff = 1 if d_pol != t_pol else 0
    return SIPSIN_NAMES[relation][is_diff]

def get_12unseong(stem: str, branch: str) -> str:
    start_map = {
        "甲": ("亥",  1), "丙": ("寅",  1), "戊": ("寅",  1), "庚": ("巳",  1), "壬": ("申",  1),
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

# ---------------------------------------------------------
# 3) 시간 처리 (Swisseph)
# ---------------------------------------------------------
def parse_hms(s: str) -> dt.time:
    s = (s or "").strip()
    parts = s.split(":")
    if len(parts) not in (2, 3):
        return dt.time(12, 0)
    try:
        hh, mm = int(parts[0]), int(parts[1])
        ss = int(parts[2]) if len(parts) == 3 else 0
        return dt.time(hh, mm, ss)
    except Exception:
        return dt.time(12, 0)

def jd_ut_from_utc(dt_utc: dt.datetime) -> float:
    hour = dt_utc.hour + dt_utc.minute/60 + dt_utc.second/3600
    return swe.julday(dt_utc.year, dt_utc.month, dt_utc.day, hour, swe.GREG_CAL)

def utc_from_jd_ut(jd_ut: float) -> dt.datetime:
    y, m, d, hour = swe.revjul(jd_ut, swe.GREG_CAL)
    hh = int(hour)
    mm = int((hour - hh) * 60)
    ss = int(round((((hour - hh) * 60) - mm) * 60))
    if ss == 60:
        ss = 0
        mm += 1
    if mm == 60:
        mm = 0
        hh += 1
    return dt.datetime(y, m, d, hh, mm, ss, tzinfo=dt.timezone.utc)

def apparent_solar_datetime(utc_dt: dt.datetime, lon_deg: float) -> tuple[dt.datetime, float]:
    jd_ut = jd_ut_from_utc(utc_dt)
    eot_days = swe.time_equ(jd_ut)  # days
    lat_dt = utc_dt + dt.timedelta(seconds=(lon_deg*240.0 + eot_days*86400.0))
    return lat_dt, eot_days * 1440.0  # minutes

def sexagenary(jdn: int) -> tuple[str, str]:
    idx = (jdn + 49) % 60
    return STEMS[idx % 10], BRANCHES[idx % 12]

def year_pillar(jd_ut_birth: float):
    birth_utc = utc_from_jd_ut(jd_ut_birth)
    y = birth_utc.year
    jd_start = swe.julday(y, 1, 1, 0.0, swe.GREG_CAL)
    lichun = swe.solcross_ut(315.0, jd_start, swe.FLG_SWIEPH)  # 입춘

    if jd_ut_birth < lichun:
        y -= 1

    idx = (y - 1984) % 60
    return STEMS[idx % 10], BRANCHES[idx % 12], y, lichun

def month_pillar(jd_ut_birth: float, year_stem: str):
    best = None
    for name, lon, branch in MAJOR_TERMS:
        jx = swe.solcross_ut(lon, jd_ut_birth - 40.0, swe.FLG_SWIEPH)
        if jx <= jd_ut_birth:
            if (best is None) or (jx > best[3]):
                best = (name, lon, branch, jx)

    if not best:
        return "甲","寅","오류", 0.0, None

    m_branch = best[2]
    order = ["寅","卯","辰","巳","午","未","申","酉","戌","亥","子","丑"]
    m_idx = order.index(m_branch)
    yin_stem = Y_STEM_TO_YIN_MONTH_STEM[year_stem]
    m_stem = STEMS[(STEMS.index(yin_stem) + m_idx) % 10]
    return m_stem, m_branch, best[0], best[3], best

def day_pillar(lat_dt: dt.datetime):
    adj_dt = lat_dt
    if lat_dt.hour >= 23:  # 야자시
        adj_dt = lat_dt + dt.timedelta(days=1)

    jd = swe.julday(adj_dt.year, adj_dt.month, adj_dt.day, 0, swe.GREG_CAL)
    jdn = int(math.floor(jd + 0.5))
    s, b = sexagenary(jdn)
    return s, b, jdn, adj_dt

def hour_pillar(lat_dt: dt.datetime, day_stem: str):
    minutes = lat_dt.hour * 60 + lat_dt.minute + lat_dt.second/60.0
    idx = int(((minutes + 60) // 120) % 12)  # 자시 중심 보정
    h_branch = BRANCHES[idx]

    zi_stem = D_STEM_TO_ZI_HOUR_STEM[day_stem]
    h_stem = STEMS[(STEMS.index(zi_stem) + idx) % 10]
    return h_stem, h_branch, idx

# ---------------------------------------------------------
# 4) 지오코딩 (OSM)
# ---------------------------------------------------------
def geocode_osm(place: str):
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "manseryeok-v2"}
    try:
        r = requests.get(url, params={"q": place, "format": "json", "limit": 1}, headers=headers, timeout=8)
        if r.ok:
            js = r.json()
            if js:
                return float(js[0]["lat"]), float(js[0]["lon"])
    except Exception:
        pass
    return None, None

# ---------------------------------------------------------
# 5) 대운 계산 (기본)
# ---------------------------------------------------------
def daewoon_direction(year_stem: str, gender: str) -> bool:
    """True=순행, False=역행"""
    is_yang_year = (STEMS.index(year_stem) % 2 == 0)
    is_male = (gender == "남")
    if is_male:
        return is_yang_year
    return not is_yang_year

def daewoon_start_delta_days(jd_ut_birth: float, month_branch: str, forward: bool, term_jd_current: float):
    # 현재 월지의 인덱스 찾기
    curr_term_idx = -1
    for i, (_, _, br) in enumerate(MAJOR_TERMS):
        if br == month_branch:
            curr_term_idx = i
            break

    if curr_term_idx < 0:
        return None, None

    if forward:
        next_idx = (curr_term_idx + 1) % 12
        next_lon = MAJOR_TERMS[next_idx][1]
        next_term_jd = swe.solcross_ut(next_lon, jd_ut_birth, swe.FLG_SWIEPH)
        diff_days = next_term_jd - jd_ut_birth
        return diff_days, next_term_jd
    else:
        diff_days = jd_ut_birth - term_jd_current
        return diff_days, term_jd_current

def days_to_ymd_for_daewoon(diff_days: float):
    """
    흔히 쓰는 근사:
      3일=1년 => diff_days/3 = years
    여기서는 '반올림' 대신 '년 + 월 + 일' 표기로 보여주기 위해
      total_years = diff_days/3
      years = floor(total_years)
      months = floor((total_years - years) * 12)
      days = round((((total_years - years) * 12) - months) * 30)
    """
    total_years = diff_days / 3.0
    years = int(math.floor(total_years))
    rem = (total_years - years) * 12.0
    months = int(math.floor(rem))
    rem2 = (rem - months) * 30.0
    days = int(round(rem2))
    # 보정
    if days >= 30:
        days -= 30
        months += 1
    if months >= 12:
        months -= 12
        years += 1
    return years, months, days, total_years

def build_daewoon_list(month_stem: str, month_branch: str, start_years_float: float, forward: bool, count: int = 10):
    m_stem_idx = STEMS.index(month_stem)
    m_branch_idx = BRANCHES.index(month_branch)

    res = []
    for i in range(1, count+1):
        offset = i if forward else -i
        d_stem = STEMS[(m_stem_idx + offset) % 10]
        d_branch = BRANCHES[(m_branch_idx + offset) % 12]
        start_age = start_years_float + (i-1)*10
        end_age = start_age + 10
        res.append({
            "idx": i,
            "ganji": f"{d_stem}{d_branch}",
            "stem": d_stem,
            "branch": d_branch,
            "start_age": start_age,
            "end_age": end_age,
        })
    return res

def year_ganji(year: int):
    offset = (year - 1984)  # 1984=갑자
    return STEMS[offset % 10], BRANCHES[offset % 12]

# ---------------------------------------------------------
# 6) UI (Streamlit)
# ---------------------------------------------------------
st.set_page_config(
    page_title="만세력 변환 명식 찾기",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 헤더(제목 교체 요청 반영)
st.title("만세력 변환 명식 찾기")
st.caption("입력은 양력(그레고리력) 기준이며, 월주는 절입(태양 황경 15° 경계), 시주는 진태양시(LAT) + 야자시(23:00~) 기준으로 계산합니다.")

# 카드/타일 CSS
st.markdown("""
<style>
    .pillars-wrap{
        display:flex;
        gap:16px;
        align-items:stretch;
        justify-content:flex-start;
        flex-wrap:wrap;
        margin-top:8px;
        margin-bottom:8px;
    }
    .pillar-card{
        background:#11161e;
        border:1px solid rgba(255,255,255,0.08);
        border-radius:16px;
        padding:14px 14px 12px 14px;
        min-width:220px;
        max-width:240px;
        box-shadow:0 6px 18px rgba(0,0,0,0.25);
    }
    .pillar-title{
        font-weight:800;
        font-size:15px;
        margin-bottom:6px;
        color:#e8edf5;
        letter-spacing:0.2px;
    }
    .pillar-sub{
        font-size:12px;
        color:rgba(232,237,245,0.75);
        margin-bottom:10px;
        line-height:1.35;
    }
    .tiles{
        display:flex;
        gap:10px;
        align-items:center;
        justify-content:flex-start;
        margin-bottom:10px;
    }
    .tile{
        width:68px;
        height:68px;
        border-radius:14px;
        display:flex;
        align-items:center;
        justify-content:center;
        font-size:34px;
        font-weight:900;
        border:1px solid rgba(255,255,255,0.10);
        box-shadow: inset 0 0 0 1px rgba(255,255,255,0.05);
        user-select:none;
    }
    .tile.small{
        width:64px;
        height:64px;
        font-size:32px;
    }
    .hidden-wrap{
        margin-top:6px;
        font-size:12px;
        color:rgba(232,237,245,0.78);
        line-height:1.25;
    }
    .hidden-stems{
        margin-top:2px;
        font-size:13px;
        color:rgba(232,237,245,0.92);
        font-weight:700;
        letter-spacing:0.3px;
    }
    .meta{
        font-size:12px;
        color:rgba(232,237,245,0.75);
        line-height:1.4;
    }

    /* 대운 바 (스크롤) */
    .dw-scroll{
        display:flex;
        gap:10px;
        overflow-x:auto;
        padding:8px 4px 12px 4px;
        margin-top:4px;
        margin-bottom:8px;
    }
    .dw-item{
        min-width:96px;
        border-radius:16px;
        padding:10px 10px 8px 10px;
        background:#11161e;
        border:1px solid rgba(255,255,255,0.08);
        text-align:center;
        cursor:pointer;
        user-select:none;
    }
    .dw-item.sel{
        border:2px solid rgba(34,102,204,0.9);
        background:#0f1a2c;
    }
    .dw-ganji{
        font-size:18px;
        font-weight:900;
        color:#e8edf5;
        line-height:1.15;
        margin-bottom:4px;
    }
    .dw-age{
        font-size:12px;
        color:rgba(232,237,245,0.75);
    }
</style>
""", unsafe_allow_html=True)

def tile_html(char: str):
    elem = get_element_idx(char)
    fg = ELEM_FG[elem]
    bg = ELEM_BG[elem]
    return f'<div class="tile" style="color:{fg}; background:{bg};">{char}</div>'

def pillar_card_html(title_kor: str, stem: str, branch: str, day_stem: str):
    s_sipsin = "본원" if title_kor == "일주" else get_sipsin(day_stem, stem)
    b_sipsin = get_sipsin(day_stem, branch)
    unseong = get_12unseong(stem, branch)

    hidden = HIDDEN_STEMS.get(branch, [])
    hidden_txt = "".join(hidden) if hidden else "-"

    html = f"""
    <div class="pillar-card">
        <div class="pillar-title">{title_kor}</div>
        <div class="pillar-sub">
            천간 십신: {s_sipsin} / 지지 십신: {b_sipsin}<br/>
            십이운성: {unseong}
        </div>
        <div class="tiles">
            {tile_html(stem)}
            {tile_html(branch)}
        </div>
        <div class="hidden-wrap">
            지장간(초·중·정/본기 순):<br/>
            <div class="hidden-stems">{hidden_txt}</div>
        </div>
    </div>
    """
    return html

# ---------------------------------------------------------
# 7) 입력 UI
# ---------------------------------------------------------
with st.expander("중요: 법정시(시간대)·LMT(지역평균태양시) 선택에 관하여", expanded=False):
    st.write(
        "이 앱은 입력된 시각을 먼저 **해당 지역의 법정시(IANA 시간대)**로 해석한 뒤 UTC로 변환하고, "
        "그 UTC에 경도(4분/1도) + 균시차(EoT)를 더해 **진태양시(LAT)**를 계산합니다. "
        "즉, 사용자가 LMT를 직접 입력하는 방식이 아니라, 좌표 기반으로 LAT를 만들기 위한 표준 절차를 따릅니다."
    )

c1, c2, c3 = st.columns([1, 1, 2])
with c1:
    birth_date = st.date_input("출생일(양력)", dt.date(1998, 1, 1))
with c2:
    time_str = st.text_input("출생시각 (HH:MM 또는 HH:MM:SS)", "12:00")
with c3:
    gender = st.radio("성별(대운 순/역행)", ["남", "여"], horizontal=True)

st.subheader("출생지(좌표)")
p1, p2, p3 = st.columns([2, 1, 1])
with p1:
    place = st.text_input("장소 검색(도시/주소/지명)", "Seoul")
with p2:
    if st.button("검색", type="secondary"):
        lat, lon = geocode_osm(place)
        if lat is not None:
            st.session_state["lat"] = lat
            st.session_state["lon"] = lon
            st.success(f"좌표 설정: lat={lat:.6f}, lon={lon:.6f}")
        else:
            st.error("장소를 찾을 수 없습니다.")
with p3:
    calc_btn = st.button("명식 계산", type="primary")

lat = st.number_input("위도", value=float(st.session_state.get("lat", 37.5665)), format="%.6f")
lon = st.number_input("경도", value=float(st.session_state.get("lon", 126.9780)), format="%.6f")

show_advanced = st.toggle("고급 표시(오행/십신/십이운성 카드 + 선택 대운의 세운표)", value=True)

# ---------------------------------------------------------
# 8) 계산/출력
# ---------------------------------------------------------
if calc_btn:
    try:
        b_time = parse_hms(time_str)
        naive = dt.datetime.combine(birth_date, b_time)

        tz_str = TF.timezone_at(lat=lat, lng=lon) or "Asia/Seoul"
        local_dt = naive.replace(tzinfo=ZoneInfo(tz_str))
        utc_dt = local_dt.astimezone(dt.timezone.utc)

        lat_dt, eot_min = apparent_solar_datetime(utc_dt, lon)
        jd_ut = jd_ut_from_utc(utc_dt)

        # 4주
        y_s, y_b, base_year, lichun_jd = year_pillar(jd_ut)
        m_s, m_b, m_term_name, m_term_jd, m_term_best = month_pillar(jd_ut, y_s)
        d_s, d_b, jdn, day_base_dt = day_pillar(lat_dt)
        h_s, h_b, hour_idx = hour_pillar(lat_dt, d_s)

        st.success("계산 완료")

        st.header("결과(사주 4주)")
        st.write(f"연주: {y_s}{y_b} (기준 연도: {base_year}, 입춘 기준)")
        st.write(f"월주: {m_s}{m_b} (기준 절기: {m_term_name})")
        st.write(f"일주: {d_s}{d_b} (LAT 날짜: {day_base_dt.date()}, JDN={jdn})")
        st.write(f"시주: {h_s}{h_b} (LAT 시각 기준)")

        if show_advanced:
            st.subheader("카드 보기(오행/십신/십이운성)")
            # 요청 반영: 오른쪽이 연주, 왼쪽으로 갈수록 월-일-시
            # -> 화면에선 "연-월-일-시"를 오른쪽에서 왼쪽으로 보이게 배치
            cards_html = f"""
            <div class="pillars-wrap">
                {pillar_card_html("연주", y_s, y_b, d_s)}
                {pillar_card_html("월주", m_s, m_b, d_s)}
                {pillar_card_html("일주", d_s, d_b, d_s)}
                {pillar_card_html("시주", h_s, h_b, d_s)}
            </div>
            """
            st.markdown(cards_html, unsafe_allow_html=True)

        # -----------------------------
        # 대운
        # -----------------------------
        st.header("대운")

        forward = daewoon_direction(y_s, gender)
        diff_days, ref_term_jd = daewoon_start_delta_days(jd_ut, m_b, forward, m_term_jd)

        if diff_days is None:
            st.error("대운 계산: 절기 기준점 탐색 실패")
        else:
            years, months, days, years_float = days_to_ymd_for_daewoon(diff_days)
            st.write(f"- 대운 방향: {'순행' if forward else '역행'} (연간 음양 + 성별 기준)")
            st.write(f"- 기운 기준 절기: 입춘(立春) (λ=315°)")
            st.write(f"- 출생-절기 간격({tz_str} 기준): {diff_days:.3f}일")
            st.write(f"- 대운수(기운 나이): {years}년 {months}개월 {days}일 (표기: {years_float:.3f})")

            daewoon_list = build_daewoon_list(m_s, m_b, years_float, forward, count=10)

            # 선택
            if "dw_sel" not in st.session_state:
                st.session_state["dw_sel"] = 0

            # 버튼으로 선택(가로 스크롤 느낌)
            # Streamlit 버튼/HTML 이벤트 연동이 제한되므로, 버튼은 columns로 구성
            cols = st.columns(10)
            for i, dw in enumerate(daewoon_list):
                with cols[i]:
                    label = f"{dw['ganji']}\n{dw['start_age']:.2f}~{dw['end_age']:.2f}"
                    if st.button(label, key=f"dw_{i}"):
                        st.session_state["dw_sel"] = i

            sel = daewoon_list[st.session_state["dw_sel"]]

            # 표
            st.subheader("대운표")
            table_rows = []
            for dw in daewoon_list:
                table_rows.append({
                    "순서": dw["idx"],
                    "대운": dw["ganji"],
                    "시작(세)": round(dw["start_age"], 2),
                    "끝(세)": round(dw["end_age"], 2),
                })
            st.dataframe(table_rows, use_container_width=True, hide_index=True)

            # -----------------------------
            # 세운(선택 대운)
            # -----------------------------
            if show_advanced:
                st.subheader(f"선택 대운의 세운표: {sel['ganji']}")
                start_year = base_year + int(math.floor(sel["start_age"]))
                seun = []
                for k in range(10):
                    year = start_year + k
                    ys, yb = year_ganji(year)
                    seun.append({
                        "연도": year,
                        "간지": f"{ys}{yb}",
                        "천간 십신": get_sipsin(d_s, ys),
                        "지지 십신": get_sipsin(d_s, yb),
                        "12운성": get_12unseong(d_s, yb),
                    })
                st.dataframe(seun, use_container_width=True, hide_index=True)

        # -----------------------------
        # 검증용 중간값
        # -----------------------------
        st.subheader("중간 값(검증용)")
        st.write(f"- 좌표: lat={lat}, lon={lon}")
        st.write(f"- 시간대(IANA): {tz_str}")
        st.write(f"- 입력 기준: 표준시({tz_str}) → UTC 변환")
        st.write(f"- 출생 시각(UTC): {utc_dt.isoformat()}")
        st.write(f"- 출생 시각(표준시): {local_dt.isoformat()}")
        st.write(f"- 균시차(EoT): {eot_min:.3f} 분 (LAT = LMT + EoT)")
        st.write(f"- 진태양시(LAT): {lat_dt.replace(tzinfo=None)} (표기상 tz를 떼고 '현지 LAT'로 해석)")
        st.write(f"- 월주 기준 절입시각(UTC JD): {m_term_jd}")
        st.write(f"- 입춘(연주 경계) 시각(UTC JD): {lichun_jd}")

    except Exception as e:
        st.error(f"오류: {e}")
