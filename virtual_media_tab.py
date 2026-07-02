# -*- coding: utf-8 -*-
"""
Virtual Media UI Component for Platypus Redfish Control Panel
(With HTTPS Range-Request Streaming Support)
"""
import os
import json
import socket
import threading
import socketserver
import ssl
import subprocess
import tempfile

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, 
    QComboBox, QTextEdit, QFileDialog, QGroupBox, QFormLayout
)

# --- Ensure Range Streaming Support is Installed ---
try:
    from RangeHTTPServer import RangeRequestHandler
    RANGE_SUPPORT = True
except ImportError:
    import http.server
    RangeRequestHandler = http.server.SimpleHTTPRequestHandler
    RANGE_SUPPORT = False

# --- Network & Server Helpers ---

def get_local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()

class StreamingHTTPHandler(RangeRequestHandler):
    """Handles Chunked Range Requests and forces exact MIME types."""
    def log_message(self, format, *args):
        pass # Keep the terminal quiet

    def guess_type(self, path):
        if path.lower().endswith('.iso'):
            return 'application/x-iso9660-image'
        if path.lower().endswith('.img') or path.lower().endswith('.bin'):
            return 'application/octet-stream'
        return super().guess_type(path)

class LocalFileServer:
    """Spins up a local HTTPS server that supports Block Device streaming."""
    def __init__(self, directory, local_ip):
        self.directory = directory
        self.local_ip = local_ip
        
        # 1. Generate local OpenSSL Certificates
        self.workspace = tempfile.mkdtemp()
        self.cert_path = os.path.join(self.workspace, "cert.pem")
        self.key_path = os.path.join(self.workspace, "key.pem")
        
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", 
            "-keyout", self.key_path, "-out", self.cert_path, 
            "-days", "1", "-nodes", "-subj", f"/CN={self.local_ip}",
            "-addext", f"subjectAltName=IP:{self.local_ip}"
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        # 2. Setup Server
        socketserver.TCPServer.allow_reuse_address = True
        handler = lambda *args, **kwargs: StreamingHTTPHandler(*args, directory=self.directory, **kwargs)
        
        self.port = 8443
        while self.port < 8500:
            try:
                self.httpd = socketserver.TCPServer(("", self.port), handler)
                break
            except OSError:
                self.port += 1
                
        # 3. Wrap Server in SSL
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=self.cert_path, keyfile=self.key_path)
        self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
                
        self.thread = threading.Thread(target=self.httpd.serve_forever)
        self.thread.daemon = True
        self.thread.start()
        
    def stop(self):
        threading.Thread(target=self.httpd.shutdown, daemon=True).start()

def pretty(obj):
    try:
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, indent=2)
        if isinstance(obj, str):
            return json.dumps(json.loads(obj), indent=2)
    except Exception:
        pass
    return str(obj)

