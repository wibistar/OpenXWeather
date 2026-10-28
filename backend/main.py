"""
AeroGrid 2D ASEAN Hybrid Weather Radar & X-Plane 11 Bridge Backend (v5.0 — FastAPI)
What's New in v5.0:
1. Browser GUI Master Sync (`POST /api/xplane/gui_state`):
   - The Browser GUI acts as the single Master Reference. When open in the browser, it pushes its exact
     `spawn_nodes` (with exact Cloud Base & Cloud Top), aircraft category turbulence multiplier, and manual
     turbulence cap to the backend so X-Plane 11 matches the GUI popups 100%.
2. 4-Factor Realistic Aviation Turbulence Engine:
   - Combines (1) In-Cloud Convective Turbulence + Airspeed Penetration, (2) Clear Air Turbulence (CAT)
     from horizontal wind shear, (3) Low-Level Mechanical/Gust Turbulence (<3,500 ft), and (4) Thermal Heating,
     scaled by Aircraft Category (Light GA 0.6x -> Heavy Airliner 1.4x) and clamped by Manual Max Turbulence Limit.
3. 1-to-1 World-Locked 50x50m Red Box Dispersion (`debug_red_box_50m.obj`).
"""
import math
import os
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

app = FastAPI(
    title="AeroGrid 2D ASEAN Hybrid Weather & XP11 Bridge Backend",
    description="Master GUI Weather Sync + 4-Factor Turbulence Engine for X-Plane 11 (XPPython3)",
    version="5.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_INDEX = os.path.join(BASE_DIR, "frontend", "index.html")

NOAA_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"
CACHE_TTL_SECONDS = 600
STATE_CACHE: Dict[str, Any] = {"timestamp": 0.0, "stations": []}

ASEAN_PRIMARY_STATIONS = [
    {"icao": "WIII", "country": "Indonesia", "name": "Soekarno-Hatta Intl (Jakarta)", "lat": -6.1256, "lon": 106.6560, "raw": "METAR WIII 271100Z 11009KT 6000 FEW020 SCT080 29/24 Q1010 NOSIG"},
    {"icao": "WADD", "country": "Indonesia", "name": "I Gusti Ngurah Rai Intl (Bali)", "lat": -8.7484, "lon": 115.1671, "raw": "METAR WADD 270000Z 12010KT 9999 SCT016 BKN045 28/27 Q1013 NOSIG"},
    {"icao": "WMKK", "country": "Malaysia", "name": "Kuala Lumpur Intl (Sepang)", "lat": 2.7456, "lon": 101.7100, "raw": "METAR WMKK 270330Z 10007KT 7000 -SHRA FEW010 FEW017CB SCT018 28/26 Q1013 NOSIG"},
    {"icao": "WMKP", "country": "Malaysia", "name": "Penang International Airport", "lat": 5.2963, "lon": 100.2762, "raw": "METAR WMKP 271930Z 09006KT 9000 -RA SCT025 BKN060 24/24 Q1010 NOSIG"},
    {"icao": "WSSS", "country": "Singapore", "name": "Singapore Changi Intl", "lat": 1.3502, "lon": 103.9940, "raw": "METAR WSSS 271130Z 08009KT 9000 FEW018 SCT035 BKN300 30/24 Q1010 NOSIG"},
    {"icao": "WSSL", "country": "Singapore", "name": "Seletar Airport (Singapore)", "lat": 1.4156, "lon": 103.8667, "raw": "METAR WSSL 231600Z 08007KT 6000 FEW018 SCT030 BKN280 29/25 Q1010 NOSIG"},
    {"icao": "VTBS", "country": "Thailand", "name": "Suvarnabhumi Intl (Bangkok)", "lat": 13.6811, "lon": 100.7470, "raw": "METAR VTBS 271900Z 07008KT 9999 FEW020 SCT045 28/25 Q1012 NOSIG"},
    {"icao": "VTSP", "country": "Thailand", "name": "Phuket International Airport", "lat": 8.1133, "lon": 98.3174, "raw": "METAR VTSP 260630Z 08008KT 9999 -RA FEW015 SCT025 BKN050 28/25 Q1011 NOSIG"},
    {"icao": "VVTS", "country": "Vietnam", "name": "Tan Son Nhat Intl (Ho Chi Minh)", "lat": 10.8188, "lon": 106.6520, "raw": "METAR VVTS 271600Z 10008KT 9999 FEW015 SCT030 28/23 Q1012 NOSIG"},
    {"icao": "VVNB", "country": "Vietnam", "name": "Noi Bai Intl (Hanoi)", "lat": 21.2212, "lon": 105.8070, "raw": "METAR VVNB 271500Z 06009KT 9999 FEW025 SCT049 27/22 Q1019 NOSIG"},
    {"icao": "RPLL", "country": "Philippines", "name": "Ninoy Aquino Intl (Manila)", "lat": 14.5086, "lon": 121.0200, "raw": "METAR RPLL 280100Z 08011KT 9999 FEW022 SCT040 32/23 Q1014 NOSIG"},
    {"icao": "RPVM", "country": "Philippines", "name": "Mactan-Cebu Intl (Cebu)", "lat": 10.3093, "lon": 123.9797, "raw": "METAR RPVM 271700Z 07009KT 9999 FEW014 SCT022 BKN060 28/24 Q1012 NOSIG"},
    {"icao": "VDPP", "country": "Cambodia", "name": "Phnom Penh Intl Airport", "lat": 11.5472, "lon": 104.8447, "raw": "METAR VDPP 270830Z 07007KT 9999 SCT017 BKN045 32/24 Q1009 NOSIG"},
    {"icao": "VDSA", "country": "Cambodia", "name": "Siem Reap-Angkor Intl", "lat": 13.3697, "lon": 104.2238, "raw": "METAR VDSA 161900Z 07006KT 8000 SCT015 SCT040 25/24 Q1010 NOSIG"},
    {"icao": "VYYY", "country": "Myanmar", "name": "Yangon International Airport", "lat": 16.9073, "lon": 96.1332, "raw": "METAR VYYY 261630Z 06006KT 5000 -TSRA SCT015 FEW018CB BKN080 27/25 Q1011 NOSIG"},
    {"icao": "VYMD", "country": "Myanmar", "name": "Mandalay International Airport", "lat": 21.7022, "lon": 95.9779, "raw": "METAR VYMD 270930Z 05005KT 8000 SCT020 FEW025CB 33/24 Q1011 NOSIG"},
    {"icao": "VLVT", "country": "Laos", "name": "Wattay Intl (Vientiane)", "lat": 17.9851, "lon": 102.5667, "raw": "METAR VLVT 250830Z 06006KT 8000 FEW020 SCT050 34/29 Q1007 NOSIG"},
    {"icao": "VLLB", "country": "Laos", "name": "Luang Prabang Intl Airport", "lat": 19.9043, "lon": 102.1672, "raw": "METAR VLLB 270300Z 06005KT 8000 FEW022 SCT045 27/21 Q1012 NOSIG"},
    {"icao": "WBSB", "country": "Brunei", "name": "Brunei International Airport", "lat": 4.9442, "lon": 114.9280, "raw": "METAR WBSB 230600Z 08007KT 9000 -TSRA SCT014 FEW015CB BKN140 29/25 Q1009 NOSIG"},
]

OKTAS_MAP = {"FEW": 2, "SCT": 4, "BKN": 6, "OVC": 8, "VV": 8}


def decode_station_metar(st: dict) -> dict:
    raw = st.get("raw", "")
    w_dir, w_spd, w_gust = 90, 8, 0
    m_wind = re.search(r"\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?KT\b", raw)
    if m_wind:
        w_dir = 90 if m_wind.group(1) == "VRB" else int(m_wind.group(1))
        w_spd = int(m_wind.group(2))
        w_gust = int(m_wind.group(3)) if m_wind.group(3) else 0

    vis_m = 9999
    m_vis = re.search(r"\b(?:KT|MPS)\s+(\d{4})\b", raw)
    if m_vis:
        vis_m = int(m_vis.group(1))

    qnh = 1011
    m_qnh = re.search(r"\bQ(\d{4})\b", raw)
    if m_qnh:
        qnh = int(m_qnh.group(1))

    temp_c, dew_c = 28, 24
    m_temp = re.search(r"\b(M?\d{2})/(M?\d{2})\b", raw)
    if m_temp:
        temp_c = -int(m_temp.group(1)[1:]) if m_temp.group(1).startswith("M") else int(m_temp.group(1))
        dew_c = -int(m_temp.group(2)[1:]) if m_temp.group(2).startswith("M") else int(m_temp.group(2))

    max_oktas = 0
    dominant_base_ft = 2200
    has_cb = "CB" in raw or "TS" in raw
    for m_cld in re.finditer(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3})(CB|TCU)?\b", raw):
        ok = OKTAS_MAP.get(m_cld.group(1), 2)
        b_ft = int(m_cld.group(2)) * 100
        if ok >= max_oktas:
            max_oktas = ok
            dominant_base_ft = b_ft

    move_rad = math.radians((w_dir + 180) % 360)
    u = max(3, w_spd) * math.sin(move_rad)
    v = max(3, w_spd) * math.cos(move_rad)

    return {
        **st,
        "wind_dir": w_dir,
        "wind_spd": w_spd,
        "wind_gust": w_gust,
        "u": u,
        "v": v,
        "vis_m": vis_m,
        "qnh": qnh,
        "temp_c": temp_c,
        "dew_c": dew_c,
        "oktas": max_oktas,
        "base_ft": dominant_base_ft,
        "has_cb": has_cb,
    }


