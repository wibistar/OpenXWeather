"""
OpenXWeather X-Plane 11 Bridge Plugin (XPPython3 — v5.0)
Target Simulator: X-Plane 11 ONLY (Uses XPLMInstance + XPLMWorldToLocal + sim/weather/*)

What's New in v5.0:
1. Fixed 50x50x50m Red Box (`debug_red_box_50m.obj`) with 6-face outward normals & red emission:
   - Never disappears when approached (no 2m size swap).
   - Never looks black or shadowed from afar.
2. Clean 1-to-1 World-Locked Dispersion (Zero 5-Box Bunching):
   - Each spawn point from the GUI renders a single vertical column filled evenly from `base_m` to `top_m`.
3. Master GUI Reference & 4-Factor Smooth Turbulence:
   - Uses the exact cloud heights (`Cloud Base` & `Cloud Top`) and 4-factor turbulence from the Browser GUI,
     scaled by the Flight Plan Aircraft Category and clamped by the Manual Max Turbulence Slider.
"""
import os
import json
import threading
import urllib.request
import xp

SERVER_SYNC_URL = "http://127.0.0.1:8000/api/xplane/sync"


class PythonInterface:
    def XPluginStart(self):
        self.Name = "OpenXWeather XP11 Bridge v5.0"
        self.Sig = "wibi.openxweather.xp11"
        self.Desc = "50x50m World-Locked Cloud Spawner & 4-Factor Turbulence Injector for X-Plane 11"

        # 1. Aircraft Position & Attitude DataRefs (X-Plane 11)
        self.dr_lat = xp.findDataRef("sim/flightmodel/position/latitude")
        self.dr_lon = xp.findDataRef("sim/flightmodel/position/longitude")
        self.dr_elev_m = xp.findDataRef("sim/flightmodel/position/elevation")
        self.dr_psi = xp.findDataRef("sim/flightmodel/position/psi")
        self.dr_gs_ms = xp.findDataRef("sim/flightmodel/position/groundspeed")

        # 2. X-Plane 11 Weather, Wind, Shear, Turbulence, QNH & Temperature DataRefs
        self.dr_use_real_wx = xp.findDataRef("sim/weather/use_real_weather_bool")
        self.dr_wind_alt_m = [xp.findDataRef(f"sim/weather/wind_altitude_msl_m[{i}]") for i in range(3)]
        self.dr_wind_dir = [xp.findDataRef(f"sim/weather/wind_direction_degt[{i}]") for i in range(3)]
        self.dr_wind_spd = [xp.findDataRef(f"sim/weather/wind_speed_kt[{i}]") for i in range(3)]
        self.dr_shear_spd = [xp.findDataRef(f"sim/weather/shear_speed_kt[{i}]") for i in range(3)]
        self.dr_shear_dir = [xp.findDataRef(f"sim/weather/shear_direction_degt[{i}]") for i in range(3)]
        self.dr_turb = [xp.findDataRef(f"sim/weather/turbulence[{i}]") for i in range(3)]
        self.dr_qnh_inhg = xp.findDataRef("sim/weather/barometer_sealevel_inhg")
        self.dr_temp_sl_c = xp.findDataRef("sim/weather/temperature_sealevel_c")
        self.dr_cloud_type = [xp.findDataRef(f"sim/weather/cloud_type[{i}]") for i in range(3)]

        # 3. Load 50x50m Red Box & Cloud Puff Billboard
        plugin_dir = os.path.dirname(os.path.realpath(__file__))
        obj_dir = os.path.join(plugin_dir, "objects")
        p_50m = os.path.join(obj_dir, "debug_red_box_50m.obj")
        p_25m = os.path.join(obj_dir, "debug_red_box_25m.obj")
        p_puff = os.path.join(obj_dir, "xp11_cloud_puff.obj")

        self.obj_red_50m = xp.loadObject(p_50m) if os.path.exists(p_50m) else (
            xp.loadObject(p_25m) if os.path.exists(p_25m) else None
        )
        self.obj_puff = xp.loadObject(p_puff) if os.path.exists(p_puff) else self.obj_red_50m

        self.active_instances = []
        self.latest_sync = None
        self.smoothed_turb = 0.0
        self.lock = threading.Lock()

        xp.registerFlightLoopCallback(self.flightLoopCallback, 1.0, 0)
        return self.Name, self.Sig, self.Desc

    def _background_http_sync(self, payload):
        try:
            req = urllib.request.Request(
                SERVER_SYNC_URL,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=1.5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                with self.lock:
                    self.latest_sync = data
        except Exception:
            pass

    def flightLoopCallback(self, elapsedSinceLastCall, elapsedTimeSinceLastFlightLoop, counter, refcon):
        lat = xp.getDataf(self.dr_lat)
        lon = xp.getDataf(self.dr_lon)
        elev_m = xp.getDataf(self.dr_elev_m)
        heading = xp.getDataf(self.dr_psi)
        gs_kt = xp.getDataf(self.dr_gs_ms) * 1.94384

        payload = {
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            "elev_ft": round(elev_m * 3.28084, 1),
            "heading_deg": round(heading, 1),
            "groundspeed_kt": round(gs_kt, 1),
        }
        threading.Thread(target=self._background_http_sync, args=(payload,), daemon=True).start()

        with self.lock:
            sync_data = self.latest_sync
            self.latest_sync = None

        if sync_data:
            self._apply_weather_and_instances(sync_data)

        return 1.5

    def _apply_weather_and_instances(self, data):
        # 1. Disable default real weather & default 2D cloud layers so GUI 3D cloud nodes are the single reference
        if self.dr_use_real_wx:
            xp.setDatai(self.dr_use_real_wx, 0)
        for dr_ct in self.dr_cloud_type:
            if dr_ct:
                xp.setDatai(dr_ct, 0)

        w_dir = float(data.get("wind_dir_deg", 90.0))
        w_spd = float(data.get("wind_speed_kt", 8.0))
        gust_extra_kt = float(data.get("gust_extra_kt", 0.0))
        target_turb = float(data.get("turbulence", 0.02))
        qnh_hpa = float(data.get("qnh_hpa", 1011.0))
        oat_c = float(data.get("oat_c", 28.0))

        # Smooth turbulence transitions so aircraft never gets a sudden violent jolt
        self.smoothed_turb = round(0.70 * self.smoothed_turb + 0.30 * target_turb, 4)

        if self.dr_qnh_inhg:
            xp.setDataf(self.dr_qnh_inhg, qnh_hpa * 0.02953)
        if self.dr_temp_sl_c:
            xp.setDataf(self.dr_temp_sl_c, oat_c)

        altitudes_m = [600.0, 3000.0, 9000.0]
        speed_factors = [1.0, 1.20, 1.45]
        for i in range(3):
            if self.dr_wind_alt_m[i]:
                xp.setDataf(self.dr_wind_alt_m[i], altitudes_m[i])
            if self.dr_wind_dir[i]:
                xp.setDataf(self.dr_wind_dir[i], (w_dir + i * 4.0) % 360.0)
            if self.dr_wind_spd[i]:
                xp.setDataf(self.dr_wind_spd[i], w_spd * speed_factors[i])
            if self.dr_shear_spd[i]:
                xp.setDataf(self.dr_shear_spd[i], gust_extra_kt if i == 0 else gust_extra_kt * 0.4)
            if self.dr_shear_dir[i]:
                xp.setDataf(self.dr_shear_dir[i], 8.0 if gust_extra_kt > 2.0 else 0.0)
            if self.dr_turb[i]:
                xp.setDataf(self.dr_turb[i], self.smoothed_turb)

        # 2. Rebuild 3D Cloud / 50x50m Red Box Instances at exact World-Locked (Lat, Lon, Alt_m)
        for inst in self.active_instances:
            xp.destroyInstance(inst)
        self.active_instances.clear()

        mode = data.get("object_mode", "red_box_50m")
        target_obj = self.obj_puff if mode == "cloud_puff" else self.obj_red_50m
        if not target_obj:
            return

        # Clean 1-to-1 Dispersion: 1 GUI dot = 1 column at (n_lat, n_lon), filled evenly from base_m to top_m
        for node in data.get("spawn_nodes", []):
            n_lat = float(node["lat"])
            n_lon = float(node["lon"])
            base_m = float(node["base_m"])
            top_m = float(node["top_m"])
            layers = max(1, int(node.get("vertical_layers", 2)))

            for k in range(layers):
                frac = 0.0 if layers == 1 else (k / float(layers - 1))
                alt_m = base_m + (top_m - base_m) * frac
                x, y, z = xp.worldToLocal(n_lat, n_lon, alt_m)
                inst = xp.createInstance(target_obj, [])
                xp.instanceSetPosition(inst, (x, y, z, 0.0, 0.0, 0.0), [])
                self.active_instances.append(inst)

    def XPluginStop(self):
        for inst in self.active_instances:
            xp.destroyInstance(inst)
        self.active_instances.clear()

    def XPluginEnable(self):
        return 1

    def XPluginDisable(self):
        pass

    def XPluginReceiveMessage(self, inFromWho, inMessage, inParam):
        pass