# --- Main UI Class ---
class VirtualMediaTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.local_server = None 
        self._build_ui()

    def _label(self, text):
        return QLabel(text)

    def _edit(self, text="", placeholder=""):
        e = QLineEdit()
        e.setText(text)
        e.setPlaceholderText(placeholder)
        return e

    def _button(self, text, handler):
        b = QPushButton(text)
        b.clicked.connect(handler)
        return b

    def _build_ui(self):
        layout = QVBoxLayout(self)
        
        row1 = QHBoxLayout()
        layout.addLayout(row1)
        row1.addWidget(self._button("Discover Virtual Media Slots", self.on_vm_discover))
        
        self.vm_combo = QComboBox()
        self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
        row1.addWidget(self.vm_combo, 1)
        row1.addWidget(self._button("Refresh Selected Slot", self.on_vm_status))
        
        self.slot_details_group = QGroupBox("Selected Slot Live Info")
        details_layout = QFormLayout()
        
        self.lbl_connected = QLabel("Unknown")
        self.lbl_current_media = QLabel("None")
        self.lbl_write_protected = QLabel("Unknown")
        self.lbl_media_types = QLabel("Unknown")
        
        details_layout.addRow("Connected Status:", self.lbl_connected)
        details_layout.addRow("Current Attached Media:", self.lbl_current_media)
        details_layout.addRow("Write Protected:", self.lbl_write_protected)
        details_layout.addRow("Supported Media Types:", self.lbl_media_types)
        self.slot_details_group.setLayout(details_layout)
        layout.addWidget(self.slot_details_group)
        
        row2 = QHBoxLayout()
        layout.addLayout(row2)
        row2.addWidget(self._label("Image URL / Path:"))
        self.vm_image_in = self._edit("", "https://...")
        row2.addWidget(self.vm_image_in, 2)
        
        self.btn_browse = self._button("Browse & Host (HTTPS)", self.on_browse_iso)
        row2.addWidget(self.btn_browse)
        
        row3 = QHBoxLayout()
        layout.addLayout(row3)
        row3.addWidget(self._label("User (opt):"))
        self.vm_user_in = self._edit("")
        row3.addWidget(self.vm_user_in, 1)
        row3.addWidget(self._label("Pass (opt):"))
        self.vm_pass_in = self._edit("")
        self.vm_pass_in.setEchoMode(QLineEdit.Password)
        row3.addWidget(self.vm_pass_in, 1)
        
        row4 = QHBoxLayout()
        layout.addLayout(row4)
        row4.addWidget(self._button("Mount Image (InsertMedia)", self.on_vm_mount))
        row4.addWidget(self._button("Unmount Image (EjectMedia)", self.on_vm_unmount))
        row4.addStretch(1)
        
        self.vm_out = QTextEdit()
        self.vm_out.setReadOnly(True)
        layout.addWidget(self.vm_out, 1)

    def on_browse_iso(self):
        if not RANGE_SUPPORT:
            self.main.set_status("Missing dependency! Please run: pip install RangeHTTPServer", error=True)
            return

        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Virtual Media Disk Image", "", "Disk Images (*.iso *.img);;All Files (*)"
        )
        if file_path:
            if self.local_server:
                self.local_server.stop()
                self.local_server = None
                
            directory = os.path.dirname(file_path)
            filename = os.path.basename(file_path)
            local_ip = get_local_ip()
            
            # Spin up the HTTPS Server
            self.local_server = LocalFileServer(directory, local_ip)
            auto_url = f"https://{local_ip}:{self.local_server.port}/{filename}"
            self.vm_image_in.setText(auto_url)
            
            # Clear credentials (Python server doesn't use them)
            self.vm_user_in.setText("")
            self.vm_pass_in.setText("")
            
            self.main.set_status("Uploading auto-generated HTTPS certificate to BMC...")
            
            # Upload the Certificate to the BMC
            with open(self.local_server.cert_path, "r") as f:
                cert_data = f.read()
                
            payload = {
                "CertificateString": cert_data,
                "CertificateType": "PEM"
            }
            
            self.main._spawn_worker(
                lambda: self.main.client.post_json("/redfish/v1/Managers/bmc/Truststore/Certificates", payload),
                self._on_cert_upload_done
            )

    def _on_cert_upload_done(self, ok, data):
        if ok:
            self.main.set_status(f"Server certificate trusted! Ready to mount from {self.vm_image_in.text()}")
        else:
            self.main.set_status(f"Warning: Failed to upload cert to BMC. Mount may fail. (HTTP {data.get('status')})", error=True)
            self.vm_out.setPlainText(pretty(data))

    def on_vm_discover(self):
        if not self.main.client:
            self.main.set_status("Not connected.", error=True)
            return
            
        self.main.set_status("Discovering Virtual Media slots...")
        self.main._spawn_worker(self.main.client.get_virtual_media, self._on_vm_discover_done)

    def _on_vm_discover_done(self, ok, data):
        try:
            self.vm_combo.currentIndexChanged.disconnect(self.on_slot_selection_changed)
        except TypeError:
            pass
            
        self.vm_combo.clear()
        
        if ok and isinstance(data, dict) and "endpoints" in data:
            endpoints = data["endpoints"]
            for ep in endpoints:
                slot_name = ep.split("/")[-1]
                self.vm_combo.addItem(f"Slot: {slot_name}", ep)
            
            self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
            self.main.set_status(f"Found {len(endpoints)} Virtual Media slots.")
            self.vm_out.setPlainText(pretty(data))
            
            if endpoints:
                self.on_vm_status()
        else:
            self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
            self.main.set_status("Failed to parse Virtual Media endpoints.", error=True)
            self.vm_out.setPlainText(pretty(data))

    def on_slot_selection_changed(self, index):
        if index >= 0:
            self.on_vm_status()

    def on_vm_status(self):
        ep = self.vm_combo.currentData()
        if not ep:
            return
        self.main.set_status(f"Refreshing live details for {ep.split('/')[-1]}...")
        self.main._spawn_worker(lambda: self.main.client.get_virtual_media_status(ep), self._on_vm_status_done)

    def _on_vm_status_done(self, ok, data):
        self.vm_out.setPlainText(pretty(data))
        if ok and isinstance(data, dict) and "data" in data:
            payload = data["data"]
            
            connected = payload.get("Inserted", "Unknown")
            media_url = payload.get("Image", "None")
            write_prot = payload.get("WriteProtected", "Unknown")
            media_types = payload.get("MediaTypes", [])
            
            self.lbl_connected.setText(f"<b>{'CONNECTED' if connected else 'DISCONNECTED'}</b>")
            self.lbl_current_media.setText(str(media_url) if media_url else "None")
            self.lbl_write_protected.setText(str(write_prot))
            self.lbl_media_types.setText(", ".join(media_types) if media_types else "None")
            
            self.main.set_status("Virtual Media status refreshed.")
        else:
            self.main.set_status("Failed to get explicit slot status details.", error=True)

    def on_vm_mount(self):
        ep = self.vm_combo.currentData()
        img = self.vm_image_in.text().strip()
        if not ep:
            self.main.set_status("Select a Virtual Media slot first.", error=True)
            return
        if not img:
            self.main.set_status("Image file path or web URL is required.", error=True)
            return
            
        user = self.vm_user_in.text().strip()
        pwd = self.vm_pass_in.text()
        self.main.set_status(f"Mounting {img} to {ep.split('/')[-1]}...")
        self.main._spawn_worker(lambda: self.main.client.insert_virtual_media(ep, img, user, pwd), self._on_vm_action_done)

    def on_vm_unmount(self):
        ep = self.vm_combo.currentData()
        if not ep:
            self.main.set_status("Select a Virtual Media slot first.", error=True)
            return
        self.main.set_status(f"Unmounting media from {ep.split('/')[-1]}...")
        self.main._spawn_worker(lambda: self.main.client.eject_virtual_media(ep), self._on_vm_action_done)

    def _on_vm_action_done(self, ok, data):
        self.vm_out.setPlainText(pretty(data))
        if ok:
            self.main.set_status(f"Action successfully sent (HTTP {data.get('status')})")
            self.on_vm_status()
        else:
            self.main.set_status("Virtual Media transaction execution failed.", error=True)