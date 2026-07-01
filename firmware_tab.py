# -*- coding: utf-8 -*-
"""
Firmware Update UI Component using Asyncio and Redfish Library
"""
import asyncio
import redfish
from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, 
    QTextEdit, QFileDialog, QProgressBar, QGroupBox
)

# --- 1. Background Async Worker ---
class AsyncFirmwareWorker(QThread):
    """Wraps the asyncio Redfish update process in a PyQt Thread."""
    
    # Signals to communicate with the GUI safely
    progress_signal = pyqtSignal(float)
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal(bool)

    def __init__(self, ip, user, password, fw_path):
        super().__init__()
        self.ip = ip
        self.user = user
        self.password = password
        self.fw_path = fw_path

    def run(self):
        """Creates a new event loop for this thread and runs the async process."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Read the binary firmware file
            with open(self.fw_path, "rb") as f:
                fw_content = f.read()
                
            loop.run_until_complete(self.bmc_update(
                self.user, 
                self.password, 
                self.ip, 
                fw_content, 
                self.progress_callback, 
                self.log_callback
            ))
            self.finished_signal.emit(True)
        except Exception as e:
            self.log_callback(f"CRITICAL ERROR: {str(e)}")
            self.finished_signal.emit(False)
        finally:
            loop.close()

    # --- Callbacks mapped to Qt Signals ---
    def progress_callback(self, value: float):
        self.progress_signal.emit(value)

    def log_callback(self, message: str):
        self.log_signal.emit(message)

    # --- Task Monitor ---
    async def monitor_task(self, redfish_client, task_url, callback_output, callback_progress):
        callback_output(f"Monitoring backend task: {task_url}")
        
        while True:
            resp = await asyncio.to_thread(redfish_client.get, task_url)
            if resp.status == 200:
                state = resp.dict.get("TaskState", "Unknown")
                status = resp.dict.get("TaskStatus", "Unknown")
                
                callback_output(f" -> Task State: {state} | Status: {status}")
                
                if state == "Completed":
                    callback_output("Firmware update task completed successfully.")
                    callback_progress(1.0)
                    break
                elif state in ["Exception", "Killed", "Cancelled"]:
                    callback_output(f"Task aborted with state: {state}")
                    break
            else:
                callback_output(f"Failed to fetch task status. HTTP {resp.status}")
                break
                
            await asyncio.sleep(5) # Poll every 5 seconds

    # --- Your Core Update Logic ---
    async def bmc_update(self, bmc_user, bmc_pass, bmc_ip, fw_content, callback_progress, callback_output):
        callback_output("Initializing Redfish client...")
        redfish_client = redfish.redfish_client(base_url=f"https://{bmc_ip}", username=bmc_user, password=bmc_pass)
        callback_progress(0.25)
        
        try:
            await asyncio.to_thread(redfish_client.login)
            update_service = await asyncio.to_thread(redfish_client.get, "/redfish/v1/UpdateService")
            
            if update_service.status != 200:
                callback_output("Failed to find the update service.")
                return

            callback_progress(0.50)
            callback_output("Logged in.")

            update_service_url = update_service.dict["@odata.id"]
            headers = {"Content-Type": "application/octet-stream"}
            
            callback_output("Sending update payload... (This may take a minute)")
            
            # Note: Appending '/update' to the UpdateService URL per your design
            target_uri = f"{update_service_url}/update"
            response = await asyncio.to_thread(redfish_client.post, target_uri, body=fw_content, headers=headers)
            
            callback_progress(0.75)

            if response.status in [200, 202]:
                callback_output(f"Update initiated successfully: {response.text}")
                
                # Check for Task Monitor Location header OR in the body
                task_url = response.dict.get("@odata.id")
                if not task_url and "Location" in response.getheaders():
                    task_url = response.getheader("Location")
                
                if task_url:
                    await self.monitor_task(redfish_client, task_url, callback_output, callback_progress)
                else:
                    callback_output("No Task Monitor returned by BMC. Assume update is processing in background.")
                    callback_progress(1.0)
            else:
                callback_output(f"Failed to initiate firmware update. Response code: {response.status}")
                
        except Exception as e:
            callback_output(f"Error: {e}")
        finally:
            await asyncio.to_thread(redfish_client.logout)
        
        await asyncio.sleep(2)


# --- 2. The GUI Tab ---
class FirmwareTab(QWidget):
    def __init__(self, main_window):
        super().__init__()
        self.main = main_window
        self.worker = None
        self._build_ui()

    def _build_ui(self):
        layout = QVBoxLayout(self)
        
        group = QGroupBox("Single BMC Firmware Update")
        group_layout = QVBoxLayout(group)
        
        # File Picker
        row1 = QHBoxLayout()
        row1.addWidget(QLabel("Firmware Image (.bin/.tar):"))
        self.fw_path_in = QLineEdit()
        self.fw_path_in.setPlaceholderText("Select a local firmware file...")
        row1.addWidget(self.fw_path_in, 1)
        
        btn_browse = QPushButton("Browse...")
        btn_browse.clicked.connect(self.on_browse)
        row1.addWidget(btn_browse)
        group_layout.addLayout(row1)
        
        # Progress Bar & Start Button
        row2 = QHBoxLayout()
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        row2.addWidget(self.progress_bar, 1)
        
        self.btn_start = QPushButton("Start Update")
        self.btn_start.clicked.connect(self.on_start_update)
        row2.addWidget(self.btn_start)
        group_layout.addLayout(row2)
        
        layout.addWidget(group)
        
        # Output Log
        layout.addWidget(QLabel("Update Logs:"))
        self.log_out = QTextEdit()
        self.log_out.setReadOnly(True)
        layout.addWidget(self.log_out, 1)

    def on_browse(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Select Firmware Image", "", "Firmware (*.bin *.tar *.tar.gz);;All Files (*)"
        )
        if file_path:
            self.fw_path_in.setText(file_path)

    def on_start_update(self):
        fw_path = self.fw_path_in.text().strip()
        
        # Pull credentials dynamically from your Main Window connection bar
        ip = self.main.host_in.text().strip()
        user = self.main.user_in.text().strip()
        password = self.main.pass_in.text().strip()
        
        if not ip or not user or not password:
            self.append_log("[ERROR] Please fill out BMC Connection details in the top bar first.")
            return
            
        if not fw_path:
            self.append_log("[ERROR] Please select a firmware file.")
            return

        self.btn_start.setEnabled(False)
        self.progress_bar.setValue(0)
        self.log_out.clear()
        self.append_log(f"Starting async firmware update for {ip}...")

        # Initialize and start the background thread
        self.worker = AsyncFirmwareWorker(ip, user, password, fw_path)
        self.worker.log_signal.connect(self.append_log)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.finished_signal.connect(self.on_worker_finished)
        self.worker.start()

    def append_log(self, msg):
        self.log_out.append(msg)

    def update_progress(self, val: float):
        # Convert the float (0.0 - 1.0) to percentage for the progress bar
        percentage = int(val * 100)
        self.progress_bar.setValue(percentage)

    def on_worker_finished(self, success):
        self.btn_start.setEnabled(True)
        if success:
            self.append_log("\n--- Firmware Update Routine Finished ---")
        else:
            self.append_log("\n--- Firmware Update Failed ---")