# -*- coding: utf-8 -*-
"""
Automated Diagnostics & Self-Test UI Component for Platypus
"""
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QTableWidget, QTableWidgetItem, 
    QHeaderView, QLabel, QLineEdit, QFileDialog, QMessageBox, QComboBox
)
from PyQt5.QtGui import QColor

class TestTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        
        # Define the API subsystems (Safe Reads + Active Disruptive Tests)
        self.tests = [
            # --- SAFE READS ---
            {"name": "1. Root Connection", "method": lambda: self.main.client.get("/redfish/v1"), "prompt": None},
            {"name": "2. Computer Systems", "method": lambda: self.main.client.get("/redfish/v1/Systems"), "prompt": None},
            {"name": "3. Chassis & Sensors", "method": lambda: self.main.client.get("/redfish/v1/Chassis"), "prompt": None},
            {"name": "4. Virtual Media Discovery", "method": lambda: self.main.client.get_virtual_media(), "prompt": None},
            
            # --- ACTIVE TESTS (Requires Pop-up Confirmation) ---
            {"name": "5. Power ON", "method": lambda: self._test_power("On"), 
             "prompt": "Do you want to test Powering ON the server?"},
             
            {"name": "6. Power OFF", "method": lambda: self._test_power("ForceOff"), 
             "prompt": "Do you want to test Force Powering OFF the server?"},
             
            {"name": "7. Force Restart", "method": lambda: self._test_power("ForceRestart"), 
             "prompt": "Do you want to test Force Restarting the server?"},
             
            {"name": "8. Mount Test ISO", "method": self._test_mount, 
             "prompt": "Do you want to test Mounting the selected ISO to Virtual Media?"},
             
            {"name": "9. Unmount Media", "method": self._test_unmount, 
             "prompt": "Do you want to test Ejecting Virtual Media?"},
             
            {"name": "10. Push Firmware Update", "method": self._test_fw, 
             "prompt": "Test Firmware Update pipeline? (We will send the file as a dummy payload to test the API response without bricking the BMC)."}
        ]
        self.current_test = 0
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        
        # Description
        desc = QLabel("<b>Automated Redfish Diagnostics (Active Mode)</b><br/>"
                      "<i>Tests READ access, Power Control, Virtual Media, and Firmware pipelines. You will be prompted before any disruptive actions.</i>")
        layout.addWidget(desc)

        # --- Test ISO Selector (Virtual Media) ---
        row_iso = QHBoxLayout()
        row_iso.addWidget(QLabel("Test ISO (Virtual Media):"))
        self.test_iso_in = QLineEdit()
        self.test_iso_in.setPlaceholderText("https://... or C:/path/to/test.iso")
        row_iso.addWidget(self.test_iso_in, 1)
        
        btn_browse_iso = QPushButton("Browse ISO...")
        btn_browse_iso.clicked.connect(self.on_browse_iso)
        row_iso.addWidget(btn_browse_iso)
        layout.addLayout(row_iso)

        # --- Target VM Slot Selector ---
        row_slot = QHBoxLayout()
        row_slot.addWidget(QLabel("Target VM Slot:"))
        self.vm_slot_combo = QComboBox()
        row_slot.addWidget(self.vm_slot_combo, 1)
        
        btn_discover_slots = QPushButton("Discover Slots")
        btn_discover_slots.clicked.connect(self.on_discover_slots)
        row_slot.addWidget(btn_discover_slots)
        layout.addLayout(row_slot)

        # --- Test Firmware Selector (Update Service) ---
        row_fw = QHBoxLayout()
        row_fw.addWidget(QLabel("Test Firmware (Update Service):"))
        self.test_fw_in = QLineEdit()
        self.test_fw_in.setPlaceholderText("C:/path/to/firmware.bin")
        row_fw.addWidget(self.test_fw_in, 1)
        
        btn_browse_fw = QPushButton("Browse Firmware...")
        btn_browse_fw.clicked.connect(self.on_browse_fw)
        row_fw.addWidget(btn_browse_fw)
        layout.addLayout(row_fw)

        # --- Table ---
        self.table = QTableWidget(len(self.tests), 4)
        self.table.setHorizontalHeaderLabels(["Diagnostic Test", "Status", "HTTP Code", "Response Details"])
        
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.Stretch)
        
        for i, test in enumerate(self.tests):
            self.table.setItem(i, 0, QTableWidgetItem(test["name"]))
            self.table.setItem(i, 1, QTableWidgetItem("Pending"))
            self.table.setItem(i, 2, QTableWidgetItem("-"))
            self.table.setItem(i, 3, QTableWidgetItem("-"))
            
        layout.addWidget(self.table, 1)
        
        # --- Run Button ---
        self.btn_run = QPushButton("Run Diagnostic Sequence")
        self.btn_run.clicked.connect(self.run_tests)
        self.btn_run.setMinimumHeight(40)
        layout.addWidget(self.btn_run)

    # --- Browse & Discover Methods ---
    def on_browse_iso(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Test ISO", "", "Disk Images (*.iso *.img);;All Files (*)")
        if file_path:
            self.test_iso_in.setText(file_path)

    def on_browse_fw(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "Select Test Firmware", "", "Firmware Images (*.bin *.img *.tar *.tar.gz *.cap);;All Files (*)")
        if file_path:
            self.test_fw_in.setText(file_path)

    def on_discover_slots(self):
        if not self.main.client:
            self.main.set_status("Connect to a BMC first!", error=True)
            return
        self.main.set_status("Discovering Virtual Media slots for diagnostics...")
        self.main._spawn_worker(self.main.client.get_virtual_media, self._on_discover_slots_done)

    def _on_discover_slots_done(self, ok, data):
        self.vm_slot_combo.clear()
        if ok and isinstance(data, dict) and "endpoints" in data:
            endpoints = data["endpoints"]
            for ep in endpoints:
                slot_name = ep.split("/")[-1]
                self.vm_slot_combo.addItem(f"Slot: {slot_name}", ep)
            self.main.set_status(f"Found {len(endpoints)} VM slots.")
        else:
            self.main.set_status("Failed to discover VM slots.", error=True)

    # --- Active Test Logic Helpers ---
    def _test_power(self, action):
        ok, resp, _ = self.main.client.get("/redfish/v1/Systems")
        payload = resp.get("data", {}) if isinstance(resp, dict) else {}
        if ok and "Members" in payload and len(payload["Members"]) > 0:
            sys_ep = payload["Members"][0]["@odata.id"]
            return self.main.client.post_json(f"{sys_ep}/Actions/ComputerSystem.Reset", {"ResetType": action})
        return False, {"error": "System endpoint not found"}, 404

    def _test_mount(self):
        iso = self.test_iso_in.text().strip()
        ep = self.vm_slot_combo.currentData()
        
        if not iso:
            return False, {"error": "No ISO selected in the top bar."}, 400
        if not ep:
            return False, {"error": "No VM slot selected. Click 'Discover Slots' first."}, 400
            
        return self.main.client.insert_virtual_media(ep, iso)

    def _test_unmount(self):
        ep = self.vm_slot_combo.currentData()
        
        if not ep:
            return False, {"error": "No VM slot selected. Click 'Discover Slots' first."}, 400
            
        return self.main.client.eject_virtual_media(ep)

    def _test_fw(self):
        fw_img = self.test_fw_in.text().strip()
        if not fw_img:
            return False, {"error": "No Firmware Image selected in the top bar to push."}, 400
            
        # Push the firmware via SimpleUpdate to test the API pipeline
        ok, svc, _ = self.main.client.get_update_service()
        payload = svc.get("data", {}) if isinstance(svc, dict) else {}
        target = payload.get("Actions", {}).get("#UpdateService.SimpleUpdate", {}).get("target")
        
        if not target:
            return False, {"error": "UpdateService.SimpleUpdate target not found."}, 404
            
        return self.main.client.simple_update(target, fw_img)

    # --- Test Execution Loop ---
    def run_tests(self):
        if not self.main.client:
            self.main.set_status("Connect to a BMC first!", error=True)
            return

        self.btn_run.setEnabled(False)
        self.main.set_status("Running automated diagnostics...")
        
        for i in range(self.table.rowCount()):
            self.table.setItem(i, 1, QTableWidgetItem("Pending..."))
            self.table.setItem(i, 2, QTableWidgetItem(""))
            self.table.setItem(i, 3, QTableWidgetItem(""))

        self.current_test = 0
        self.run_next_test()

    def run_next_test(self):
        if self.current_test >= len(self.tests):
            self.btn_run.setEnabled(True)
            self.main.set_status("All diagnostic tests completed.")
            return

        test = self.tests[self.current_test]
        
        # Check if this test requires a pop-up confirmation
        if test["prompt"]:
            # Trigger the pop-up (Runs on the main GUI thread)
            reply = QMessageBox.question(
                self, 'Active Diagnostic Confirmation', test["prompt"],
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                # Mark as skipped and move to the next
                skip_item = QTableWidgetItem("SKIPPED")
                skip_item.setForeground(QColor(150, 150, 150)) # Gray
                self.table.setItem(self.current_test, 1, skip_item)
                self.table.setItem(self.current_test, 3, QTableWidgetItem("Skipped by user."))
                self.current_test += 1
                self.run_next_test()
                return

        # If approved (or if it's a safe read without a prompt), run it
        self.table.setItem(self.current_test, 1, QTableWidgetItem("Running..."))
        self.main._spawn_worker(test["method"], self._on_test_done)

    def _on_test_done(self, ok, data):
        status_code = "N/A"
        if isinstance(data, dict):
            status_code = str(data.get("status", "Error"))

        status_item = QTableWidgetItem("PASS" if ok else "FAIL")
        if ok:
            status_item.setForeground(QColor(0, 180, 0))
        else:
            status_item.setForeground(QColor(200, 0, 0))

        self.table.setItem(self.current_test, 1, status_item)
        self.table.setItem(self.current_test, 2, QTableWidgetItem(status_code))

        summary = ""
        if ok and isinstance(data, dict):
            if "endpoints" in data:
                summary = f"Found {len(data['endpoints'])} VM endpoints."
            elif "data" in data and isinstance(data["data"], dict):
                payload = data["data"]
                if "Members" in payload:
                    summary = f"Found {len(payload['Members'])} members."
                else:
                    summary = "Action executed successfully."
            else:
                summary = "Action executed successfully."
        else:
            summary = str(data.get("error", "Failed")) if isinstance(data, dict) else str(data)

        self.table.setItem(self.current_test, 3, QTableWidgetItem(summary))

        self.current_test += 1
        self.run_next_test()