def asean_mask(lat: float, lon: float) -> float:
    min_lat, max_lat = -11.5, 23.5
    min_lon, max_lon = 93.0, 126.5
    fade = 2.5
    lw = 1.0
    if lat < min_lat:
        lw = max(0.0, 1.0 - (min_lat - lat) / fade)
    elif lat > max_lat:
        lw = max(0.0, 1.0 - (lat - max_lat) / fade)
    lonw = 1.0
    if lon < min_lon:
        lonw = max(0.0, 1.0 - (min_lon - lon) / fade)
    elif lon > max_lon:
        lonw = max(0.0, 1.0 - (lon - max_lon) / fade)
    w = lw * lonw
    return w * w * (3.0 - 2.0 * w)


def deterministic_cloud_noise(lat: float, lon: float) -> float:
    x = lat * 3.2
    y = lon * 3.2
    wx = math.sin(x * 0.7 + 1.3) * 0.45
    wy = math.cos(y * 0.7 - 0.8) * 0.45
    n1 = 0.5 + 0.5 * math.sin((x + wx) * 2.1 + (y + wy) * 1.9)
    n2 = 0.5 + 0.5 * math.cos((x - wy) * 3.7 - (y + wx) * 3.1)
    return max(0.0, min(1.0, 0.72 * n1 + 0.28 * n2))


