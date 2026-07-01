#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Platypus – Redfish Control Panel (PyQt5)
"""

import os
import sys
import json
import time
import traceback
import webbrowser
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests
from requests.auth import HTTPBasicAuth

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal, QThread, QTimer
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTabWidget, QTextEdit, QCheckBox, QFileDialog, QComboBox, QDoubleSpinBox,
    QSpinBox
)

# Matplotlib for charts
from matplotlib.figure import Figure
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

# Import our new UI module
from virtual_media_tab import VirtualMediaTab
from firmware_tab import FirmwareTab
# ---------------------------
# Backend client
# ---------------------------

@dataclass
class RFConfig:
    scheme: str = "https"        # "http" or "https"
    host: str = ""               # BMC IP/hostname
    port: Optional[int] = None   # None = default port for scheme
    username: str = "root"
    password: str = "0penBmc"
    verify: bool = False         # False == curl -k
    timeout: float = 8.0
    use_session: bool = False    # use Redfish SessionService token


class RedfishClient:
    def __init__(self, cfg: RFConfig, log_fn=None):
        self.cfg = cfg
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json"})
        self.auth = HTTPBasicAuth(cfg.username, cfg.password)
        self.token: Optional[str] = None
        self.log = log_fn or (lambda *_: None)

    # ---- URL helpers ----
    def base_url(self) -> str:
        host = self.cfg.host.strip().rstrip("/")
        if self.cfg.port:
            return f"{self.cfg.scheme}://{host}:{self.cfg.port}"
        return f"{self.cfg.scheme}://{host}"

    def url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.base_url()}{path}"

    # ---- SessionService (optional) ----
    def login(self) -> Tuple[bool, Any, int]:
        self.log("POST /redfish/v1/SessionService/Sessions (login)")
        try:
            r = self.session.post(
                self.url("/redfish/v1/SessionService/Sessions"),
                json={"UserName": self.cfg.username, "Password": self.cfg.password},
                timeout=self.cfg.timeout,
                verify=self.cfg.verify,
            )
            token = r.headers.get("X-Auth-Token")
            ok = r.status_code in (200, 201) and bool(token)
            if ok:
                self.token = token
                self.session.headers.update({"X-Auth-Token": token})
                self.auth = None  # token replaces basic auth
            data = {"status": r.status_code, "data": self._maybe_json(r)}
            return ok, data, r.status_code
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def logout(self) -> None:
        self.session.headers.pop("X-Auth-Token", None)
        self.token = None

    # ---- HTTP helpers ----
    def _maybe_json(self, r: requests.Response) -> Any:
        ct = r.headers.get("content-type", "")
        try:
            return r.json() if "application/json" in ct else r.text
        except Exception:
            return r.text

    def _parse(self, r: requests.Response) -> Tuple[bool, Any, int]:
        data = self._maybe_json(r)
        ok = r.ok or r.status_code in (200, 201, 202, 204)
        return ok, {"status": r.status_code, "data": data}, r.status_code

    def get(self, path: str) -> Tuple[bool, Any, int]:
        self.log(f"GET {path}")
        try:
            r = self.session.get(self.url(path), auth=self.auth, timeout=self.cfg.timeout, verify=self.cfg.verify)
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def head(self, path: str) -> Tuple[bool, Any, int]:
        self.log(f"HEAD {path}")
        try:
            r = self.session.head(
                self.url(path), auth=self.auth, timeout=self.cfg.timeout,
                verify=self.cfg.verify, allow_redirects=True
            )
            return True, {"status": r.status_code, "data": dict(r.headers)}, r.status_code
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def post_json(self, path: str, payload: Dict[str, Any]) -> Tuple[bool, Any, int]:
        self.log(f"POST {path} {payload}")
        headers = {"Content-Type": "application/json"}
        try:
            r = self.session.post(
                self.url(path), json=payload, headers=headers, auth=self.auth,
                timeout=self.cfg.timeout, verify=self.cfg.verify
            )
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def delete(self, path: str) -> Tuple[bool, Any, int]:
        self.log(f"DELETE {path}")
        try:
            r = self.session.delete(self.url(path), auth=self.auth, timeout=self.cfg.timeout, verify=self.cfg.verify)
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    # ---- Firmware helpers (octet-stream) ----
    def put_octet_stream(self, path: str, file_bytes: bytes) -> Tuple[bool, Any, int]:
        self.log(f"PUT {path} (application/octet-stream, {len(file_bytes)} bytes)")
        headers = {"Content-Type": "application/octet-stream"}
        try:
            r = self.session.put(
                self.url(path), data=file_bytes, headers=headers, auth=self.auth,
                timeout=self.cfg.timeout, verify=self.cfg.verify
            )
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def post_octet_stream(self, path: str, file_bytes: bytes) -> Tuple[bool, Any, int]:
        self.log(f"POST {path} (application/octet-stream, {len(file_bytes)} bytes)")
        headers = {"Content-Type": "application/octet-stream"}
        try:
            r = self.session.post(
                self.url(path), data=file_bytes, headers=headers,
                auth=self.auth, timeout=self.cfg.timeout, verify=self.cfg.verify
            )
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def post_multipart(self, path: str, file_bytes: bytes, filename: str) -> Tuple[bool, Any, int]:
        self.log(f"POST {path} (multipart, {len(file_bytes)} bytes)")
        try:
            files = {"UpdateFile": (filename, file_bytes)}
            r = self.session.post(
                self.url(path), files=files, auth=self.auth,
                timeout=self.cfg.timeout, verify=self.cfg.verify
            )
            return self._parse(r)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}", 0

    def put_local_file_best_effort(self, file_bytes: bytes, filename: str = "image.bin"):
        headers_octet = {
            "Content-Type": "application/octet-stream",
            "Accept": "*/*",
            "Expect": "",           
            "Connection": "close",  
        }
        auth = HTTPBasicAuth(self.cfg.username, self.cfg.password)

        trace = []
        def _rec(step, method, url, resp):
            try:
                is_json = "application/json" in resp.headers.get("content-type", "").lower()
                body = resp.json() if is_json else resp.text
            except Exception:
                body = resp.text
            trace.append({"step": step, "method": method, "url": url, "status": resp.status_code})
            ok = resp.ok or resp.status_code in (200, 201, 202, 204)
            return ok, {"status": resp.status_code, "data": body, "used": {"method": method, "url": url}, "trace": trace}, resp.status_code

        http_uri = None
        mp_uri = None
        ok_us, svc, _ = self.get("/redfish/v1/UpdateService")
        if ok_us and isinstance(svc, dict) and isinstance(svc.get("data"), dict):
            body = svc["data"]
            if isinstance(body.get("HttpPushUri"), str) and body["HttpPushUri"].strip():
                http_uri = body["HttpPushUri"].strip()
            if isinstance(body.get("MultipartHttpPushUri"), str) and body["MultipartHttpPushUri"].strip():
                mp_uri = body["MultipartHttpPushUri"].strip()

        candidates_put = []
        candidates_post = []
        candidates_mp = []

        if http_uri:
            candidates_put += [self.url(http_uri)]
            if not http_uri.endswith("/"):
                candidates_put += [self.url(http_uri + "/")]
            candidates_post = list(candidates_put)  
        if mp_uri:
            candidates_mp += [self.url(mp_uri)]
            if not mp_uri.endswith("/"):
                candidates_mp += [self.url(mp_uri + "/")]

        for u in candidates_put:
            try:
                r = requests.put(u, data=file_bytes, headers=headers_octet, auth=auth,
                                timeout=self.cfg.timeout, verify=self.cfg.verify, allow_redirects=False)
                ok, data, code = _rec("PUT HttpPushUri", "PUT", u, r)
                if ok: return ok, data, code
            except Exception as e:
                trace.append({"step": "PUT HttpPushUri EXC", "url": u, "error": f"{type(e).__name__}: {e}"})

        for u in candidates_post:
            try:
                r = requests.post(u, data=file_bytes, headers=headers_octet, auth=auth,
                                timeout=self.cfg.timeout, verify=self.cfg.verify, allow_redirects=False)
                ok, data, code = _rec("POST HttpPushUri", "POST", u, r)
                if ok: return ok, data, code
            except Exception as e:
                trace.append({"step": "POST HttpPushUri EXC", "url": u, "error": f"{type(e).__name__}: {e}"})

        for u in candidates_mp:
            try:
                files = {"UpdateFile": (filename, file_bytes)}
                r = requests.post(u, files=files, auth=auth,
                                timeout=self.cfg.timeout, verify=self.cfg.verify, allow_redirects=False)
                ok = r.ok or r.status_code in (200, 201, 202, 204)
                is_json = "application/json" in r.headers.get("content-type", "").lower()
                body = r.json() if is_json else r.text
                trace.append({"step": "POST MultipartHttpPushUri", "method": "POST", "url": u, "status": r.status_code})
                if ok:
                    return True, {"status": r.status_code, "data": body, "used": {"method": "POST", "url": u}, "trace": trace}, r.status_code
            except Exception as e:
                trace.append({"step": "POST Multipart EXC", "url": u, "error": f"{type(e).__name__}: {e}"})

        for u in [self.url("/redfish/v1/UpdateService"), self.url("/redfish/v1/UpdateService/")]:
            try:
                r = requests.put(u, data=file_bytes, headers=headers_octet, auth=auth,
                                timeout=self.cfg.timeout, verify=self.cfg.verify, allow_redirects=False)
                ok, data, code = _rec("PUT /UpdateService (legacy)", "PUT", u, r)
                if ok: return ok, data, code
            except Exception as e:
                trace.append({"step": "PUT legacy EXC", "url": u, "error": f"{type(e).__name__}: {e}"})

        return False, {"status": 405, "data": {"error": {"message": "All local-file methods rejected"}}, "trace": trace}, 405

    def simple_update(self, target: str, image_uri: str) -> Tuple[bool, Any, int]:
        self.log(f"POST {target} (SimpleUpdate ImageURI={image_uri})")
        payload = {"ImageURI": image_uri}
        return self.post_json(target, payload)

    def get_update_service(self) -> Tuple[bool, Any, int]:
        return self.get("/redfish/v1/UpdateService")

    def put_firmware_octet(self, file_bytes: bytes) -> Tuple[bool, Any, int]:
        return self.put_octet_stream("/redfish/v1/UpdateService", file_bytes)

    # ---- Virtual Media helpers ----
    def get_virtual_media(self) -> Tuple[bool, Any, int]:
        """Discover Virtual Media endpoints."""
        ok, data, code = self.get("/redfish/v1/Managers")
        if not ok or not isinstance(data.get("data"), dict):
            return False, {"error": "Failed to get Managers"}, code
        
        members = data["data"].get("Members", [])
        vm_endpoints = []
        for m in members:
            mgr_id = m.get("@odata.id")
            if mgr_id:
                vm_url = f"{mgr_id}/VirtualMedia"
                ok_vm, vm_data, _ = self.get(vm_url)
                if ok_vm and isinstance(vm_data.get("data"), dict):
                    for v in vm_data["data"].get("Members", []):
                        v_id = v.get("@odata.id")
                        if v_id:
                            vm_endpoints.append(v_id)
        
        if not vm_endpoints:
            return False, {"error": "No VirtualMedia endpoints found"}, 404
            
        return True, {"endpoints": vm_endpoints}, 200

    def get_virtual_media_status(self, endpoint: str) -> Tuple[bool, Any, int]:
        return self.get(endpoint)

    def insert_virtual_media(self, endpoint: str, image_url: str, user: str = "", password: str = "") -> Tuple[bool, Any, int]:
        payload = {"Image": image_url, "Inserted": True}
        if user:
            payload["UserName"] = user
        if password:
            payload["Password"] = password
        return self.post_json(f"{endpoint}/Actions/VirtualMedia.InsertMedia", payload)

    def eject_virtual_media(self, endpoint: str) -> Tuple[bool, Any, int]:
        return self.post_json(f"{endpoint}/Actions/VirtualMedia.EjectMedia", {})

    # ---- Quick probe ----
    def test_connection(self) -> Tuple[bool, Any, int]:
        ok, data, code = self.head("/redfish/v1")
        if ok and code in (200, 204, 405):
            return True, data, code
        return self.get("/redfish/v1")

    # ---- Sensors discovery helpers ----
    def discover_sensor_paths(self) -> List[str]:
        paths: List[str] = []

        ok, data, _ = self.get("/redfish/v1/Chassis/chassis/Sensors")
        if ok and isinstance(data, dict) and isinstance(data.get("data"), dict):
            members = data["data"].get("Members") or []
            for m in members:
                p = m.get("@odata.id") if isinstance(m, dict) else None
                if isinstance(p, str):
                    paths.append(p)

        ok, data, _ = self.get("/redfish/v1/Chassis")
        if ok and isinstance(data, dict) and isinstance(data.get("data"), dict):
            members = data["data"].get("Members") or []
            for m in members:
                ch = m.get("@odata.id") if isinstance(m, dict) else None
                if not isinstance(ch, str):
                    continue
                ok2, d2, _ = self.get(f"{ch}/Sensors")
                if ok2 and isinstance(d2, dict) and isinstance(d2.get("data"), dict):
                    for mm in d2["data"].get("Members") or []:
                        p = mm.get("@odata.id") if isinstance(mm, dict) else None
                        if isinstance(p, str) and p not in paths:
                            paths.append(p)
                else:
                    ok3, th, _ = self.get(f"{ch}/Thermal")
                    if ok3 and isinstance(th, dict) and isinstance(th.get("data"), dict):
                        temps = th["data"].get("Temperatures") or []
                        for i, _t in enumerate(temps):
                            paths.append(f"{ch}/Thermal#Temperatures/{i}")

        ok, data, _ = self.get("/redfish/v1/Systems/system/Sensors")
        if ok and isinstance(data, dict) and isinstance(data.get("data"), dict):
            for m in data["data"].get("Members") or []:
                p = m.get("@odata.id") if isinstance(m, dict) else None
                if isinstance(p, str) and p not in paths:
                    paths.append(p)

        seen = set(); dedup: List[str] = []
        for p in paths:
            if p not in seen:
                seen.add(p); dedup.append(p)
        return dedup

    def read_sensor(self, path: str) -> Optional[Dict[str, Any]]:
        if "#Temperatures/" in path:
            chassis_path, idx = path.split("#Temperatures/")
            ok, d, _ = self.get(f"{chassis_path}/Thermal")
            if not ok or not isinstance(d, dict) or not isinstance(d.get("data"), dict):
                return None
            temps = d["data"].get("Temperatures") or []
            try:
                t = temps[int(idx)]
            except Exception:
                return None
            name = t.get("Name") or t.get("SensorName") or f"Temp{idx}"
            val = t.get("ReadingCelsius") or t.get("Reading") or t.get("Value")
            units = "C"
            if isinstance(val, (int, float)):
                return {"name": str(name), "reading": float(val), "units": units, "path": path}
            return None

        ok, data, _ = self.get(path)
        if not ok or not isinstance(data, dict):
            return None
        body = data.get("data")
        if not isinstance(body, dict):
            return None

        name = body.get("Name") or body.get("Id") or body.get("SensorName") or "Sensor"
        units = body.get("ReadingUnits") or body.get("Units") or body.get("PhysicalContext") or ""

        for key in ("Reading", "ReadingCelsius", "ReadingVolts", "ReadingWatts", "ReadingAmps", "ReadingPercent"):
            if key in body:
                try:
                    val = float(body[key])
                    if key == "ReadingCelsius": units = units or "C"
                    elif key == "ReadingVolts": units = units or "V"
                    elif key == "ReadingWatts": units = units or "W"
                    elif key == "ReadingAmps": units = units or "A"
                    elif key == "ReadingPercent": units = units or "%"
                    return {"name": str(name), "reading": val, "units": units, "path": path}
                except Exception:
                    pass

        sr = body.get("SensorReading")
        if isinstance(sr, (int, float)):
            return {"name": str(name), "reading": float(sr), "units": units, "path": path}
        return None


# ---------------------------
# Worker threads
# ---------------------------

class RFWorker(QThread):
    finished = pyqtSignal(object, object)  # (success: bool, result: Any)
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
    def run(self):
        try:
            ok, data, _status = self.fn(*self.args, **self.kwargs)
            self.finished.emit(ok, data)
        except Exception as e:
            self.finished.emit(False, f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class MultiUpdateWorker(QThread):
    finished = pyqtSignal(str, bool, object)  # (host_label, success, result)

    def __init__(self, host: str, port: Optional[int],
                 scheme: str, username: str, password: str, verify: bool,
                 timeout: float, file_bytes: bytes, filename: str, mode: str = "auto"):
        super().__init__()
        self.host = host
        self.port = port
        self.scheme = scheme
        self.username = username
        self.password = password
        self.verify = verify
        self.timeout = timeout
        self.file_bytes = file_bytes
        self.filename = filename
        self.mode = mode

    def run(self):
        label = f"{self.scheme}://{self.host}{(':'+str(self.port)) if self.port else ''}"
        try:
            cfg = RFConfig(
                scheme=self.scheme, host=self.host, port=self.port,
                username=self.username, password=self.password,
                verify=self.verify, timeout=self.timeout, use_session=False
            )
            client = RedfishClient(cfg)
            ok, data, _ = client.put_local_file_best_effort(self.file_bytes, getattr(self, "filename", "image.bin"))
            self.finished.emit(label, ok, data)
        except Exception as e:
            self.finished.emit(label, False, f"{type(e).__name__}: {e}")



# ---------------------------
# UI Helpers
# ---------------------------

def pretty(obj: Any) -> str:
    try:
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, indent=2)
        if isinstance(obj, str):
            return json.dumps(json.loads(obj), indent=2)
    except Exception:
        pass
    return str(obj)


# ---------------------------
# Main Window
# ---------------------------

class MainWindow(QMainWindow):
    BLUE = "#1F6BA6"        # accent color
    BLUE_HOVER = "#2B86CF"
    DARK_BG = "#1D1E1E"     # app background
    CARD_BG = "#2B2D31"
    FIELD_BG = "#202225"
    TEXT = "#EAEAEA"
    MUTED = "#9AA0A6"
    DANGER = "#C92A2A"

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Platypus – Redfish Control Panel (PyQt5)")
        self.resize(1280, 860)
        self._apply_theme()

        self.client: Optional[RedfishClient] = None
        self._workers: List[RFWorker] = []

        central = QWidget(self); self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- Connection UI ----
        conn_row1 = QHBoxLayout(); root.addLayout(conn_row1)
        conn_row1.addWidget(self._label("Scheme:"))
        self.scheme_box = self._combo(["https", "http"], "https"); conn_row1.addWidget(self.scheme_box)

        conn_row1.addWidget(self._label("Host/IP:"))
        self.host_in = self._edit("", "192.168.1.10 or bmc.local"); self.host_in.setMinimumWidth(220)
        conn_row1.addWidget(self.host_in, 2)

        conn_row1.addWidget(self._label("Port:"))
        self.port_in = self._edit("", "(blank = default)"); self.port_in.setMaximumWidth(120)
        conn_row1.addWidget(self.port_in)

        conn_row1.addWidget(self._label("Username:"))
        self.user_in = self._edit("root"); conn_row1.addWidget(self.user_in)

        conn_row1.addWidget(self._label("Password:"))
        self.pass_in = self._edit("0penBmc"); self.pass_in.setEchoMode(QLineEdit.Password)
        conn_row1.addWidget(self.pass_in)

        conn_row2 = QHBoxLayout(); root.addLayout(conn_row2)
        self.verify_cb = self._check("Verify TLS (off = curl -k)", False); conn_row2.addWidget(self.verify_cb)
        conn_row2.addWidget(self._label("Timeout (s):"))
        self.timeout_sb = QDoubleSpinBox(); self.timeout_sb.setDecimals(1); self.timeout_sb.setMinimum(1.0)
        self.timeout_sb.setMaximum(120.0); self.timeout_sb.setValue(8.0); self.timeout_sb.setSingleStep(0.5)
        conn_row2.addWidget(self.timeout_sb)
        self.session_cb = self._check("Use SessionService token", False); conn_row2.addWidget(self.session_cb)

        self.connect_btn = self._button("Connect / Test", self.on_connect)
        conn_row2.addWidget(self.connect_btn)

        self.status_lbl = self._label("")
        root.addWidget(self.status_lbl)

        # ---- Tabs ----
        self.tabs = QTabWidget(); root.addWidget(self.tabs, 1)
        self.power_tab = QWidget(); self.tabs.addTab(self.power_tab, "Power"); self._build_power_tab()
        self.sensors_tab = QWidget(); self.tabs.addTab(self.sensors_tab, "Sensors"); self._build_sensors_tab()
        self.logs_tab = QWidget(); self.tabs.addTab(self.logs_tab, "Logs"); self._build_logs_tab()
        self.fw_tab = FirmwareTab(self)
        self.tabs.addTab(self.fw_tab, "Firmware")
        self.users_tab = QWidget(); self.tabs.addTab(self.users_tab, "Users"); self._build_users_tab()
        self.vm_tab = VirtualMediaTab(self)
        self.tabs.addTab(self.vm_tab, "Virtual Media")
        self.raw_tab = QWidget(); self.tabs.addTab(self.raw_tab, "Raw"); self._build_raw_tab()
        self.console_tab = QWidget(); self.tabs.addTab(self.console_tab, "Console"); self._build_console_tab()

    # ---------- Styling helpers ----------
    def _apply_theme(self):
        self.setStyleSheet(f"""
            QMainWindow {{ background: {self.DARK_BG}; color: {self.TEXT}; }}
            QWidget {{ background: {self.DARK_BG}; color: {self.TEXT}; }}
            QTextEdit, QLineEdit {{
                background: {self.FIELD_BG};
                color: {self.TEXT};
                border: 1px solid #3C4043;
                border-radius: 6px; padding: 6px;
                selection-background-color: {self.BLUE};
            }}
            QTabWidget::pane {{ border: 1px solid #3C4043; background: {self.CARD_BG}; }}
            QTabBar::tab {{
                background: #242628; color: {self.TEXT}; padding: 8px 16px; border: 1px solid #3C4043;
                border-bottom: none; border-top-left-radius: 6px; border-top-right-radius: 6px;
            }}
            QTabBar::tab:selected {{ background: {self.CARD_BG}; }}
            QLabel {{ color: {self.TEXT}; }}
            QPushButton {{
                background-color: {self.BLUE};
                color: white; border: none; border-radius: 8px; padding: 8px 14px; font-weight: 600;
            }}
            QPushButton:hover {{ background-color: {self.BLUE_HOVER}; }}
            QPushButton:disabled {{ background-color: #3c6282; color: #cbd5e1; }}
            QCheckBox {{ color: {self.TEXT}; }}
        """)

    def _label(self, text: str) -> QLabel:
        return QLabel(text)

    def _edit(self, text: str = "", placeholder: str = "") -> QLineEdit:
        e = QLineEdit(); e.setText(text); e.setPlaceholderText(placeholder); return e

    def _button(self, text: str, handler) -> QPushButton:
        b = QPushButton(text); b.clicked.connect(handler); return b

    def _check(self, text: str, checked: bool) -> QCheckBox:
        c = QCheckBox(text); c.setChecked(checked); return c

    def _combo(self, items: List[str], current: str) -> QComboBox:
        b = QComboBox(); b.addItems(items); b.setCurrentText(current); return b

    def log(self, msg: str):
        if hasattr(self, "console"):
            self.console.append(msg)

    def set_status(self, msg: str, error: bool = False):
        color = self.DANGER if error else self.TEXT
        self.status_lbl.setText(f'<span style="color:{color}">{msg}</span>')
        self.log(msg)

    # ---------- Connection ----------
    def on_connect(self):
        host = self.host_in.text().strip()
        if not host:
            self.set_status("Enter host/IP.", error=True); return
        try:
            port = int(self.port_in.text()) if self.port_in.text().strip() else None
        except ValueError:
            self.set_status("Port must be a number.", error=True); return

        cfg = RFConfig(
            scheme=self.scheme_box.currentText(),
            host=host,
            port=port,
            username=self.user_in.text().strip(),
            password=self.pass_in.text(),
            verify=self.verify_cb.isChecked(),
            timeout=float(self.timeout_sb.value()),
            use_session=self.session_cb.isChecked(),
        )
        self.client = RedfishClient(cfg, log_fn=lambda m: self.console.append(f"[HTTP] {m}"))
        base = self.client.base_url()
        self.set_status(f"Connecting to {base} …")
        self.connect_btn.setEnabled(False)

        if cfg.use_session:
            self._spawn_worker(self.client.login, self._on_login_done)
        else:
            self._spawn_worker(self.client.test_connection, self._on_connect_done)

    def _on_login_done(self, ok: bool, data: Any):
        if ok:
            self.set_status("Session login OK ✓")
            self._spawn_worker(self.client.test_connection, self._on_connect_done)
        else:
            self.connect_btn.setEnabled(True)
            self.set_status(f"Session login failed: {pretty(data)}", error=True)

    def _on_connect_done(self, ok: bool, data: Any):
        self.connect_btn.setEnabled(True)
        if ok:
            self.set_status("Connected ✓")
            self.on_power_refresh()
            self.on_list_sensors()
        else:
            self.set_status(f"Connection failed: {pretty(data)}", error=True)

    def _spawn_worker(self, fn, cb):
        if not self.client:
            self.set_status("Not connected.", error=True); return
        w = RFWorker(fn)
        w.finished.connect(cb)
        self._workers.append(w)
        def _cleanup(*_):
            try: self._workers.remove(w)
            except ValueError: pass
        w.finished.connect(_cleanup)
        w.start()

    # ---------- Power Tab ----------
    def _build_power_tab(self):
        layout = QVBoxLayout(self.power_tab)
        row = QHBoxLayout(); layout.addLayout(row)
        self.power_state_lbl = self._label("PowerState: (unknown)"); row.addWidget(self.power_state_lbl)
        row.addWidget(self._button("Refresh State", self.on_power_refresh)); row.addStretch(1)

        row2 = QHBoxLayout(); layout.addLayout(row2)
        row2.addWidget(self._button("Power On", lambda: self.on_power_action("On")))
        row2.addWidget(self._button("Force Off", lambda: self.on_power_action("ForceOff")))
        row2.addWidget(self._button("Graceful Restart", lambda: self.on_power_action("GracefulRestart")))
        row2.addStretch(1)

        self.power_out = QTextEdit(); self.power_out.setReadOnly(True); layout.addWidget(self.power_out, 1)

    def on_power_refresh(self):
        self._spawn_worker(lambda: self.client.get("/redfish/v1/Systems/system"), self._on_power_refreshed)

    def _on_power_refreshed(self, ok: bool, data: Any):
        if ok and isinstance(data, dict) and isinstance(data.get("data"), dict):
            state = data["data"].get("PowerState")
            self.power_state_lbl.setText(f"PowerState: {state}")
        else:
            self.power_state_lbl.setText(f"PowerState: error -> {pretty(data)}")
        self.power_out.setPlainText(pretty(data))

    def on_power_action(self, reset_type: str):
        payload = {"ResetType": reset_type}
        self._spawn_worker(lambda: self.client.post_json("/redfish/v1/Systems/system/Actions/ComputerSystem.Reset", payload),
                           self._on_power_action_done)

    def _on_power_action_done(self, ok: bool, data: Any):
        self.power_out.setPlainText(pretty(data))
        if ok:
            self.set_status(f"Power action sent ✓ (HTTP {data.get('status')})")
            self.on_power_refresh()
        else:
            self.set_status(f"Power action failed: {pretty(data)}", error=True)

    # ---------- Sensors Tab ----------
    def _build_sensors_tab(self):
        layout = QVBoxLayout(self.sensors_tab)
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(self._button("Discover + Read Sensors", self.on_list_sensors))
        row.addStretch(1)

        mid = QHBoxLayout(); layout.addLayout(mid, 1)
        left = QVBoxLayout(); mid.addLayout(left, 1)
        self.sensors_out = QTextEdit(); self.sensors_out.setReadOnly(True); left.addWidget(self.sensors_out, 1)

        right = QVBoxLayout(); mid.addLayout(right, 1)
        self.bar_fig = Figure(figsize=(5, 3), facecolor=self.CARD_BG)
        self.bar_ax = self.bar_fig.add_subplot(111)
        self._style_axes(self.bar_ax)
        self.bar_canvas = FigureCanvas(self.bar_fig); right.addWidget(self.bar_canvas, 2)
        
        ctl = QHBoxLayout(); right.addLayout(ctl)
        ctl.addWidget(self._label("Live sensor:"))
        self.live_combo = QComboBox(); ctl.addWidget(self.live_combo, 1)
        ctl.addWidget(self._label("Poll (s):"))
        self.live_interval = QDoubleSpinBox(); self.live_interval.setDecimals(1); self.live_interval.setMinimum(0.5)
        self.live_interval.setMaximum(60.0); self.live_interval.setValue(2.0); ctl.addWidget(self.live_interval)
        self.live_btn = self._button("Start Live", self.on_toggle_live); ctl.addWidget(self.live_btn)
        
        self.live_fig = Figure(figsize=(5, 2.4), facecolor=self.CARD_BG)
        self.live_ax = self.live_fig.add_subplot(111)
        self._style_axes(self.live_ax)
        self.live_canvas = FigureCanvas(self.live_fig); right.addWidget(self.live_canvas, 2)

    def _style_axes(self, ax):
        ax.set_facecolor(self.CARD_BG)
        ax.tick_params(colors=self.TEXT)
        for side in ("bottom", "top", "left", "right"):
            ax.spines[side].set_color(self.MUTED)
        ax.title.set_color(self.TEXT); ax.yaxis.label.set_color(self.TEXT); ax.xaxis.label.set_color(self.TEXT)

    def on_list_sensors(self):
        if not self.client:
            self.set_status("Not connected.", error=True); return
        self.set_status("Discovering sensors …")
        self._spawn_worker(self._discover_read_sensors, self._on_sensors_ready)

    def _discover_read_sensors(self):
        paths = self.client.discover_sensor_paths()
        readings = []
        for p in paths:
            item = self.client.read_sensor(p)
            if item:
                readings.append(item)
        readings.sort(key=lambda x: x["name"])
        return True, {"status": 200, "data": {"readings": readings}}, 200

    def _on_sensors_ready(self, ok: bool, data: Any):
        if not ok:
            self.set_status("Sensors discovery failed.", error=True); return
        body = data.get("data", {})
        readings = body.get("readings", [])
        self.sensors_out.setPlainText(pretty(readings))

        self.live_combo.clear()
        for r in readings:
            self.live_combo.addItem(f'{r["name"]} [{r["units"]}]', r["path"])

        names = [r["name"] for r in readings if isinstance(r.get("reading"), (int, float))]
        vals = [r["reading"] for r in readings if isinstance(r.get("reading"), (int, float))]
        self.bar_ax.clear(); self._style_axes(self.bar_ax)
        if vals:
            self.bar_ax.bar(range(len(vals)), vals, color=self.BLUE)
            self.bar_ax.set_title("Sensor Readings"); self.bar_ax.set_ylabel("Value")
            self.bar_ax.set_xticks(range(len(names)))
            max_labels = 12
            if len(names) <= max_labels:
                display_names = names
            else:
                step = max(1, len(names) // max_labels)
                display_names = [n if (i % step == 0) else "" for i, n in enumerate(names)]
            self.bar_ax.set_xticklabels(display_names, rotation=30, ha='right')
        self.bar_canvas.draw()
        self.set_status(f"Found {len(readings)} sensor(s).")

    def on_toggle_live(self):
        if hasattr(self, "_live_timer") and self._live_timer and self._live_timer.isActive():
            self._live_timer.stop(); self.live_btn.setText("Start Live"); return
        idx = self.live_combo.currentIndex()
        if idx < 0:
            self.set_status("Select a sensor first.", error=True); return
        self._live_path = self.live_combo.currentData()
        self._live_series = []
        if not hasattr(self, "_live_timer") or not self._live_timer:
            self._live_timer = QTimer(self); self._live_timer.timeout.connect(self._live_poll_once)
        self._live_timer.start(int(self.live_interval.value() * 1000))
        self.live_btn.setText("Stop Live")
        self._live_poll_once()

    def _live_poll_once(self):
        if not hasattr(self, "_live_path") or not self._live_path or not self.client:
            return
        fetch_path = self._live_path.split("#")[0] if "#Temperatures/" in self._live_path else self._live_path
        ok, data, _ = self.client.get(fetch_path)
        if ok and isinstance(data, dict):
            body = data.get("data")
            val = None; units = ""
            if isinstance(body, dict):
                for k in ("Reading","ReadingCelsius","ReadingVolts","ReadingWatts","ReadingAmps","ReadingPercent"):
                    if k in body:
                        try: val = float(body[k]); break
                        except Exception: pass
                units = body.get("ReadingUnits") or units
            if val is None and "#Temperatures/" in self._live_path:
                item = self.client.read_sensor(self._live_path)
                if item:
                    val = item["reading"]; units = item.get("units") or units
            if isinstance(val, (int, float)):
                t = time.time()
                if not hasattr(self, "_live_series"): self._live_series = []
                self._live_series.append((t, val)); self._live_series = self._live_series[-180:]
                self.live_ax.clear(); self._style_axes(self.live_ax)
                xs = [x - self._live_series[0][0] for x, _ in self._live_series]
                ys = [y for _, y in self._live_series]
                self.live_ax.plot(xs, ys, marker="o", linewidth=2.0, color=self.BLUE)
                self.live_ax.set_title("Live Sensor"); self.live_ax.set_xlabel("Time (s)"); self.live_ax.set_ylabel(units or "Value")
                self.live_canvas.draw()

    # ---------- Logs Tab ----------
    def _build_logs_tab(self):
        layout = QVBoxLayout(self.logs_tab)
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(self._button("Refresh Logs", self.on_logs_refresh))
        row.addWidget(self._button("Clear Logs", self.on_logs_clear)); row.addStretch(1)
        self.logs_out = QTextEdit(); self.logs_out.setReadOnly(True); layout.addWidget(self.logs_out, 1)

    def on_logs_refresh(self):
        self._spawn_worker(lambda: self.client.get("/redfish/v1/Systems/system/LogServices/EventLog/Entries"), self._on_logs_done)

    def _on_logs_done(self, ok: bool, data: Any):
        self.logs_out.setPlainText(pretty(data))
        if not ok: self.set_status("Failed to get logs.", error=True)

    def on_logs_clear(self):
        self._spawn_worker(lambda: self.client.post_json("/redfish/v1/Systems/system/LogServices/EventLog/Actions/LogService.ClearLog", {}),
                           self._on_logs_cleared)

    def _on_logs_cleared(self, ok: bool, data: Any):
        self.logs_out.setPlainText(pretty(data))
        if ok:
            self.set_status("Clear Log requested ✓"); self.on_logs_refresh()
        else:
            self.set_status("Failed to clear logs.", error=True)

    # ---------- Firmware Tab ----------
    def _build_fw_tab(self):
        layout = QVBoxLayout(self.fw_tab)

        top = QHBoxLayout(); layout.addLayout(top)
        top.addWidget(self._button("List Firmware Inventory", self.on_fw_list))
        top.addWidget(self._button("Show UpdateService", self.on_fw_show_update_service))
        top.addStretch(1)

        mode_row = QHBoxLayout(); layout.addLayout(mode_row)
        mode_row.addWidget(self._label("Upload mode:"))
        self.fw_mode = QComboBox()
        self.fw_mode.addItems([
            "Auto (recommended)",
            "PUT (/UpdateService or HttpPushUri)",
            "POST (HttpPushUri)",
            "Multipart (MultipartHttpPushUri)",
            "SimpleUpdate (ImageURI)",
        ])
        mode_row.addWidget(self.fw_mode)
        mode_row.addWidget(self._label("ImageURI:"))
        self.image_uri_in = self._edit("", "http(s)://host/path/to/image")
        self.image_uri_in.setEnabled(False)
        mode_row.addWidget(self.image_uri_in, 1)

        def _mode_changed(_i):
            use_simple = self.fw_mode.currentText().startswith("SimpleUpdate")
            self.image_uri_in.setEnabled(use_simple)
        self.fw_mode.currentIndexChanged.connect(_mode_changed)

        self.fw_out = QTextEdit(); self.fw_out.setReadOnly(True); layout.addWidget(self.fw_out, 1)

        row = QHBoxLayout(); layout.addLayout(row)
        self.choose_btn = self._button(
            "Choose Firmware (.bin/.img/.rom/.cap/.tar/.tar.gz/.tgz)", self.on_choose_fw
        )
        self.upload_btn = self._button("Upload Firmware", self.on_upload_fw)
        row.addWidget(self.choose_btn); row.addWidget(self.upload_btn); row.addStretch(1)

        layout.addWidget(self._label(
            '<i>Auto tries Multipart → POST HttpPushUri → PUT HttpPushUri → PUT /UpdateService. '
            'Use SimpleUpdate if your BMC only accepts an ImageURI.</i>'
        ))

        self.fw_path: Optional[str] = None

        layout.addWidget(self._label("<b>Multi-BMC Firmware Update</b>"))

        row_mu_top = QHBoxLayout(); layout.addLayout(row_mu_top)
        self.multi_choose_btn = self._button(
            "Choose Image for Multi-Update (.bin/.img/.rom/.cap/.tar/.tar.gz/.tgz)",
            lambda: self._multi_choose_fw()
        )
        self.multi_start_btn = self._button("Start Multi-Update", self._multi_start)
        self.multi_start_btn.setEnabled(False)
        row_mu_top.addWidget(self.multi_choose_btn)
        row_mu_top.addWidget(self.multi_start_btn)
        row_mu_top.addStretch(1)

        row_mu_mid = QHBoxLayout(); layout.addLayout(row_mu_mid)
        row_mu_mid.addWidget(self._label("Targets (one per line: host, host:port, scheme://user:pass@host:port, CSV, or JSON)"))
        row_mu_mid.addStretch(1)
        row_mu_mid.addWidget(self._label("Concurrency:"))
        self.multi_conc = QSpinBox(); self.multi_conc.setRange(1, 32); self.multi_conc.setValue(4)
        row_mu_mid.addWidget(self.multi_conc)

        self.multi_hosts = QTextEdit()
        self.multi_hosts.setPlaceholderText("""One target per line. Supported formats:
  bmc-01
  bmc-02:8443 admin pass123
  bmc-03 admin pass123
  https://admin:pass123@10.0.0.51:443
  bmc-04,8443,admin,pass123
  bmc-05,admin,pass123
  {"host":"bmc-06","port":443,"user":"admin","pass":"p@$$, with , and spaces"}
""")
        layout.addWidget(self.multi_hosts, 1)

        self.multi_log = QTextEdit(); self.multi_log.setReadOnly(True)
        self.multi_log.setPlaceholderText("Multi-update log will appear here…")
        layout.addWidget(self.multi_log, 2)

        self._multi_fw_path = None
        self._multi_jobs: List[tuple] = []
        self._multi_active: List[QThread] = []
        self._multi_workers: List[QThread] = []
        self._multi_cancel = False

    def on_fw_list(self):
        self._spawn_worker(lambda: self.client.get("/redfish/v1/UpdateService/FirmwareInventory"),
                           self._on_fw_list_done)

    def _on_fw_list_done(self, ok: bool, data: Any):
        self.fw_out.setPlainText(pretty(data))
        if not ok:
            self.set_status("Failed to list firmware.", error=True)

    def on_fw_show_update_service(self):
        self._spawn_worker(self.client.get_update_service, self._on_fw_us_done)

    def _on_fw_us_done(self, ok: bool, data: Any):
        self.fw_out.setPlainText(pretty(data))
        if not ok:
            self.set_status("Failed to read UpdateService.", error=True)

    def on_choose_fw(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select firmware image", os.getcwd(),
            "Firmware Images (*.bin *.img *.rom *.cap *.tar *.tar.gz *.tgz);;All Files (*.*)"
        )
        if path:
            self.fw_path = path
            self.set_status(f"Selected: {os.path.basename(path)}")
        else:
            self.fw_path = None

    def on_upload_fw(self):
        if getattr(self, "fw_path", None):
            try:
                with open(self.fw_path, "rb") as f:
                    file_bytes = f.read()
                filename = os.path.basename(self.fw_path)
            except Exception as e:
                self.set_status(f"Read error: {e}", error=True); return

            self._spawn_worker(lambda: self.client.put_local_file_best_effort(file_bytes, filename),
                            self._on_fw_upload_done)
            return

        image_uri = self.image_uri_in.text().strip() if hasattr(self, "image_uri_in") else ""
        if image_uri:
            def _simple_update():
                ok_us, svc, _ = self.client.get_update_service()
                if not ok_us or not isinstance(svc, dict) or not isinstance(svc.get("data"), dict):
                    return False, {"status": 0, "data": {"error": "UpdateService not available"}}, 0
                actions = svc["data"].get("Actions") or {}
                simple = actions.get("#UpdateService.SimpleUpdate") or {}
                target = simple.get("target")
                if not target:
                    return False, {"status": 405, "data": {"error": "SimpleUpdate not supported"}}, 405
                return self.client.simple_update(target, image_uri)
            self._spawn_worker(_simple_update, self._on_fw_upload_done)
            return

        self.set_status("Select a firmware file or provide an ImageURI.", error=True)

    def _on_fw_upload_done(self, ok: bool, data: Any):
        self.fw_out.setPlainText(pretty(data))
        if ok:
            self.set_status(f"Upload sent ✓ (HTTP {data.get('status')})")
        else:
            self.set_status(f"Firmware upload failed: {pretty(data)}", error=True)

    # --- Multi-BMC helpers ---
    def _multi_choose_fw(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select firmware image", os.getcwd(),
            "Firmware Images (*.bin *.img *.rom *.cap *.tar *.tar.gz *.tgz);;All Files (*.*)"
        )
        if path:
            self._multi_fw_path = path
            self.set_status(f"Multi-update image: {os.path.basename(path)}")
            self.multi_start_btn.setEnabled(True)

    def _parse_target_line(self, line: str, default_user: str, default_pass: str):
        import json as _json
        from urllib.parse import urlparse

        s = line.strip()
        if not s:
            return None

        scheme = self.scheme_box.currentText().strip() if hasattr(self, "scheme_box") else "https"
        user = default_user
        pw = default_pass
        host = ""
        port = None

        if s.startswith("{") and s.endswith("}"):
            try:
                obj = _json.loads(s)
                host = str(obj.get("host", "")).strip()
                if not host:
                    return None
                if obj.get("scheme"):
                    scheme = str(obj["scheme"]).strip() or scheme
                if obj.get("port") not in (None, ""):
                    try: port = int(obj["port"])
                    except Exception: return None
                if obj.get("user") not in (None, ""): user = str(obj["user"])
                if obj.get("pass") not in (None, ""): pw = str(obj["pass"])
                return (scheme, host, port, user, pw)
            except Exception:
                return None

        if "://" in s:
            try:
                p = urlparse(s)
                if p.scheme: scheme = p.scheme
                if p.hostname: host = p.hostname
                if p.port: port = int(p.port)
                if p.username: user = p.username
                if p.password: pw = p.password
                if not host: return None
                return (scheme, host, port, user, pw)
            except Exception:
                return None

        if "," in s:
            parts = [t.strip() for t in s.split(",") if t.strip() != ""]
            if len(parts) == 4:
                host = parts[0]
                try: port = int(parts[1])
                except Exception: return None
                user = parts[2] or user
                pw = parts[3] or pw
                return (scheme, host, port, user, pw)
            if len(parts) == 3:
                host = parts[0]
                user = parts[1] or user
                pw = parts[2] or pw
                return (scheme, host, None, user, pw)

        toks = s.split()
        if len(toks) >= 1:
            hp = toks[0]
            if ":" in hp:
                try:
                    h, p = hp.rsplit(":", 1)
                    host = h.strip()
                    port = int(p)
                except Exception:
                    return None
            else:
                host = hp.strip()

            if len(toks) >= 3:
                user = toks[1]
                pw = " ".join(toks[2:]) 

            if not host:
                return None
            return (scheme, host, port, user, pw)

        return None

    def _multi_reset_state(self):
        if not hasattr(self, "_multi_jobs") or not isinstance(self._multi_jobs, list):
            self._multi_jobs = []
        if not hasattr(self, "_multi_active") or not isinstance(self._multi_active, list):
            try:
                self._multi_active = list(self._multi_active)
            except Exception:
                self._multi_active = []
        if not hasattr(self, "_multi_workers") or not isinstance(self._multi_workers, list):
            self._multi_workers = []
        self._multi_cancel = False

    def _multi_start(self):
        if not getattr(self, "_multi_fw_path", None):
            self.set_status("Select a firmware image for multi-update.", error=True); return

        try:
            with open(self._multi_fw_path, "rb") as f:
                self._multi_fw_bytes = f.read()
            self._multi_filename = os.path.basename(self._multi_fw_path)
        except Exception as e:
            self.set_status(f"Read error: {e}", error=True); return

        default_user = self.user_in.text().strip() if hasattr(self, "user_in") else "root"
        default_pass = self.pass_in.text() if hasattr(self, "pass_in") else "0penBmc"
        verify = self.verify_cb.isChecked() if hasattr(self, "verify_cb") else False
        timeout = float(self.timeout_sb.value()) if hasattr(self, "timeout_sb") else 8.0

        self._multi_reset_state()

        lines = [ln for ln in self.multi_hosts.toPlainText().splitlines() if ln.strip()]
        targets = []
        for ln in lines:
            parsed = self._parse_target_line(ln, default_user, default_pass)
            if parsed:
                targets.append(parsed)
            else:
                self.multi_log.append(f"[skip] malformed line: {ln}")

        if not targets:
            self.set_status("No valid targets provided.", error=True); return

        self._multi_jobs = [(sch, h, p, u, pw, verify, timeout) for (sch, h, p, u, pw) in targets]
        self.multi_log.clear()
        self.set_status(f"Starting multi-update: {len(self._multi_jobs)} target(s)")
        self._launch_next_batch()

    def _launch_next_batch(self):
        if self._multi_cancel:
            return

        if not hasattr(self, "_multi_active") or not isinstance(self._multi_active, list):
            try: self._multi_active = list(self._multi_active)
            except Exception: self._multi_active = []
        if not hasattr(self, "_multi_workers") or not isinstance(self._multi_workers, list):
            self._multi_workers = []

        max_workers = int(self.multi_conc.value())
        while len(self._multi_active) < max_workers and self._multi_jobs:
            sch, host, port, user, pw, verify, timeout = self._multi_jobs.pop(0)
            label = f"{sch}://{host}{(':'+str(port)) if port else ''}"
            self.multi_log.append(f"[start] {label}")

            w = MultiUpdateWorker(
                host, port, sch, user, pw, verify, timeout,
                self._multi_fw_bytes, self._multi_filename, mode="auto"
            )
            self._multi_workers.append(w)   
            self._multi_active.append(w)    
            w.finished.connect(self._on_multi_worker_done)
            w.start()

        if not self._multi_active and not self._multi_jobs:
            self.multi_log.append("[done] all targets processed.")

    def _on_multi_worker_done(self, host_label: str, ok: bool, result: object):
        sender = self.sender()
        try:
            if sender in self._multi_active:
                self._multi_active.remove(sender)
        except Exception:
            pass

        if ok:
            try:
                status = result.get("status")
                self.multi_log.append(f"[ok] {host_label} (HTTP {status})")
            except Exception:
                self.multi_log.append(f"[ok] {host_label}")
        else:
            self.multi_log.append(f"[fail] {host_label} -> {result}")

        self._launch_next_batch()

    # ---------- Users Tab ----------
    def _build_users_tab(self):
        layout = QVBoxLayout(self.users_tab)
        row1 = QHBoxLayout(); layout.addLayout(row1)
        row1.addWidget(self._button("List Users", self.on_users_list)); row1.addStretch(1)
        self.users_out = QTextEdit(); self.users_out.setReadOnly(True); layout.addWidget(self.users_out, 1)

        layout.addWidget(self._label("Add User"))
        row2 = QHBoxLayout(); layout.addLayout(row2)
        self.add_user_in = self._edit("", "username")
        self.add_pass_in = self._edit("", "password"); self.add_pass_in.setEchoMode(QLineEdit.Password)
        self.role_in = self._edit("Administrator", "RoleId")
        row2.addWidget(self.add_user_in); row2.addWidget(self.add_pass_in); row2.addWidget(self.role_in)
        row2.addWidget(self._button("Add", self.on_user_add))

        layout.addWidget(self._label("Delete User"))
        row3 = QHBoxLayout(); layout.addLayout(row3)
        self.del_user_in = self._edit("", "username")
        row3.addWidget(self.del_user_in); row3.addWidget(self._button("Delete", self.on_user_del)); row3.addStretch(1)

    def on_users_list(self):
        self._spawn_worker(lambda: self.client.get("/redfish/v1/AccountService/Accounts"), self._on_users_list_done)

    def _on_users_list_done(self, ok: bool, data: Any):
        self.users_out.setPlainText(pretty(data))
        if not ok: self.set_status("Failed to list users.", error=True)

    def on_user_add(self):
        u = self.add_user_in.text().strip(); p = self.add_pass_in.text(); r = self.role_in.text().strip() or "Administrator"
        if not u or not p:
            self.set_status("Username and password required.", error=True); return
        payload = {"UserName": u, "Password": p, "RoleId": r}
        self._spawn_worker(lambda: self.client.post_json("/redfish/v1/AccountService/Accounts", payload),
                           self._on_user_add_done)

    def _on_user_add_done(self, ok: bool, data: Any):
        self.users_out.setPlainText(pretty(data))
        if ok: self.set_status("User created ✓"); self.on_users_list()
        else: self.set_status("Failed to add user.", error=True)

    def on_user_del(self):
        u = self.del_user_in.text().strip()
        if not u:
            self.set_status("Enter username to delete.", error=True); return
        self._spawn_worker(lambda: self.client.delete(f"/redfish/v1/AccountService/Accounts/{u}"),
                           self._on_user_del_done)

    def _on_user_del_done(self, ok: bool, data: Any):
        self.users_out.setPlainText(pretty(data))
        if ok: self.set_status("User deleted ✓"); self.on_users_list()
        else: self.set_status("Failed to delete user.", error=True)


# ---- Virtual Media helpers ----
    def get_virtual_media(self):
            """Discover Virtual Media endpoints with aggressive OpenBMC fallback."""
            ok, data, code = self.get("/redfish/v1/Managers")
            vm_endpoints = []
            
            # Standard Dynamic Discovery
            if ok and isinstance(data.get("data"), dict):
                members = data["data"].get("Members", [])
                for m in members:
                    mgr_id = m.get("@odata.id")
                    if mgr_id:
                        vm_url = f"{mgr_id}/VirtualMedia"
                        ok_vm, vm_data, _ = self.get(vm_url)
                        if ok_vm and isinstance(vm_data.get("data"), dict):
                            for v in vm_data["data"].get("Members", []):
                                v_id = v.get("@odata.id")
                                if v_id:
                                    vm_endpoints.append(v_id)
            
            # OpenBMC Fallback (If standard discovery returned 0 slots)
            if not vm_endpoints:
                self.log("Standard discovery empty. Trying OpenBMC fallback path...")
                fallback_url = "/redfish/v1/Managers/bmc/VirtualMedia"
                ok_vm, vm_data, _ = self.get(fallback_url)
                if ok_vm and isinstance(vm_data.get("data"), dict):
                    for v in vm_data["data"].get("Members", []):
                        v_id = v.get("@odata.id")
                        if v_id:
                            vm_endpoints.append(v_id)
                            
            if not vm_endpoints:
                return False, {"error": "No VirtualMedia endpoints found on this BMC. Check permissions or BMC firmware support."}, 404
                
            return True, {"endpoints": vm_endpoints}, 200

    def get_virtual_media_status(self, endpoint: str) -> Tuple[bool, Any, int]:
        return self.get(endpoint)

    def insert_virtual_media(self, endpoint: str, image_url: str, user: str = "", password: str = "") -> Tuple[bool, Any, int]:
        payload = {"Image": image_url, "Inserted": True}
        if user:
            payload["UserName"] = user
        if password:
            payload["Password"] = password
        return self.post_json(f"{endpoint}/Actions/VirtualMedia.InsertMedia", payload)

    def eject_virtual_media(self, endpoint: str) -> Tuple[bool, Any, int]:
        return self.post_json(f"{endpoint}/Actions/VirtualMedia.EjectMedia", {})
 

    # ---------- Raw Tab ----------
    def _build_raw_tab(self):
        layout = QVBoxLayout(self.raw_tab)
        row1 = QHBoxLayout(); layout.addLayout(row1)
        row1.addWidget(self._label("Method:"))
        self.method_box = QComboBox(); self.method_box.addItems(["GET","POST","DELETE"]); row1.addWidget(self.method_box)
        row1.addWidget(self._label("Path:")); self.path_in = self._edit("", "/redfish/v1/Systems/system"); row1.addWidget(self.path_in, 1)
        self.body_edit = QTextEdit(); self.body_edit.setPlaceholderText('JSON body (POST only). Example: {"ResetType":"On"}'); layout.addWidget(self.body_edit, 1)
        layout.addWidget(self._button("Send", self.on_raw_send))
        self.raw_out = QTextEdit(); self.raw_out.setReadOnly(True); layout.addWidget(self.raw_out, 2)

    def on_raw_send(self):
        method = self.method_box.currentText()
        path = (self.path_in.text().strip() or "/")
        if method == "GET":
            self._spawn_worker(lambda: self.client.get(path), self._on_raw_done)
        elif method == "POST":
            body = self.body_edit.toPlainText().strip()
            try:
                payload = json.loads(body) if body else {}
            except Exception as e:
                self.set_status(f"Invalid JSON body: {e}", error=True); return
            self._spawn_worker(lambda: self.client.post_json(path, payload), self._on_raw_done)
        else:
            self._spawn_worker(lambda: self.client.delete(path), self._on_raw_done)

    def _on_raw_done(self, ok: bool, data: Any):
        self.raw_out.setPlainText(pretty(data))
        if not ok: self.set_status("Raw request failed.", error=True)

    # ---------- Console Tab ----------
    def _build_console_tab(self):
        layout = QVBoxLayout(self.console_tab)
        self.console = QTextEdit(); self.console.setReadOnly(True); layout.addWidget(self.console, 1)
        row = QHBoxLayout(); layout.addLayout(row)
        row.addWidget(self._button("Clear Console", lambda: self.console.clear())); row.addStretch(1)


def main():
    app = QApplication(sys.argv)
    w = MainWindow(); w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()