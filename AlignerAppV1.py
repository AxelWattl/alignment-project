import sys

# PyQt6 widgets are the visible GUI parts of the program.
# Each widget is one piece of the interface: windows, buttons, labels, tabs, etc.
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

# Qt contains alignment flags, mouse button constants, and other core Qt settings.
# pyqtSignal lets one widget send data/events to another part of the program.
from PyQt6.QtCore import Qt, pyqtSignal


class ClickableCameraView(QLabel):
    """
    A QLabel that acts like a simple camera display.

    Right now this does not show a real camera image. It is a placeholder black
    box that can be clicked. When the user clicks it, the widget records the
    pixel coordinates and emits a signal containing:

        camera name, x coordinate, y coordinate

    Later, this class is where you would likely display actual frames from
    Camera 1 and Camera 2.
    """

    # Custom signal emitted when the user clicks the camera view.
    # The signal sends:
    #   str -> camera name, like "Camera 1"
    #   int -> x pixel coordinate of click
    #   int -> y pixel coordinate of click
    clicked = pyqtSignal(str, int, int)

    def __init__(self, camera_name):
        # Initialize QLabel parent class.
        super().__init__()

        # Store which camera this view represents.
        # This is important because both camera views use the same class.
        self.camera_name = camera_name

        # Placeholder text shown before an image/target is selected.
        self.setText(f"{camera_name}\nClick to select target position")

        # Center the text inside the black camera placeholder.
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Give the camera display a minimum size so the GUI is usable.
        self.setMinimumSize(350, 300)

        # Style the QLabel so it looks like a camera screen.
        # This can be replaced later with actual image display logic.
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
        """
        Runs automatically whenever the user clicks inside this QLabel.

        This method converts the mouse click location into x/y pixel coordinates.
        Those coordinates are then sent to the main app through the custom
        clicked signal.
        """

        # Only respond to left mouse clicks.
        if event.button() == Qt.MouseButton.LeftButton:
            # event.position() gives the click position inside this widget.
            # Convert to int because pixel coordinates should be whole numbers.
            x = int(event.position().x())
            y = int(event.position().y())

            # Update the camera placeholder text so the user sees what they clicked.
            self.setText(
                f"{self.camera_name}\n"
                f"Target selected\n"
                f"X = {x}, Y = {y}"
            )

            # Emit the custom signal so AlignerApp can store the selected target.
            self.clicked.emit(self.camera_name, x, y)


class SettingsDialog(QDialog):
    """
    Popup settings window.

    This dialog lets the user change display/acquisition settings such as:
    - whether to save images
    - how often to save images
    - gain
    - colorscale
    - exposure time

    The dialog itself does not permanently save anything. It only holds the
    input widgets. The main window reads the values if the user presses OK.
    """

    def __init__(self):
        # Initialize QDialog parent class.
        super().__init__()

        # Window title shown at the top of the popup.
        self.setWindowTitle("Settings")

        # Make the dialog wide enough that labels and inputs do not feel cramped.
        self.setMinimumWidth(350)

        # QFormLayout creates rows like:
        #   label: input widget
        layout = QFormLayout()

        # Checkbox for whether images should be saved during alignment.
        self.save_images_checkbox = QCheckBox("Save images during alignment")

        # Spin box for image save interval in seconds.
        self.save_interval_input = QSpinBox()
        self.save_interval_input.setRange(1, 60)  # allowed values: 1 to 60 seconds
        self.save_interval_input.setValue(3)      # default value
        self.save_interval_input.setSuffix(" sec")

        # Double spin box allows decimal values for gain.
        self.gain_input = QDoubleSpinBox()
        self.gain_input.setRange(0.0, 100.0)  # allowed gain range
        self.gain_input.setValue(1.0)         # default gain
        self.gain_input.setSingleStep(0.1)    # button increments/decrements by 0.1

        # Drop-down menu for choosing how the camera image should be colored.
        # These are display choices, not hardware settings.
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

        # Spin box for camera exposure time in milliseconds.
        self.exposure_input = QSpinBox()
        self.exposure_input.setRange(1, 10000)  # allowed exposure: 1 ms to 10 s
        self.exposure_input.setValue(100)       # default exposure
        self.exposure_input.setSuffix(" ms")

        # Add the settings widgets to the form layout.
        layout.addRow("Save images:", self.save_images_checkbox)
        layout.addRow("Save interval:", self.save_interval_input)
        layout.addRow("Gain:", self.gain_input)
        layout.addRow("Colorscale:", self.colorscale_input)
        layout.addRow("Exposure time:", self.exposure_input)

        # Standard OK and Cancel buttons.
        # OK accepts the dialog; Cancel rejects it.
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )

        # Connect button behavior to the built-in accept/reject functions.
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        # Add the button row to the bottom of the dialog.
        layout.addWidget(buttons)

        # Attach the layout to the dialog window.
        self.setLayout(layout)