@app.get("/")
async def serve_gui_index():
    if os.path.exists(FRONTEND_INDEX):
        return FileResponse(FRONTEND_INDEX, media_type="text/html")
    return HTMLResponse("<h1>AeroGrid 2D Backend Running</h1>")


@app.get("/favicon.ico")
async def serve_favicon():
    return Response(status_code=204)


@app.get("/api/weather/bounds")
async def get_weather_bounds(
    min_lat: int = Query(-10),
    max_lat: int = Query(22),
    min_lon: int = Query(95),
    max_lon: int = Query(125),
):
    now = time.time()
    cached_ts = float(STATE_CACHE.get("timestamp", 0.0))
    if STATE_CACHE.get("stations") and (now - cached_ts < CACHE_TTL_SECONDS):
        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "cached": True,
            "cache_age_sec": round(now - cached_ts, 1),
            "stations": STATE_CACHE["stations"],
        }

    icaos = ",".join(s["icao"] for s in ASEAN_PRIMARY_STATIONS)
    stations = [dict(s) for s in ASEAN_PRIMARY_STATIONS]
    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "AeroGridAseanWeatherMVP/5.0"}) as client:
            resp = await client.get(NOAA_AWC_METAR_URL, params={"ids": icaos, "format": "json"})
            if resp.status_code == 200 and isinstance(resp.json(), list):
                raw_map = {item.get("icaoId"): item.get("rawOb") for item in resp.json() if item.get("icaoId")}
                for s in stations:
                    if s["icao"] in raw_map and raw_map[s["icao"]]:
                        s["raw"] = raw_map[s["icao"]]
    except Exception:
        pass

    STATE_CACHE["timestamp"] = now
    STATE_CACHE["stations"] = stations
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "cached": False,
        "stations": stations,
    }


