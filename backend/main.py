"""
AeroGrid 2D Weather Radar MVP — Phase 1 Backend Service (FastAPI)
- Automated METAR ingestion from NOAA AWC API (https://aviationweather.gov/api/data/metar)
- Regex METAR string parser (ICAO, Wind Vector, Visibility, QNH, Cloud Layers & Oktas 0-8)
- 1° x 1° Spatial Hash & 2D k-NN Inverse Distance Weighting (IDW) interpolation
- REST Endpoint: GET /api/weather/bounds?min_lat=...&max_lat=...&min_lon=...&max_lon=...
"""
import math
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(
    title="AeroGrid 2D Weather Radar Backend",
    description="Phase 1 METAR Ingestion, Spatial Grid Indexing (1x1 deg), and IDW Interpolation API",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

NOAA_AWC_METAR_URL = "https://aviationweather.gov/api/data/metar"

OKTAS_MAP = {
    "SKC": 0,
    "CLR": 0,
    "NSC": 0,
    "NCD": 0,
    "CAVOK": 0,
    "FEW": 2,  # FEW = 1-2 oktas
    "SCT": 4,  # SCT = 3-4 oktas
    "BKN": 6,  # BKN = 5-7 oktas
    "OVC": 8,  # OVC = 8 oktas
    "VV": 8,
}

FALLBACK_STATIONS = [
    {"icao": "WIII", "name": "Soekarno-Hatta Intl", "lat": -6.1256, "lon": 106.6560, "raw": "METAR WIII 271100Z 18004KT 140V220 6000 FEW020 29/24 Q1010 NOSIG"},
    {"icao": "WIHH", "name": "Halim Perdanakusuma Intl", "lat": -6.2670, "lon": 106.8903, "raw": "METAR WIHH 261730Z 00000KT 6000 SCT020 27/24 Q1011 NOSIG"},
    {"icao": "WICC", "name": "Husein Sastranegara Intl", "lat": -6.9006, "lon": 107.5760, "raw": "METAR WICC 270530Z 21004KT 7000 FEW020 26/23 Q1011 NOSIG"},
    {"icao": "WAHS", "name": "Jenderal Ahmad Yani Intl", "lat": -6.9707, "lon": 110.3732, "raw": "METAR WAHS 240800Z 30008KT 6000 -RA BKN017 31/24 Q1006 NOSIG"},
    {"icao": "WAHH", "name": "Adisutjipto Intl", "lat": -7.7882, "lon": 110.4320, "raw": "METAR WAHH 271130Z VRB01KT 2000 BR FEW018 25/23 Q1012 NOSIG"},
    {"icao": "WARR", "name": "Juanda Intl", "lat": -7.3798, "lon": 112.7870, "raw": "METAR WARR 270800Z 09008KT 9999 FEW020 31/26 Q1009 NOSIG"},
    {"icao": "WADD", "name": "I Gusti Ngurah Rai Intl", "lat": -8.7484, "lon": 115.1671, "raw": "METAR WADD 270000Z 15004KT 9999 SCT016 28/27 Q1013 NOSIG"},
    {"icao": "WADS", "name": "Sultan Muhammad Kaharuddin III", "lat": -8.48904, "lon": 117.4120, "raw": "METAR WADS 270600Z 12007KT 9999 FEW019 31/24 Q1010 NOSIG"},
    {"icao": "WAAA", "name": "Sultan Hasanuddin Intl", "lat": -5.0755, "lon": 119.5537, "raw": "METAR WAAA 261430Z VRB02KT 5000 FU FEW020 27/25 Q1013 NOSIG"},
    {"icao": "WAOO", "name": "Syamsudin Noor Intl", "lat": -3.4401, "lon": 114.7612, "raw": "METAR WAOO 230800Z 20013KT 7000 FEW020 33/23 Q1005 NOSIG"},
    {"icao": "WALL", "name": "Sultan Aji Muhammad Sulaiman", "lat": -1.26834, "lon": 116.89452, "raw": "METAR WALL 260200Z 36005KT 340V050 4000 FU FEW020 32/25 Q1012 NOSIG"},
    {"icao": "WIOO", "name": "Supadio Intl", "lat": -0.1523, "lon": 109.4045, "raw": "METAR WIOO 271230Z 34003KT 5000 HZ SCT012 27/26 Q1011 NOSIG"},
    {"icao": "WIPP", "name": "Sultan Mahmud Badaruddin II", "lat": -2.8977, "lon": 104.6981, "raw": "METAR WIPP 231430Z 11009KT 1400 FU SCT015 30/24 Q1010 NOSIG"},
    {"icao": "WSSS", "name": "Singapore Changi Intl", "lat": 1.3502, "lon": 103.9940, "raw": "METAR WSSS 271130Z 04008KT 9000 FEW018 BKN300 30/24 Q1010 NOSIG"},
    {"icao": "WMKK", "name": "Kuala Lumpur Intl", "lat": 2.7456, "lon": 101.7100, "raw": "METAR WMKK 270330Z VRB01KT 7000 -SHRA FEW010 FEW017CB SCT018 28/26 Q1013 RESHRA NOSIG"},
    {"icao": "WIMM", "name": "Kualanamu Intl", "lat": 3.6378, "lon": 98.8706, "raw": "METAR WIMM 240530Z 33010KT 9000 SCT018 31/25 Q1008 NOSIG"},
]


def parse_metar_string(raw: str, icao: str, lat: float, lon: float, name: str = "") -> Dict:
    clean = (raw or "").strip().upper()

    # Wind vector extraction
    wind_m = re.search(r"\b(VRB|\d{3})(\d{2,3})(?:G(\d{2,3}))?(KT|MPS)\b", clean)
    is_vrb = bool(wind_m and wind_m.group(1) == "VRB")
    wind_dir = 0 if (not wind_m or is_vrb) else int(wind_m.group(1))
    factor = 1.94384 if (wind_m and wind_m.group(4) == "MPS") else 1.0
    wind_spd = round(int(wind_m.group(2)) * factor) if wind_m else 0
    wind_gust = round(int(wind_m.group(3)) * factor) if (wind_m and wind_m.group(3)) else None

    move_rad = math.radians((wind_dir + 180) % 360)
    u_kt = wind_spd * math.sin(move_rad)
    v_kt = wind_spd * math.cos(move_rad)

    # Visibility extraction (meters)
    if "CAVOK" in clean:
        vis_m = 10000
    else:
        vis_match = re.search(r"\b(?:KT|MPS|\d{3}V\d{3})\s+(\d{4})\b", clean)
        vis_m = int(vis_match.group(1)) if vis_match else 9999

    # QNH extraction (hPa)
    qnh_match = re.search(r"\bQ(\d{4})\b", clean)
    alt_match = re.search(r"\bA(\d{4})\b", clean)
    if qnh_match:
        qnh_hpa = int(qnh_match.group(1))
    elif alt_match:
        qnh_hpa = round((int(alt_match.group(1)) / 100.0) * 33.8639)
    else:
        qnh_hpa = 1013

    # Cloud layers & oktas (0 - 8)
    clouds = []
    max_oktas = 0
    lowest_base_ft: Optional[int] = None
    for m in re.finditer(r"\b(FEW|SCT|BKN|OVC|VV)(\d{3})(CB|TCU)?\b", clean):
        ctype = m.group(1)
        base_ft = int(m.group(2)) * 100
        conv = m.group(3)
        oktas = OKTAS_MAP.get(ctype, 0)
        clouds.append({"type": ctype, "base_ft": base_ft, "convective": conv, "oktas": oktas})
        if oktas > max_oktas:
            max_oktas = oktas
        if lowest_base_ft is None or base_ft < lowest_base_ft:
            lowest_base_ft = base_ft

    tau = max(0.0, min(1.0, 1.0 - (max_oktas / 8.0)))

    return {
        "icao": icao,
        "name": name or icao,
        "lat": lat,
        "lon": lon,
        "grid_lat": math.floor(lat),
        "grid_lon": math.floor(lon),
        "raw": clean,
        "wind_dir": wind_dir,
        "wind_speed_kt": wind_spd,
        "wind_gust_kt": wind_gust,
        "u_kt": round(u_kt, 3),
        "v_kt": round(v_kt, 3),
        "visibility_m": vis_m,
        "qnh_hpa": qnh_hpa,
        "clouds": clouds,
        "oktas": max_oktas,
        "lowest_base_ft": lowest_base_ft or 2000,
        "tau": round(tau, 4),
    }


def build_spatial_grid_and_idw(
    min_lat: int,
    max_lat: int,
    min_lon: int,
    max_lon: int,
    stations: List[Dict],
    k: int = 4,
    power: float = 2.0,
) -> List[Dict]:
    # Spatial hash indexing stations into 1x1 deg buckets
    spatial_hash: Dict[str, List[Dict]] = {}
    for s in stations:
        key = f"{s['grid_lat']},{s['grid_lon']}"
        spatial_hash.setdefault(key, []).append(s)

    grid_cells = []
    for lat_idx in range(min_lat, max_lat):
        for lon_idx in range(min_lon, max_lon):
            key = f"{lat_idx},{lon_idx}"
            center_lat = lat_idx + 0.5
            center_lon = lon_idx + 0.5
            direct = spatial_hash.get(key, [])

            if direct:
                n = len(direct)
                oktas = sum(s["oktas"] for s in direct) / n
                u_kt = sum(s["u_kt"] for s in direct) / n
                v_kt = sum(s["v_kt"] for s in direct) / n
                qnh = round(sum(s["qnh_hpa"] for s in direct) / n)
                vis = round(sum(s["visibility_m"] for s in direct) / n)
                base_ft = round(sum(s["lowest_base_ft"] for s in direct) / n)
                mode = "direct_station"
                contributors = [
                    {"icao": s["icao"], "distance_km": 0.0, "weight": round(1.0 / n, 3)}
                    for s in direct
                ]
            else:
                # Inverse Distance Weighting (IDW) over k nearest stations
                dists = []
                for s in stations:
                    dlat = center_lat - s["lat"]
                    dlon = (center_lon - s["lon"]) * math.cos(math.radians(center_lat))
                    dist_deg = max(0.05, math.hypot(dlat, dlon))
                    dists.append((dist_deg, dist_deg * 111.32, s))
                dists.sort(key=lambda x: x[0])
                nearest = dists[: min(k, len(dists))]

                raw_weights = [(1.0 / (d_deg ** power), d_km, s) for d_deg, d_km, s in nearest]
                w_sum = sum(w for w, _, _ in raw_weights) or 1.0

                oktas = sum((w / w_sum) * s["oktas"] for w, _, s in raw_weights)
                u_kt = sum((w / w_sum) * s["u_kt"] for w, _, s in raw_weights)
                v_kt = sum((w / w_sum) * s["v_kt"] for w, _, s in raw_weights)
                qnh = round(sum((w / w_sum) * s["qnh_hpa"] for w, _, s in raw_weights))
                vis = round(sum((w / w_sum) * s["visibility_m"] for w, _, s in raw_weights))
                base_ft = round(sum((w / w_sum) * s["lowest_base_ft"] for w, _, s in raw_weights))
                mode = f"idw_k{len(nearest)}"
                contributors = [
                    {"icao": s["icao"], "distance_km": round(d_km, 1), "weight": round(w / w_sum, 3)}
                    for w, d_km, s in raw_weights
                ]

            oktas = max(0.0, min(8.0, oktas))
            tau = max(0.0, min(1.0, 1.0 - (oktas / 8.0)))
            spd_kt = math.hypot(u_kt, v_kt)
            move_deg = (math.degrees(math.atan2(u_kt, v_kt)) + 360.0) % 360.0
            wind_dir = round((move_deg + 180.0) % 360.0)

            grid_cells.append({
                "grid_id": key,
                "bounds": {"min_lat": lat_idx, "max_lat": lat_idx + 1, "min_lon": lon_idx, "max_lon": lon_idx + 1},
                "center": {"lat": center_lat, "lon": center_lon},
                "interpolation_mode": mode,
                "oktas": round(oktas, 3),
                "threshold_tau": round(tau, 4),
                "wind_vector": {
                    "dir_deg": wind_dir,
                    "speed_kt": round(spd_kt, 2),
                    "u_kt": round(u_kt, 2),
                    "v_kt": round(v_kt, 2),
                },
                "qnh_hpa": qnh,
                "visibility_m": vis,
                "cloud_base_ft": base_ft,
                "contributors": contributors,
            })

    return grid_cells


@app.get("/api/weather/bounds")
async def get_weather_bounds(
    min_lat: int = Query(-9, description="Minimum latitude (deg)"),
    max_lat: int = Query(4, description="Maximum latitude (deg)"),
    min_lon: int = Query(98, description="Minimum longitude (deg)"),
    max_lon: int = Query(120, description="Maximum longitude (deg)"),
    k: int = Query(4, ge=2, le=8, description="Number of nearest stations for IDW interpolation"),
    power: float = Query(2.0, ge=1.0, le=4.0, description="IDW distance exponent p"),
):
    bbox = f"{min_lat},{min_lon},{max_lat},{max_lon}"
    stations: List[Dict] = []

    try:
        async with httpx.AsyncClient(timeout=8.0, headers={"User-Agent": "AeroGridWeatherMVP/1.0"}) as client:
            resp = await client.get(NOAA_AWC_METAR_URL, params={"bbox": bbox, "format": "json"})
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    for item in payload:
                        icao = item.get("icaoId") or item.get("station_id")
                        raw = item.get("rawOb") or item.get("raw_text")
                        lat = item.get("lat")
                        lon = item.get("lon")
                        if icao and raw and lat is not None and lon is not None:
                            stations.append(
                                parse_metar_string(raw, icao, float(lat), float(lon), item.get("name", icao))
                            )
    except Exception:
        stations = []

    if not stations:
        stations = [
            parse_metar_string(s["raw"], s["icao"], s["lat"], s["lon"], s["name"])
            for s in FALLBACK_STATIONS
        ]

    grid_cells = build_spatial_grid_and_idw(min_lat, max_lat, min_lon, max_lon, stations, k=k, power=power)

    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "bounds": {"min_lat": min_lat, "max_lat": max_lat, "min_lon": min_lon, "max_lon": max_lon},
        "idw_config": {"k_neighbors": k, "power_p": power},
        "stations_count": len(stations),
        "grid_cells_count": len(grid_cells),
        "stations": stations,
        "grid_cells": grid_cells,
    }
