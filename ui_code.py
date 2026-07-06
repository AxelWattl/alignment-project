# ==========================================
# USER INTERFACE CODE
# ==========================================
# This file contains the PyQt6 GUI used to display camera feeds,
# show alignment status, select manual targets, and start/stop alignment.

import sys
import cv2

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QTabWidget, QDialog,
    QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox,
    QDialogButtonBox, QFrame, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

from functional_code import HardwareThread

# ==========================================
# 3. GUI CLASSES
# ==========================================

# QLabel subclass that displays a camera feed and reports click coordinates.
class ClickableCameraView(QLabel):
    clicked = pyqtSignal(str, int, int)

    # Sets up the camera display widget.
    def __init__(self, camera_name):
        super().__init__()
        self.camera_name = camera_name
        self.setText(f"{camera_name}\nLoading camera feed...")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(350, 300)
        self.setStyleSheet("background-color: black; color: white; border: 2px solid gray; font-size: 16px;")

    # Emits the clicked image location for manual targeting.
    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x, y = int(event.position().x()), int(event.position().y())
            self.clicked.emit(self.camera_name, x, y)

    # Converts an OpenCV frame into a scaled Qt pixmap.
    def update_image(self, cv_img):
        rgb_image = cv2.cvtColor(cv_img, cv2.COLOR_BGR2RGB)
        h, w, ch = rgb_image.shape
        bytes_per_line = ch * w
        qt_image = QImage(rgb_image.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        pixmap = QPixmap.fromImage(qt_image)
        self.setPixmap(pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

# Dialog for image saving and camera acquisition settings.
class SettingsDialog(QDialog):

    # Builds the settings form and confirmation buttons.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Settings")
        self.setMinimumWidth(350)
        layout = QFormLayout()

        self.save_images_checkbox = QCheckBox("Save images during alignment")
        self.save_interval_input = QSpinBox()
        self.save_interval_input.setRange(1, 60)
        self.save_interval_input.setSuffix(" sec")

        self.gain_input = QDoubleSpinBox()
        self.gain_input.setRange(0.0, 100.0)
        self.gain_input.setSingleStep(0.1)

        self.exposure_input = QSpinBox()
        self.exposure_input.setRange(1, 10000)
        self.exposure_input.setSuffix(" ms")

        layout.addRow("Save images:", self.save_images_checkbox)
        layout.addRow("Save interval:", self.save_interval_input)
        layout.addRow("Gain:", self.gain_input)
        layout.addRow("Exposure time:", self.exposure_input)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.setLayout(layout)

# Main GUI window for camera viewing, controls, and hardware status.
class AlignerApp(QMainWindow):

    # Initializes the GUI and starts the hardware thread.
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Laser Alignment System")
        self.setGeometry(100, 100, 1100, 650)
        
        self.save_images = False
        self.save_interval = 3
        self.gain = 0.0
        self.exposure_time = 7000

        self.current_x = 0.0
        self.current_y = 0.0
        self.target_camera = None
        self.target_x = None
        self.target_y = None
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)

        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)
        
        self.tabs.addTab(self.create_camera_tab(), "Cameras")
        self.tabs.addTab(self.create_logs_tab(), "Logs")

        alignment_panel = self.create_alignment_panel()
        main_layout.addWidget(alignment_panel)

        self.hw_thread = HardwareThread()
        self.hw_thread.frame_ready.connect(self.display_camera_frame)
        self.hw_thread.laser_pos_update.connect(self.update_current_position)
        self.hw_thread.log_msg.connect(self.log)
        self.hw_thread.status_msg.connect(self.set_status_message)
        self.hw_thread.start() 

        self.log("App started.")
        self.set_status_message("Cameras warming up...")

    # Creates the live camera display tab.
    def create_camera_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        
        self.camera_status_label = QLabel()
        self.camera_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_status_label.setStyleSheet("background-color: #2b2b2b; color: white; padding: 10px; border-radius: 6px; font-size: 16px; font-weight: bold;")
        layout.addWidget(self.camera_status_label)

        camera_row = QHBoxLayout()
        
        frame1 = QFrame()
        frame1.setFrameShape(QFrame.Shape.Box)
        layout1 = QVBoxLayout(frame1)
        layout1.addWidget(QLabel("<b>Camera 1</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        self.cam1_view = ClickableCameraView("Camera 1")
        self.cam1_view.clicked.connect(self.set_target_position)
        layout1.addWidget(self.cam1_view)
        
        frame2 = QFrame()
        frame2.setFrameShape(QFrame.Shape.Box)
        layout2 = QVBoxLayout(frame2)
        layout2.addWidget(QLabel("<b>Camera 2</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        self.cam2_view = ClickableCameraView("Camera 2")
        self.cam2_view.clicked.connect(self.set_target_position)
        layout2.addWidget(self.cam2_view)

        camera_row.addWidget(frame1)
        camera_row.addWidget(frame2)
        layout.addLayout(camera_row)
        return tab

    # Creates the system log tab.
    def create_logs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)
        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        layout.addWidget(self.log_box)
        return tab

    # Creates the right-side alignment control panel.
    def create_alignment_panel(self):
        panel = QFrame()
        panel.setFixedWidth(280)
        layout = QVBoxLayout()
        panel.setLayout(layout)

        layout.addWidget(QLabel("<b>Alignment Controls</b>", alignment=Qt.AlignmentFlag.AlignCenter))
        
        self.current_position_label = QLabel()
        self.target_position_label = QLabel()
        layout.addWidget(self.current_position_label)
        layout.addWidget(self.target_position_label)
        
        self.update_position_display()
        self.update_target_display()

        go_button = QPushButton("Go to Target")
        go_button.clicked.connect(self.go_to_target)
        layout.addWidget(go_button)

        start_btn = QPushButton("Start Alignment")
        stop_btn = QPushButton("Stop")
        start_btn.clicked.connect(self.start_alignment)
        stop_btn.clicked.connect(self.stop_alignment)
        layout.addWidget(start_btn)
        layout.addWidget(stop_btn)

        self.save_images_label = QLabel()
        layout.addWidget(self.save_images_label)
        self.update_settings_display()

        layout.addStretch()
        return panel

    # Opens the settings dialog and applies accepted changes.
    def open_settings(self):
        dialog = SettingsDialog()
        dialog.save_images_checkbox.setChecked(self.save_images)
        dialog.save_interval_input.setValue(self.save_interval)
        dialog.gain_input.setValue(self.gain)
        dialog.exposure_input.setValue(self.exposure_time)

        if dialog.exec():
            self.save_images = dialog.save_images_checkbox.isChecked()
            self.save_interval = dialog.save_interval_input.value()
            self.gain = dialog.gain_input.value()
            self.exposure_time = dialog.exposure_input.value()
            
            self.update_settings_display()
            
            self.hw_thread.save_images = self.save_images
            self.hw_thread.save_interval = self.save_interval
            self.hw_thread.update_camera_settings(self.exposure_time, self.gain)

    # Routes incoming frames to the correct camera widget.
    def display_camera_frame(self, cam_index, img_data):
        if cam_index == 0: self.cam1_view.update_image(img_data)
        elif cam_index == 1: self.cam2_view.update_image(img_data)

    # Updates the displayed laser coordinate.
    def update_current_position(self, cam_idx, x, y):
        self.current_x = x
        self.current_y = y
        self.update_position_display(f"Camera {cam_idx + 1}")

    # Stores a clicked target point for manual alignment.
    def set_target_position(self, camera_name, x, y):
        self.target_camera = camera_name
        self.target_x = x
        self.target_y = y
        self.update_target_display()
        self.set_status_message(f"Target selected on {camera_name}: X={x}, Y={y}")

    # Commands the hardware thread to move toward the selected target.
    def go_to_target(self):
        if self.target_x is None:
            self.set_status_message("Select a target first!")
            return
        
        cam_idx = 0 if self.target_camera == "Camera 1" else 1
        
        self.log(f"Manual override: Target set for {self.target_camera} at X={self.target_x}, Y={self.target_y}")
        self.set_status_message(f"Executing manual target move on {self.target_camera}...")
        
        self.hw_thread.execute_manual_move(cam_idx, self.target_x, self.target_y)

    # Refreshes the current-position label.
    def update_position_display(self, active_cam="None"):
        self.current_position_label.setText(f"Laser Tracking ({active_cam}):\nX = {self.current_x}\nY = {self.current_y}")

    # Refreshes the selected-target label.
    def update_target_display(self):
        if self.target_x is None:
            self.target_position_label.setText("Target position:\nNone selected")
        else:
            self.target_position_label.setText(f"Target position ({self.target_camera}):\nX = {self.target_x}\nY = {self.target_y}")

    # Refreshes the image-saving status label.
    def update_settings_display(self):
        if self.save_images:
            self.save_images_label.setText(f"Save images: ON ({self.save_interval}s)")
        else:
            self.save_images_label.setText("Save images: OFF")

    # Resets lock states and starts automatic alignment.
    def start_alignment(self):
        self.hw_thread.manual_target_active = False
        self.hw_thread.is_aligning = True

        self.hw_thread.cam1_locked = False
        self.hw_thread.cam2_locked = False
        self.hw_thread.cam1_stable_count = 0
        self.hw_thread.cam2_stable_count = 0
        self.hw_thread.cam1_drift_count = 0
        self.hw_thread.cam2_drift_count = 0
        self.hw_thread.system_locked_stop_sent = False
        self.hw_thread.alignment_cooldown = 0.0

        self.set_status_message("Auto-alignment Running...")
        self.log("Started alignment algorithm.")

    # Stops active alignment and motor motion.
    def stop_alignment(self):
        self.hw_thread.stop_all_movement()
        self.log("Stopped all alignment and halted motors.")

    # Updates the top status message.
    def set_status_message(self, msg):
        self.camera_status_label.setText(msg)

    # Writes a message to both the GUI log and terminal.
    def log(self, msg):
        if hasattr(self, "log_box"):
            self.log_box.append(msg)
        print(msg)

    # Ensures hardware is closed before the GUI exits.
    def closeEvent(self, event):
        self.log("Shutting down hardware cleanly...")
        self.hw_thread.stop()
        event.accept()

# ==========================================
# PROGRAM ENTRY POINT
# ==========================================
# Starts the Qt application and displays the main window.
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = AlignerApp()
    window.show()
    sys.exit(app.exec())