# Master state shared between Browser GUI (Master Weather Reference) and X-Plane 11 (XPPython3)
XP11_STATE: Dict[str, Any] = {
    "connected": False,
    "lat": -6.1256,
    "lon": 106.6560,
    "elev_ft": 4500.0,
    "heading_deg": 335.0,
    "groundspeed_kt": 185.0,
    "object_mode": "red_box_50m",
    "aircraft_category": "light_jet",
    "aircraft_turb_scale": 1.0,
    "max_turb_cap": 0.50,
    "active_radius_nm": 40.0,
    "nearest_station": "WIII",
    "metar_visibility_m": 6000,
    "wind_dir_deg": 110.0,
    "wind_speed_kt": 9.0,
    "gust_extra_kt": 0.0,
    "turbulence": 0.04,
    "qnh_hpa": 1010.0,
    "oat_c": 29.0,
    "spawn_nodes_count": 0,
    "gui_master_nodes": None,
    "gui_master_updated_at": 0.0,
    "updated_at": 0.0,
}


class XP11TelemetryPayload(BaseModel):
    lat: float
    lon: float
    elev_ft: float
    heading_deg: float = 0.0
    groundspeed_kt: float = 0.0
    object_mode: Optional[str] = None


class GuiMasterStatePayload(BaseModel):
    object_mode: str = "red_box_50m"
    aircraft_category: str = "light_jet"
    aircraft_turb_scale: float = 1.0
    max_turb_cap: float = 0.50
    active_radius_nm: float = 40.0
    wind_dir_deg: float = 100.0
    wind_speed_kt: float = 9.0
    gust_extra_kt: float = 0.0
    turbulence: float = 0.04
    qnh_hpa: float = 1011.0
    oat_c: float = 28.0
    spawn_nodes: List[Dict[str, Any]] = []


@app.post("/api/xplane/gui_state")
async def update_gui_master_state(payload: GuiMasterStatePayload):
    """
    Receives the exact cloud spawn nodes (with exact Cloud Base & Cloud Top) and turbulence settings
    from the Browser GUI so the GUI is the 100% single Master Weather Reference for X-Plane 11!
    """
    XP11_STATE["object_mode"] = payload.object_mode
    XP11_STATE["aircraft_category"] = payload.aircraft_category
    XP11_STATE["aircraft_turb_scale"] = payload.aircraft_turb_scale
    XP11_STATE["max_turb_cap"] = payload.max_turb_cap
    XP11_STATE["active_radius_nm"] = payload.active_radius_nm
    XP11_STATE["wind_dir_deg"] = payload.wind_dir_deg
    XP11_STATE["wind_speed_kt"] = payload.wind_speed_kt
    XP11_STATE["gust_extra_kt"] = payload.gust_extra_kt
    XP11_STATE["turbulence"] = payload.turbulence
    XP11_STATE["qnh_hpa"] = payload.qnh_hpa
    XP11_STATE["oat_c"] = payload.oat_c
    XP11_STATE["gui_master_nodes"] = payload.spawn_nodes
    XP11_STATE["spawn_nodes_count"] = len(payload.spawn_nodes)
    XP11_STATE["gui_master_updated_at"] = time.time()
    return {"status": "ok", "synced_nodes": len(payload.spawn_nodes)}


