# -*- coding: utf-8 -*-
"""
Virtual Media UI Component for Platypus Redfish Control Panel
"""
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, 
    QComboBox, QTextEdit, QFileDialog, QGroupBox, QFormLayout
)

def pretty(obj):
    """Helper to format JSON responses beautifully."""
    try:
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, indent=2)
        if isinstance(obj, str):
            return json.dumps(json.loads(obj), indent=2)
    except Exception:
        pass
    return str(obj)

class VirtualMediaTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window  # Reference to MainWindow for background threads
        self._build_ui()

    # --- Local UI Helpers ---
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

    # --- UI Layout ---
    def _build_ui(self):
        layout = QVBoxLayout(self)
        
        # --- Slot Discovery Section ---
        row1 = QHBoxLayout()
        layout.addLayout(row1)
        row1.addWidget(self._button("Discover Virtual Media Slots", self.on_vm_discover))
        
        self.vm_combo = QComboBox()
        self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
        row1.addWidget(self.vm_combo, 1)
        row1.addWidget(self._button("Refresh Selected Slot", self.on_vm_status))
        
        # --- Detailed Slot Metadata Group ---
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
        
        # --- Image Management Section ---
        row2 = QHBoxLayout()
        layout.addLayout(row2)
        row2.addWidget(self._label("Image URL / Path:"))
        self.vm_image_in = self._edit("", "http://... or select a local ISO/IMG file")
        row2.addWidget(self.vm_image_in, 2)
        
        # New Browse Button
        self.btn_browse = self._button("Browse...", self.on_browse_iso)
        row2.addWidget(self.btn_browse)
        
        # Optional Credentials
        row3 = QHBoxLayout()
        layout.addLayout(row3)
        row3.addWidget(self._label("User (opt):"))
        self.vm_user_in = self._edit("")
        row3.addWidget(self.vm_user_in, 1)
        row3.addWidget(self._label("Pass (opt):"))
        self.vm_pass_in = self._edit("")
        self.vm_pass_in.setEchoMode(QLineEdit.Password)
        row3.addWidget(self.vm_pass_in, 1)
        
        # Actions
        row4 = QHBoxLayout()
        layout.addLayout(row4)
        row4.addWidget(self._button("Mount Image (InsertMedia)", self.on_vm_mount))
        row4.addWidget(self._button("Unmount Image (EjectMedia)", self.on_vm_unmount))
        row4.addStretch(1)
        
        # Raw API output
        self.vm_out = QTextEdit()
        self.vm_out.setReadOnly(True)
        layout.addWidget(self.vm_out, 1)

    # --- Browse ISO Handler ---
    def on_browse_iso(self):
        """Opens a file dialog allowing user to pick a local disc image file."""
        file_path, _ = QFileDialog.getOpenFileName(
            self, 
            "Select Virtual Media Disk Image", 
            "", 
            "Disk Images (*.iso *.img);;All Files (*)"
        )
        if file_path:
            # Populates the path straight into the layout box
            self.vm_image_in.setText(file_path)
            self.main.set_status(f"Selected image: {file_path}")

    # --- Execution & Slot Parsing Logic ---
    def on_vm_discover(self):
        if not self.main.client:
            self.main.set_status("Not connected.", error=True)
            return
            
        if not hasattr(self.main.client, "get_virtual_media"):
            self.main.set_status("Critical Error: Virtual Media API methods are missing from RedfishClient.", error=True)
            return
            
        self.main.set_status("Discovering Virtual Media slots...")
        self.main._spawn_worker(self.main.client.get_virtual_media, self._on_vm_discover_done)

    def _on_vm_discover_done(self, ok, data):
        # Disconnect signal temporarily to avoid trigger loops during population
        self.vm_combo.currentIndexChanged.disconnect(self.on_slot_selection_changed)
        self.vm_combo.clear()
        
        if ok and isinstance(data, dict) and "endpoints" in data.get("data", {}):
            endpoints = data["data"]["endpoints"]
            for ep in endpoints:
                # Add formatted slot ID and stash full resource endpoint URL inside item metadata
                slot_name = ep.split("/")[-1]
                self.vm_combo.addItem(f"Slot: {slot_name}", ep)
            
            self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
            self.main.set_status(f"Found {len(endpoints)} Virtual Media slots.")
            self.vm_out.setPlainText(pretty(data))
            
            # Auto-check status of the first found slot
            if endpoints:
                self.on_vm_status()
        else:
            self.vm_combo.currentIndexChanged.connect(self.on_slot_selection_changed)
            self.main.set_status("Failed to discover Virtual Media.", error=True)
            self.vm_out.setPlainText(pretty(data))

    def on_slot_selection_changed(self, index):
        """Automatically fires when a user changes the dropdown slot selection."""
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
            
            # Safely parse live operational parameters out of the JSON
            connected = payload.get("Inserted", "Unknown")
            media_url = payload.get("Image", "None")
            write_prot = payload.get("WriteProtected", "Unknown")
            media_types = payload.get("MediaTypes", [])
            
            # Format and populate the metadata fields
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
            # Force UI metadata block to sync back up
            self.on_vm_status()
        else:
            self.main.set_status("Virtual Media transaction execution failed.", error=True)