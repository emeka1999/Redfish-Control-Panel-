# -*- coding: utf-8 -*-
"""
Power & Boot Options UI Component for Platypus Redfish Control Panel
"""
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, 
    QComboBox, QTextEdit, QGroupBox, QFormLayout
)

def pretty(obj):
    try:
        if isinstance(obj, (dict, list)):
            return json.dumps(obj, indent=2)
        if isinstance(obj, str):
            return json.dumps(json.loads(obj), indent=2)
    except Exception:
        pass
    return str(obj)

class PowerTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.system_endpoint = None
        self._build_ui()

    def _button(self, text, handler):
        b = QPushButton(text)
        b.clicked.connect(handler)
        return b

    def _build_ui(self):
        layout = QVBoxLayout(self)

        # --- Top Section: Power Controls ---
        power_group = QGroupBox("System Power Control")
        power_layout = QHBoxLayout()
        power_layout.addWidget(self._button("Power On", lambda: self.on_power_action("On")))
        power_layout.addWidget(self._button("Graceful Shutdown", lambda: self.on_power_action("GracefulShutdown")))
        power_layout.addWidget(self._button("Force Off", lambda: self.on_power_action("ForceOff")))
        power_layout.addWidget(self._button("Force Restart", lambda: self.on_power_action("ForceRestart")))
        power_group.setLayout(power_layout)
        layout.addWidget(power_group)

        # --- Middle Section: Next Boot Options ---
        boot_group = QGroupBox("Next Boot Options (BootSourceOverride)")
        boot_layout = QFormLayout()

        row_layout = QHBoxLayout()
        self.btn_refresh_boot = self._button("Read Current Boot Info", self.on_refresh_boot)
        row_layout.addWidget(self.btn_refresh_boot)
        self.lbl_current_boot = QLabel("<i>Click to read current status...</i>")
        row_layout.addWidget(self.lbl_current_boot, 1)
        boot_layout.addRow(row_layout)

        # Boot Target Dropdown
        self.combo_boot_target = QComboBox()
        self.combo_boot_target.addItems([
            "None", "Pxe", "Cd", "Usb", "Hdd", "BiosSetup", "Utilities", "Diags", "SDCard"
        ])
        boot_layout.addRow("Boot Target:", self.combo_boot_target)

        # Boot State Dropdown
        self.combo_boot_state = QComboBox()
        self.combo_boot_state.addItems(["Once", "Continuous", "Disabled"])
        boot_layout.addRow("Override Mode:", self.combo_boot_state)

        # Apply Button
        self.btn_apply_boot = self._button("Set Next Boot Option", self.on_set_boot)
        boot_layout.addRow("", self.btn_apply_boot)

        boot_group.setLayout(boot_layout)
        layout.addWidget(boot_group)

        # --- Bottom Section: Output Log ---
        self.out_log = QTextEdit()
        self.out_log.setReadOnly(True)
        layout.addWidget(self.out_log, 1)

    # --- Power Logic ---
    def on_power_action(self, action_type):
        if not self.main.client:
            self.main.set_status("Not connected.", error=True)
            return
            
        self.main.set_status(f"Sending Power Command: {action_type}...")
        self.main._spawn_worker(
            lambda: self._execute_power_action(action_type),
            self._on_action_done
        )

    def _execute_power_action(self, action_type):
        """Finds the System endpoint and posts the Reset action."""
        if not self.system_endpoint:
            ok, resp, _ = self.main.client.get("/redfish/v1/Systems")
            payload = resp.get("data", {}) if isinstance(resp, dict) else {}
            
            if ok and "Members" in payload and len(payload["Members"]) > 0:
                self.system_endpoint = payload["Members"][0]["@odata.id"]
            else:
                return False, {"error": "Failed to discover ComputerSystem endpoint."}, 404

        action_payload = {"ResetType": action_type}
        return self.main.client.post_json(f"{self.system_endpoint}/Actions/ComputerSystem.Reset", action_payload)

    # --- Boot Options Logic ---
    def on_refresh_boot(self):
        if not self.main.client:
            self.main.set_status("Not connected.", error=True)
            return

        self.main.set_status("Reading Boot Options...")
        self.main._spawn_worker(self._get_system_info, self._on_refresh_boot_done)

    def _get_system_info(self):
        """Discovers the System endpoint and fetches its properties."""
        if not self.system_endpoint:
            ok, resp, _ = self.main.client.get("/redfish/v1/Systems")
            payload = resp.get("data", {}) if isinstance(resp, dict) else {}
            
            if ok and "Members" in payload and len(payload["Members"]) > 0:
                self.system_endpoint = payload["Members"][0]["@odata.id"]
            else:
                return False, {"error": "Failed to discover ComputerSystem endpoint."}, 404

        return self.main.client.get(self.system_endpoint)

    def _on_refresh_boot_done(self, ok, data):
        if ok and isinstance(data, dict):
            # Extract the actual JSON payload from the backend wrapper
            payload = data.get("data", {})
            
            # Parse Boot attributes out of the System response
            boot_info = payload.get("Boot", {})
            target = boot_info.get("BootSourceOverrideTarget", "Unknown")
            state = boot_info.get("BootSourceOverrideEnabled", "Unknown")
            mode = boot_info.get("BootSourceOverrideMode", "Unknown")

            self.lbl_current_boot.setText(f"<b>Target:</b> {target} | <b>State:</b> {state} | <b>Mode:</b> {mode}")
            
            # Auto-align the dropdowns to match the current state
            self.combo_boot_target.setCurrentText(target)
            self.combo_boot_state.setCurrentText(state)
            
            self.out_log.setPlainText(pretty(boot_info))
            self.main.set_status("Boot Options refreshed.")
        else:
            self.main.set_status("Failed to read System Boot options.", error=True)
            self.out_log.setPlainText(pretty(data))

    def on_set_boot(self):
        if not self.main.client:
            self.main.set_status("Not connected.", error=True)
            return

        target = self.combo_boot_target.currentText()
        state = self.combo_boot_state.currentText()
        
        self.main.set_status(f"Setting Next Boot to: {target} ({state})...")
        self.main._spawn_worker(
            lambda: self._patch_boot_options(target, state),
            self._on_action_done
        )

    def _patch_boot_options(self, target, state):
        if not self.system_endpoint:
            ok, resp, _ = self.main.client.get("/redfish/v1/Systems")
            payload = resp.get("data", {}) if isinstance(resp, dict) else {}
            
            if ok and "Members" in payload and len(payload["Members"]) > 0:
                self.system_endpoint = payload["Members"][0]["@odata.id"]
            else:
                return False, {"error": "Failed to discover ComputerSystem endpoint."}, 404

        action_payload = {
            "Boot": {
                "BootSourceOverrideTarget": target,
                "BootSourceOverrideEnabled": state
            }
        }
        return self.main.client.patch_json(self.system_endpoint, action_payload)

    # --- Generic Callback ---
    def _on_action_done(self, ok, data):
        self.out_log.setPlainText(pretty(data))
        if ok:
            self.main.set_status("Action successful.")
            # If it was a boot patch, dynamically refresh the label to confirm
            if "Boot" in str(data) or data == {}: 
                self.on_refresh_boot()
        else:
            self.main.set_status("Action failed.", error=True)