@app.post("/api/xplane/sync")
async def sync_xplane11_telemetry(payload: XP11TelemetryPayload):
    """
    Called every 1.5s by PI_OpenXWeather.py in X-Plane 11.
    If the Browser GUI is open (gui_master_updated_at < 15s ago), serves the exact Master GUI nodes
    and computes in-cloud penetration turbulence using the aircraft's exact live elevation!
    """
    now = time.time()
    XP11_STATE["connected"] = True
    XP11_STATE["lat"] = round(payload.lat, 5)
    XP11_STATE["lon"] = round(payload.lon, 5)
    XP11_STATE["elev_ft"] = round(payload.elev_ft, 1)
    XP11_STATE["heading_deg"] = round(payload.heading_deg, 1)
    XP11_STATE["groundspeed_kt"] = round(payload.groundspeed_kt, 1)
    XP11_STATE["updated_at"] = now

    decoded_stations = [decode_station_metar(s) for s in (STATE_CACHE.get("stations") or ASEAN_PRIMARY_STATIONS)]
    cos_lat = max(0.2, math.cos(math.radians(payload.lat)))

    with_dist = []
    for st in decoded_stations:
        d_lat = payload.lat - st["lat"]
        d_lon = (payload.lon - st["lon"]) * cos_lat
        dist_deg = math.hypot(d_lat, d_lon)
        dist_nm = dist_deg * 60.0
        w = math.exp(-(dist_deg * dist_deg) / (2.0 * 2.5 * 2.5))
        with_dist.append((dist_nm, w, st))

    with_dist.sort(key=lambda x: x[0])
    nearest_dist_nm, _, nearest_st = with_dist[0]

    # Check if Browser GUI is actively pushing master nodes
    gui_active = (
        XP11_STATE.get("gui_master_nodes") is not None
        and (now - float(XP11_STATE.get("gui_master_updated_at", 0.0))) < 20.0
    )

    if gui_active:
        spawn_nodes = XP11_STATE["gui_master_nodes"]
        wind_dir_deg = float(XP11_STATE["wind_dir_deg"])
        wind_spd_kt = float(XP11_STATE["wind_speed_kt"])
        gust_extra_kt = float(XP11_STATE["gust_extra_kt"])
        qnh_interp = float(XP11_STATE["qnh_hpa"])
        temp_interp = float(XP11_STATE["oat_c"])
        active_radius_nm = float(XP11_STATE["active_radius_nm"])
    else:
        w_sum = sum(w for _, w, _ in with_dist if w > 0.001) or 1.0
        u_interp = sum(w * st["u"] for _, w, st in with_dist if w > 0.001) / w_sum
        v_interp = sum(w * st["v"] for _, w, st in with_dist if w > 0.001) / w_sum
        oktas_interp = sum(w * st["oktas"] for _, w, st in with_dist if w > 0.001) / w_sum
        base_ft_interp = sum(w * st["base_ft"] for _, w, st in with_dist if w > 0.001) / w_sum
        qnh_interp = sum(w * st["qnh"] for _, w, st in with_dist if w > 0.001) / w_sum
        temp_interp = sum(w * st["temp_c"] for _, w, st in with_dist if w > 0.001) / w_sum

        wind_spd_kt = max(3.0, math.hypot(u_interp, v_interp))
        move_deg = (math.degrees(math.atan2(u_interp, v_interp)) + 360.0) % 360.0
        wind_dir_deg = (move_deg + 180.0) % 360.0
        gust_extra_kt = max(0.0, nearest_st["wind_gust"] - nearest_st["wind_spd"]) if nearest_dist_nm < 45.0 else 0.0

        vis_m = nearest_st["vis_m"] if nearest_dist_nm < 90.0 else 9999
        metar_vis_nm = vis_m / 1852.0
        active_radius_nm = min(40.0, max(3.0, metar_vis_nm)) if vis_m < 9999 else 40.0

        mask_val = asean_mask(payload.lat, payload.lon)
        spawn_nodes = []
        if mask_val > 0.01:
            tau = max(0.05, min(0.95, 1.0 - (oktas_interp * mask_val) / 8.0))
            step_deg = 0.04 if active_radius_nm <= 15.0 else 0.065
            radius_deg = active_radius_nm / 60.0
            min_lat_idx = int(math.floor((payload.lat - radius_deg) / step_deg))
            max_lat_idx = int(math.ceil((payload.lat + radius_deg) / step_deg))
            min_lon_idx = int(math.floor((payload.lon - radius_deg / cos_lat) / step_deg))
            max_lon_idx = int(math.ceil((payload.lon + radius_deg / cos_lat) / step_deg))

            for lat_i in range(min_lat_idx, max_lat_idx + 1):
                pt_lat = round(lat_i * step_deg, 5)
                for lon_i in range(min_lon_idx, max_lon_idx + 1):
                    pt_lon = round(lon_i * step_deg, 5)
                    d_north_nm = (pt_lat - payload.lat) * 60.0
                    d_east_nm = (pt_lon - payload.lon) * 60.0 * cos_lat
                    dist_nm = math.hypot(d_north_nm, d_east_nm)
                    if dist_nm > active_radius_nm:
                        continue
                    n_val = deterministic_cloud_noise(pt_lat, pt_lon)
                    if n_val > tau:
                        excess = (n_val - tau) / max(0.2, 1.0 - tau)
                        base_ft = int(round(base_ft_interp / 100.0) * 100)
                        thickness_ft = int(round((1800 + 3600 * excess) / 100.0) * 100)
                        top_ft = base_ft + thickness_ft
                        layers = max(2, min(6, int(round(thickness_ft / 1100.0))))
                        spawn_nodes.append({
                            "lat": pt_lat,
                            "lon": pt_lon,
                            "dist_nm": round(dist_nm, 2),
                            "base_ft": base_ft,
                            "top_ft": top_ft,
                            "base_m": round(base_ft * 0.3048, 1),
                            "top_m": round(top_ft * 0.3048, 1),
                            "vertical_layers": layers,
                            "has_cb": nearest_st["has_cb"] and nearest_dist_nm < 40.0,
                        })

    # ==========================================================================
    # 4-FACTOR REALISTIC TURBULENCE ENGINE (Matches GUI Formula)
    # 1. In-Cloud Convective Turbulence (only when aircraft is inside a cloud vertically & horizontally!)
    # 2. Clear Air Turbulence (CAT / Wind Shear)
    # 3. Low-Level Mechanical & Gust Turbulence (< 3,500 ft)
    # 4. Thermal Surface Heating (< 4,000 ft & OAT >= 30°C)
    # ==========================================================================
    ac_elev_ft = payload.elev_ft
    ac_gs_kt = max(60.0, payload.groundspeed_kt)
    speed_penetration_factor = min(1.45, max(0.55, ac_gs_kt / 180.0))

    in_cloud_turb = 0.0
    for nd in spawn_nodes:
        d_nm = math.hypot((payload.lat - nd["lat"]) * 60.0, (payload.lon - nd["lon"]) * 60.0 * cos_lat)
        if d_nm <= 2.2 and (nd["base_ft"] - 300) <= ac_elev_ft <= (nd["top_ft"] + 300):
            base_bump = 0.32 if nd.get("has_cb") else 0.14
            in_cloud_turb = max(in_cloud_turb, base_bump * speed_penetration_factor)

    # 2. Clear Air Turbulence (Wind Shear gradient)
    cat_turb = min(0.12, max(0.0, (wind_spd_kt - 14.0) * 0.006))

    # 3. Low-level mechanical + gust turbulence (< 3,500 ft)
    low_level_factor = max(0.0, 1.0 - (ac_elev_ft / 3500.0)) if ac_elev_ft < 3500.0 else 0.0
    mech_turb = low_level_factor * (max(0.0, wind_spd_kt - 10.0) * 0.008 + gust_extra_kt * 0.015)

    # 4. Thermal turbulence (< 4,000 ft when OAT >= 30°C)
    thermal_factor = max(0.0, 1.0 - (ac_elev_ft / 4000.0)) if (ac_elev_ft < 4000.0 and temp_interp >= 30.0) else 0.0
    thermal_turb = thermal_factor * min(0.06, (temp_interp - 29.0) * 0.012)

    raw_turb = in_cloud_turb + cat_turb + mech_turb + thermal_turb
    acft_scale = float(XP11_STATE.get("aircraft_turb_scale", 1.0))
    max_cap = float(XP11_STATE.get("max_turb_cap", 0.50))
    final_turb = round(min(max_cap, raw_turb * acft_scale), 3)

    XP11_STATE["turbulence"] = final_turb
    XP11_STATE["spawn_nodes_count"] = len(spawn_nodes)

    return {
        "status": "ok",
        "aircraft": {
            "lat": XP11_STATE["lat"],
            "lon": XP11_STATE["lon"],
            "elev_ft": XP11_STATE["elev_ft"],
            "heading_deg": XP11_STATE["heading_deg"],
            "groundspeed_kt": XP11_STATE["groundspeed_kt"],
        },
        "nearest_station": nearest_st["icao"],
        "active_radius_nm": round(active_radius_nm, 2),
        "object_mode": XP11_STATE.get("object_mode", "red_box_50m"),
        "wind_dir_deg": round(wind_dir_deg, 1),
        "wind_speed_kt": round(wind_spd_kt, 1),
        "gust_extra_kt": round(gust_extra_kt, 1),
        "turbulence": final_turb,
        "qnh_hpa": round(qnh_interp, 1),
        "oat_c": round(temp_interp, 1),
        "spawn_nodes": spawn_nodes[:180],
    }


@app.get("/api/xplane/state")
async def get_xplane11_state():
    now = time.time()
    is_live = (now - float(XP11_STATE.get("updated_at", 0.0))) < 6.0
    return {**XP11_STATE, "connected": is_live}