class AlignerApp(QMainWindow):
    """
    Main GUI window for the laser/camera alignment app.

    This class builds the full interface:
    - camera tab
    - logs tab
    - settings button
    - right-side alignment control panel
    - target position selection
    - placeholder movement behavior

    Right now this is mostly a front-end prototype. The real camera feed,
    motor movement, and alignment algorithm would be connected inside methods
    such as go_to_target(), start_alignment(), and stop_alignment().
    """

    def __init__(self):
        # Initialize QMainWindow parent class.
        super().__init__()

        # Set the title and starting size/location of the main window.
        self.setWindowTitle("Aligner App")
        self.setGeometry(100, 100, 1100, 650)

        # -----------------------------
        # Stored settings
        # -----------------------------
        # These variables hold the current app settings.
        # They are updated when the user opens Settings and presses OK.
        self.save_images = False
        self.save_interval = 3
        self.gain = 1.0
        self.colorscale = "Grayscale"
        self.exposure_time = 100

        # -----------------------------
        # Current motor/stage position
        # -----------------------------
        # These are placeholders for the current physical position.
        # Later, these should come from the real motor/stage controller.
        self.current_x = 0.0
        self.current_y = 0.0

        # -----------------------------
        # Selected target position
        # -----------------------------
        # These start as None because the user has not clicked a camera yet.
        self.target_camera = None
        self.target_x = None
        self.target_y = None

        # Main central widget is required for QMainWindow layouts.
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main layout divides the app horizontally:
        #   left  -> tab area
        #   right -> alignment controls
        main_layout = QHBoxLayout()
        central_widget.setLayout(main_layout)

        # -----------------------------
        # Left side: tab widget
        # -----------------------------
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # Small settings button placed in the top-right corner of the tab bar.
        settings_button = QToolButton()
        settings_button.setText("⚙")
        settings_button.setToolTip("Settings")
        settings_button.clicked.connect(self.open_settings)

        # Places the gear button on the tab widget corner.
        self.tabs.setCornerWidget(settings_button, Qt.Corner.TopRightCorner)

        # Add the two main tabs.
        self.tabs.addTab(self.create_camera_tab(), "Cameras")
        self.tabs.addTab(self.create_logs_tab(), "Logs")

        # -----------------------------
        # Right side: alignment controls
        # -----------------------------
        alignment_panel = self.create_alignment_panel()
        main_layout.addWidget(alignment_panel)

        # Startup messages.
        # log() prints to terminal and also writes to the Logs tab.
        self.log("App started.")

        # Status message appears above the cameras.
        self.set_status_message("Please focus camera before starting alignment.")

    def create_camera_tab(self):
        """
        Creates the Cameras tab.

        The tab contains:
        - a status/instruction banner
        - Camera 1 display
        - Camera 2 display
        """

        tab = QWidget()
        main_layout = QVBoxLayout()
        tab.setLayout(main_layout)

        # Status/instruction banner above cameras.
        # This gives the user guidance such as "focus camera" or "alignment running".
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

        # Horizontal layout for the two camera views.
        camera_row = QHBoxLayout()

        # Create frames for each camera.
        camera_1_frame = self.create_camera_frame("Camera 1")
        camera_2_frame = self.create_camera_frame("Camera 2")

        # Add both camera frames side-by-side.
        camera_row.addWidget(camera_1_frame)
        camera_row.addWidget(camera_2_frame)

        # Add the camera row below the status banner.
        main_layout.addLayout(camera_row)

        return tab

    def create_camera_frame(self, title):
        """
        Creates one camera frame.

        A camera frame contains:
        - a title label
        - a clickable camera view

        The clickable camera view sends its selected coordinate back to the
        main app using the set_target_position() method.
        """

        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.Box)
        frame.setMinimumSize(350, 300)

        layout = QVBoxLayout()
        frame.setLayout(layout)

        # Camera title shown above the camera display.
        title_label = QLabel(title)
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setStyleSheet("font-weight: bold; font-size: 16px;")

        # Placeholder camera display.
        # Later, this widget can be updated to show real camera frames.
        image_label = ClickableCameraView(title)

        # When the user clicks the camera view, call set_target_position().
        image_label.clicked.connect(self.set_target_position)

        layout.addWidget(title_label)
        layout.addWidget(image_label)

        return frame

    def create_logs_tab(self):
        """
        Creates the Logs tab.

        The log box shows status updates and debugging messages. This is useful
        while testing because the user can see what the app thinks is happening.
        """

        tab = QWidget()
        layout = QVBoxLayout()
        tab.setLayout(layout)

        # QTextEdit is used instead of QLabel because logs can grow over time.
        self.log_box = QTextEdit()

        # Make it read-only so the user does not accidentally edit the logs.
        self.log_box.setReadOnly(True)

        layout.addWidget(self.log_box)

        return tab

    def create_alignment_panel(self):
        """
        Creates the right-side control panel.

        This panel shows:
        - current position
        - selected target position
        - Go to Target button
        - Start/Stop alignment buttons
        - current settings summary
        """

        panel = QFrame()
        panel.setFrameShape(QFrame.Shape.StyledPanel)

        # Fixed width keeps the control panel from stretching too much.
        panel.setFixedWidth(280)

        layout = QVBoxLayout()
        panel.setLayout(layout)

        # Panel title.
        title = QLabel("Alignment Controls")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        layout.addWidget(title)

        # Labels that will be updated by update_position_display()
        # and update_target_display().
        self.current_position_label = QLabel()
        self.target_position_label = QLabel()

        layout.addWidget(self.current_position_label)
        layout.addWidget(self.target_position_label)

        # Fill labels with initial values.
        self.update_position_display()
        self.update_target_display()

        # Button for moving to the user-selected target coordinate.
        go_button = QPushButton("Go to Target")
        go_button.clicked.connect(self.go_to_target)
        layout.addWidget(go_button)

        # Buttons for starting/stopping the alignment process.
        start_button = QPushButton("Start Alignment")
        stop_button = QPushButton("Stop")

        start_button.clicked.connect(self.start_alignment)
        stop_button.clicked.connect(self.stop_alignment)

        layout.addWidget(start_button)
        layout.addWidget(stop_button)

        # Settings summary labels shown in the side panel.
        self.exposure_label = QLabel()
        self.gain_label = QLabel()
        self.colorscale_label = QLabel()
        self.save_images_label = QLabel()

        layout.addWidget(self.exposure_label)
        layout.addWidget(self.gain_label)
        layout.addWidget(self.colorscale_label)
        layout.addWidget(self.save_images_label)

        # Fill settings labels with initial values.
        self.update_settings_display()

        # Pushes everything upward so controls do not spread out weirdly.
        layout.addStretch()

        return panel

    def set_status_message(self, message):
        """
        Updates the status banner above the cameras and records the same message
        in the log.

        This keeps the visual status and debug log consistent.
        """

        self.camera_status_label.setText(message)
        self.log(message)

    def set_target_position(self, camera_name, x, y):
        """
        Stores the selected target position after the user clicks a camera view.

        Parameters:
            camera_name: which camera was clicked
            x: x pixel coordinate inside the camera view
            y: y pixel coordinate inside the camera view
        """

        # Store the selected camera and target coordinates.
        self.target_camera = camera_name
        self.target_x = x
        self.target_y = y

        # Refresh the right-side target display.
        self.update_target_display()

        # Tell the user what to do next.
        self.set_status_message(
            f"Target selected on {camera_name}. Press Go to Target when ready."
        )

        # Add exact coordinate information to the log.
        self.log(f"Target selected on {camera_name}: X = {x}, Y = {y}")

    def go_to_target(self):
        """
        Moves to the selected target position.

        Right now this method only simulates motion by setting current_x/current_y
        equal to the clicked target coordinates.

        Later, this is where you would call the real motor/stage function, for example:
            motor.move_to(x=self.target_x, y=self.target_y)

        You may also need to convert camera pixel coordinates into motor coordinates
        before moving.
        """

        # Do not move if the user has not selected a target yet.
        if self.target_x is None or self.target_y is None:
            self.set_status_message("No target selected. Click on a camera image first.")
            return

        self.set_status_message("Moving to selected target...")

        # Log the starting position.
        self.log(
            f"Going to target from current position "
            f"X = {self.current_x:.2f}, Y = {self.current_y:.2f}"
        )

        # Log the target position.
        self.log(
            f"Target from {self.target_camera}: "
            f"X = {self.target_x}, Y = {self.target_y}"
        )

        # -----------------------------
        # Placeholder behavior
        # -----------------------------
        # This does NOT move any real hardware.
        # It only updates the displayed current position.
        #
        # Replace this section later with:
        #   1. coordinate transform from camera pixels to stage units
        #   2. motor command
        #   3. wait/check until the move finishes
        #   4. read back actual stage position
        self.current_x = float(self.target_x)
        self.current_y = float(self.target_y)

        # Update the displayed current position in the side panel.
        self.update_position_display()

        self.set_status_message("Arrived at target position.")

        self.log(
            f"Arrived at new current position: "
            f"X = {self.current_x:.2f}, Y = {self.current_y:.2f}"
        )

    def start_alignment(self):
        """
        Starts the alignment process.

        Right now this only logs that alignment has started. Later, this method
        should connect to your actual alignment algorithm.

        Possible future responsibilities:
        - start camera acquisition
        - start automatic beam detection
        - send corrections to the motors
        - save images if save_images is enabled
        - update the GUI while alignment is running
        """

        self.set_status_message("Alignment running...")

        self.log("Starting alignment...")

        # Log whether images will be saved during the alignment process.
        if self.save_images:
            self.log(
                f"Image saving is ON. Saving every {self.save_interval} seconds."
            )
        else:
            self.log("Image saving is OFF. Images will be displayed but not saved.")

        # Log the current camera/display settings used by the alignment process.
        self.log(
            f"Using gain = {self.gain}, "
            f"exposure = {self.exposure_time} ms, "
            f"colorscale = {self.colorscale}"
        )

    def stop_alignment(self):
        """
        Stops the alignment process.

        Right now this only updates the status and log. Later, this should stop
        any running timers, camera acquisition loops, motor movement, or alignment
        algorithm threads.
        """

        self.set_status_message("Alignment stopped.")
        self.log("Stopping alignment...")

    def open_settings(self):
        """
        Opens the Settings dialog.

        The process is:
        1. Create a SettingsDialog.
        2. Load the current app settings into the dialog widgets.
        3. Show the dialog.
        4. If the user presses OK, copy the dialog values back into the app.
        5. Update the settings display and logs.
        """

        dialog = SettingsDialog()

        # Load current app values into the dialog so it opens with the latest settings.
        dialog.save_images_checkbox.setChecked(self.save_images)
        dialog.save_interval_input.setValue(self.save_interval)
        dialog.gain_input.setValue(self.gain)
        dialog.colorscale_input.setCurrentText(self.colorscale)
        dialog.exposure_input.setValue(self.exposure_time)

        # dialog.exec() blocks until the user clicks OK or Cancel.
        # It returns True if the user accepted the dialog.
        if dialog.exec():
            # Save dialog values back to the main app.
            self.save_images = dialog.save_images_checkbox.isChecked()
            self.save_interval = dialog.save_interval_input.value()
            self.gain = dialog.gain_input.value()
            self.colorscale = dialog.colorscale_input.currentText()
            self.exposure_time = dialog.exposure_input.value()

            # Refresh the right-side settings labels.
            self.update_settings_display()

            self.set_status_message("Settings updated.")

            # Log the new settings for debugging.
            self.log(f"Save images: {self.save_images}")
            self.log(f"Save interval: {self.save_interval} sec")
            self.log(f"Gain: {self.gain}")
            self.log(f"Colorscale: {self.colorscale}")
            self.log(f"Exposure time: {self.exposure_time} ms")

    def update_position_display(self):
        """
        Updates the current position label in the alignment panel.

        In the future, current_x/current_y should probably come from the actual
        motor controller instead of being simulated.
        """

        self.current_position_label.setText(
            f"Current position:\n"
            f"X = {self.current_x:.2f}\n"
            f"Y = {self.current_y:.2f}"
        )

    def update_target_display(self):
        """
        Updates the target position label in the alignment panel.

        If the user has not clicked a target yet, this shows 'None selected'.
        """

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
        """
        Updates the settings summary in the right-side alignment panel.
        """

        self.exposure_label.setText(f"Exposure: {self.exposure_time} ms")
        self.gain_label.setText(f"Gain: {self.gain}")
        self.colorscale_label.setText(f"Colorscale: {self.colorscale}")

        # Save image label changes depending on whether saving is enabled.
        if self.save_images:
            self.save_images_label.setText(
                f"Save images: ON, every {self.save_interval} sec"
            )
        else:
            self.save_images_label.setText("Save images: OFF")

    def log(self, message):
        """
        Sends a message to both:
        - the terminal
        - the Logs tab

        The hasattr check matters because log() may be called before log_box
        exists during startup.
        """

        # Print to terminal for debugging from the command line.
        print(message)

        # Also show the message inside the GUI log box if it already exists.
        if hasattr(self, "log_box"):
            self.log_box.append(message)


# QApplication manages the entire PyQt application.
# Every PyQt GUI needs exactly one QApplication.
app = QApplication(sys.argv)

# Create the main window.
window = AlignerApp()

# Show the main window on screen.
window.show()

# Start the Qt event loop.
# The event loop keeps the GUI alive and listens for clicks, typing, resizing, etc.
sys.exit(app.exec())
