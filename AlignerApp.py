import sys

from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QToolButton,
    QDialog,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QComboBox,
    QCheckBox,
    QDialogButtonBox,
    QFrame,
    QTextEdit,
)

from PyQt6.QtCore import Qt, pyqtSignal


class ClickableCameraView(QLabel):
    clicked = pyqtSignal(str, int, int)

    def __init__(self, camera_name):
        super().__init__()

        self.camera_name = camera_name

        self.setText(f"{camera_name}\nClick to select target position")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(350, 300)

        self.setStyleSheet(
            """
            QLabel {
                background-color: black;
                color: white;
                border: 2px solid gray;
                font-size: 16px;
            }
            """
        )

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            x = int(event.position().x())
            y = int(event.position().y())

            self.setText(
                f"{self.camera_name}\n"
                f"Target selected\n"
                f"X = {x}, Y = {y}"
            )

            self.clicked.emit(self.camera_name, x, y)


class SettingsDialog(QDialog):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Settings")
        self.setMinimumWidth(350)

        layout = QFormLayout()

        self.save_images_checkbox = QCheckBox("Save images during alignment")

        self.save_interval_input = QSpinBox()
        self.save_interval_input.setRange(1, 60)
        self.save_interval_input.setValue(3)
        self.save_interval_input.setSuffix(" sec")

        self.gain_input = QDoubleSpinBox()
        self.gain_input.setRange(0.0, 100.0)
        self.gain_input.setValue(1.0)
        self.gain_input.setSingleStep(0.1)

        self.colorscale_input = QComboBox()
        self.colorscale_input.addItems(
            [
                "Grayscale",
                "Viridis",
                "Plasma",
                "Inferno",
                "Jet",
            ]
        )

        self.exposure_input = QSpinBox()
        self.exposure_input.setRange(1, 10000)
        self.exposure_input.setValue(100)
        self.exposure_input.setSuffix(" ms")

        layout.addRow("Save images:", self.save_images_checkbox)
        layout.addRow("Save interval:", self.save_interval_input)
        layout.addRow("Gain:", self.gain_input)
        layout.addRow("Colorscale:", self.colorscale_input)
        layout.addRow("Exposure time:", self.exposure_input)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )

        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

        self.setLayout(layout)


class AlignerApp(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Aligner App")
        self.setGeometry(100, 100, 1100, 650)

        # Settings
        self.save_images = False
        self.save_interval = 3
        self.gain = 1.0
        self.colorscale = "Grayscale"
        self.exposure_time = 100

        # Position data
        self.current_x = 0.0
        self.current_y = 0.0

        self.target_camera = None
        self.target_x = None
        self.target_y = None

        # Main central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)

        # Left side: tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Settings cog in the top-right of the tab bar
        settings_button = QToolButton()
        settings_button.setText("⚙")
        settings_button.setToolTip("Settings")
        settings_button.clicked.connect(self.open_settings)

        self.tabs.setCornerWidget(settings_button, Qt.Corner.TopRightCorner)

        self.tabs.addTab(self.create_camera_tab(), "Cameras")
        self.tabs.addTab(self.create_logs_tab(), "Logs")

        # Right side: alignment controls
        alignment_panel = self.create_alignment_panel()
        main_layout.addWidget(alignment_panel)

        self.log("App started.")
        self.set_status_message("Please focus camera before starting alignment.")

    def create_camera_tab(self):
        tab = QWidget()
        main_layout = QVBoxLayout()
        tab.setLayout(main_layout)

        # Status/instruction banner above cameras
        self.camera_status_label = QLabel("Please focus camera before starting alignment.")
        self.camera_status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.camera_status_label.setStyleSheet(
            """
            QLabel {
                background-color: #2b2b2b;
                color: white;
                padding: 10px;
                border-radius: 6px;
                font-size: 16px;
                font-weight: bold;
            }
            """
        )

        main_layout.addWidget(self.camera_status_label)

        # Camera views
        camera_row = QHBoxLayout()

        camera_1_frame = self.create_camera_frame("Camera 1")
        camera_2_frame = self.create_camera_frame("Camera 2")

        camera_row.addWidget(camera_1_frame)
        camera_row.addWidget(camera_2_frame)

        main_layout.addLayout(camera_row)

        return tab

    def create_camera_frame(self, title):
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.Box)
        frame.setMinimumSize(350, 300)

        layout = QVBoxLayout()
        frame.setLayout(layout)

        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-weight: bold; font-size: 16px;")

        image_label = ClickableCameraView(title)
        image_label.clicked.connect(self.set_target_position)

        layout.addWidget(title_label)
        layout.addWidget(image_label)

        return frame

    def create_logs_tab(self):
        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)

        layout.addWidget(self.log_box)

        return tab

    def create_alignment_panel(self):
        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)
        panel.setFixedWidth(280)

        layout = QVBoxLayout()
        panel.setLayout(layout)

        title = QLabel("Alignment Controls")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        self.current_position_label = QLabel()
        self.target_position_label = QLabel()

        layout.addWidget(self.current_position_label)
        layout.addWidget(self.target_position_label)

        self.update_position_display()
        self.update_target_display()

        go_button = QPushButton("Go to Target")
        go_button.clicked.connect(self.go_to_target)
        layout.addWidget(go_button)

        start_button = QPushButton("Start Alignment")
        stop_button = QPushButton("Stop")

        start_button.clicked.connect(self.start_alignment)
        stop_button.clicked.connect(self.stop_alignment)

        layout.addWidget(start_button)
        layout.addWidget(stop_button)

        self.exposure_label = QLabel()
        self.gain_label = QLabel()
        self.colorscale_label = QLabel()
        self.save_images_label = QLabel()

        layout.addWidget(self.exposure_label)
        layout.addWidget(self.gain_label)
        layout.addWidget(self.colorscale_label)
        layout.addWidget(self.save_images_label)

        self.update_settings_display()

        layout.addStretch()

        return panel

    def set_status_message(self, message):
        self.camera_status_label.setText(message)
        self.log(message)

    def set_target_position(self, camera_name, x, y):
        self.target_camera = camera_name
        self.target_x = x
        self.target_y = y

        self.update_target_display()

        self.set_status_message(
            f"Target selected on {camera_name}. Press Go to Target when ready."
        )

        self.log(f"Target selected on {camera_name}: X = {x}, Y = {y}")

    def go_to_target(self):
        if self.target_x is None or self.target_y is None:
            self.set_status_message("No target selected. Click on a camera image first.")
            return

        self.set_status_message("Moving to selected target...")

        self.log(
            f"Going to target from current position "
            f"X = {self.current_x:.2f}, Y = {self.current_y:.2f}"
        )

        self.log(
            f"Target from {self.target_camera}: "
            f"X = {self.target_x}, Y = {self.target_y}"
        )

        # Placeholder behavior:
        # Later, replace this with your real stage/motor movement code.
        self.current_x = float(self.target_x)
        self.current_y = float(self.target_y)

        self.update_position_display()

        self.set_status_message("Arrived at target position.")

        self.log(
            f"Arrived at new current position: "
            f"X = {self.current_x:.2f}, Y = {self.current_y:.2f}"
        )

    def start_alignment(self):
        self.set_status_message("Alignment running...")

        self.log("Starting alignment...")

        if self.save_images:
            self.log(
                f"Image saving is ON. Saving every {self.save_interval} seconds."
            )
        else:
            self.log("Image saving is OFF. Images will be displayed but not saved.")

        self.log(
            f"Using gain = {self.gain}, "
            f"exposure = {self.exposure_time} ms, "
            f"colorscale = {self.colorscale}"
        )

    def stop_alignment(self):
        self.set_status_message("Alignment stopped.")
        self.log("Stopping alignment...")

    def open_settings(self):
        dialog = SettingsDialog()

        # Load current values into dialog
        dialog.save_images_checkbox.setChecked(self.save_images)
        dialog.save_interval_input.setValue(self.save_interval)
        dialog.gain_input.setValue(self.gain)
        dialog.colorscale_input.setCurrentText(self.colorscale)
        dialog.exposure_input.setValue(self.exposure_time)

        if dialog.exec():
            # Save dialog values back to app
            self.save_images = dialog.save_images_checkbox.isChecked()
            self.save_interval = dialog.save_interval_input.value()
            self.gain = dialog.gain_input.value()
            self.colorscale = dialog.colorscale_input.currentText()
            self.exposure_time = dialog.exposure_input.value()

            self.update_settings_display()

            self.set_status_message("Settings updated.")

            self.log(f"Save images: {self.save_images}")
            self.log(f"Save interval: {self.save_interval} sec")
            self.log(f"Gain: {self.gain}")
            self.log(f"Colorscale: {self.colorscale}")
            self.log(f"Exposure time: {self.exposure_time} ms")

    def update_position_display(self):
        self.current_position_label.setText(
            f"Current position:\n"
            f"X = {self.current_x:.2f}\n"
            f"Y = {self.current_y:.2f}"
        )

    def update_target_display(self):
        if self.target_x is None or self.target_y is None:
            self.target_position_label.setText(
                "Target position:\nNone selected"
            )
        else:
            self.target_position_label.setText(
                f"Target position:\n"
                f"{self.target_camera}\n"
                f"X = {self.target_x}\n"
                f"Y = {self.target_y}"
            )

    def update_settings_display(self):
        self.exposure_label.setText(f"Exposure: {self.exposure_time} ms")
        self.gain_label.setText(f"Gain: {self.gain}")
        self.colorscale_label.setText(f"Colorscale: {self.colorscale}")

        if self.save_images:
            self.save_images_label.setText(
                f"Save images: ON, every {self.save_interval} sec"
            )
        else:
            self.save_images_label.setText("Save images: OFF")

    def log(self, message):
        print(message)

        if hasattr(self, "log_box"):
            self.log_box.append(message)


app = QApplication(sys.argv)

window = AlignerApp()
window.show()

sys.exit(app.exec())