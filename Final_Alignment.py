# ==========================================
# IMPORTS
# ==========================================
# Core libraries for file access, timing, math, and image processing.
import sys
import os
import inspect
import time
import numpy as np
import cv2


# --- Newport & Pylon Imports ---
# Interfaces for Newport motor control and Basler camera acquisition.
import clr
from pypylon import pylon 
from System.Text import StringBuilder

# --- PyQt6 Imports ---
# GUI widgets, signals, threads, and image display support.
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QVBoxLayout,
    QHBoxLayout, QPushButton, QTabWidget, QToolButton, QDialog,
    QFormLayout, QSpinBox, QDoubleSpinBox, QCheckBox,
    QDialogButtonBox, QFrame, QTextEdit
)
from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtGui import QImage, QPixmap

#Default Camera Settings
CAMERA_SETTINGS = {
    0: {
        "exposure": 50000.0,
        "gain": 0.0,
    },
    1: {
        "exposure": 50000.0,
        "gain": 0.0,
    },
}


# ==========================================
# NEWPORT DLL CONFIGURATION
# ==========================================

NEWPORT_AVAILABLE = False
USB = None
NEWPORT_DLL_HANDLE = None

try:
    NEWPORT_DLL_DIR = (
        r"C:\Program Files\New Focus"
        r"\New Focus Picomotor Application\Bin"
    )

    NEWPORT_DLL_PATH = os.path.join(
        NEWPORT_DLL_DIR,
        "UsbDllWrap.dll",
    )

    if not os.path.isfile(NEWPORT_DLL_PATH):
        raise FileNotFoundError(
            f"Newport DLL not found: {NEWPORT_DLL_PATH}"
        )

    # Keep this object alive for the duration of the program.
    NEWPORT_DLL_HANDLE = os.add_dll_directory(
        NEWPORT_DLL_DIR
    )

    clr.AddReference(NEWPORT_DLL_PATH)

    # Import the namespace contained inside UsbDllWrap.dll.
    from Newport.USBComm import USB

    NEWPORT_AVAILABLE = True
    print("Newport DLL loaded successfully.")

except Exception as e:
    print(f"Warning: Could not load Newport DLL. {e}")
# ==========================================
# 1. IMAGE & HARDWARE FUNCTIONS 
# ==========================================
IRIS_ROI = {
    # x1, y1, x2, y2 in raw camera coordinates
    0: None,
    1: None,
}
def remove_illumination_background(gray):
    background = cv2.GaussianBlur(
        gray,
        (0, 0),
        sigmaX=25,
        sigmaY=25,
    )

    corrected = cv2.absdiff(
        gray,
        background,
    )

    corrected = cv2.normalize(
        corrected,
        None,
        0,
        255,
        cv2.NORM_MINMAX,
    )

    return corrected

# Camera-specific starting estimates.
IRIS_INITIALIZATION = {
    0: (300, 240, 120),  # Camera 1: center_x, center_y, radius
    1: (300, 240, 150),  # Camera 2: center_x, center_y, radius
}

def create_iris_mask(
    image,
    camera_index=0,
    debug=True,
):
    if image.ndim == 3:
        gray = cv2.cvtColor(
            image,
            cv2.COLOR_BGR2GRAY,
        )
    else:
        gray = image.copy()

    height, width = gray.shape
    if not hasattr(create_iris_mask, "_printed_sizes"):
        create_iris_mask._printed_sizes = set()

    if camera_index not in create_iris_mask._printed_sizes:
        print(
            f"Camera {camera_index + 1} raw image size: "
            f"{width} x {height}"
        )
    create_iris_mask._printed_sizes.add(camera_index)
    roi_coordinates = IRIS_ROI.get(camera_index)

    if roi_coordinates is None:
        x1 = 0
        y1 = 0
        x2 = width
        y2 = height
    else:
        x1,y1,x2,y2 = roi_coordinates

        x1 = max(0, min(int(x1), width - 1))
        y1 = max(0, min(int(y1), height - 1))
        x2 = max(x1 + 1, min(int(x2), width))
        y2 = max(y1 + 1, min(int(y2), height))

    roi = gray[y1:y2, x1:x2]


    roi = cv2.medianBlur(
        roi,
        5,
    )

    corrected = remove_illumination_background(
        roi
    )

    glare_mask = cv2.threshold(
        roi,
        245,
        255,
        cv2.THRESH_BINARY,
    )[1]

    glare_mask = cv2.dilate(
        glare_mask,
        np.ones((9, 9), np.uint8),
        iterations=2,
    )

    corrected[
        glare_mask > 0
    ] = 0

    edges = cv2.Canny(
        corrected,
        30,
        90,
    )

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (7, 7),
    )

    mask = cv2.morphologyEx(
        edges,
        cv2.MORPH_CLOSE,
        kernel,
        iterations=2,
    )

    if debug:
        cv2.imshow(
            f"ROI {camera_index + 1}",
            roi,
        )
        cv2.imshow(
            f"Corrected {camera_index + 1}",
            corrected,
        )
        cv2.imshow(
            f"Glare mask {camera_index + 1}",
            glare_mask,
        )
        cv2.imshow(
            f"Iris mask {camera_index + 1}",
            mask,
        )
        cv2.waitKey(1)

    return mask, (x1, y1)

def find_iris_from_mask(
    image,
    mask,
    offset=(0, 0),
    previous_circle=None,
    camera_index=0,
):

    minimum_radius = 250
    maximum_radius = 600

    """
    Finds the outer iris boundary from a binary mask.

    Returns:
        iris_x, iris_y, radius
    """

    x_offset, y_offset = offset

    contours, _ = cv2.findContours(
        mask,
        cv2.RETR_EXTERNAL,
        cv2.CHAIN_APPROX_NONE,
    )

    if not contours:
        if previous_circle is not None:
            return previous_circle

        return None, None, None

    image_height, image_width = image.shape[:2]
    image_area = image_width * image_height

    # Starting radius limits for a 1920 x 1200 frame.
    # Adjust these after seeing the detected contour sizes.
    if camera_index == 0:
        minimum_radius = 100
        maximum_radius = 550
    else:
        minimum_radius = 100
        maximum_radius = 550

    candidates = []

    for contour in contours:
        if len(contour) < 5:
            continue

        contour_area = cv2.contourArea(contour)

        # Reject tiny contours.
        if contour_area < image_area * 0.001:
            continue

        perimeter = cv2.arcLength(
            contour,
            True,
        )

        if perimeter <= 0:
            continue

        circularity = (
            4.0
            * np.pi
            * contour_area
            / (perimeter * perimeter)
        )

        # Fit ellipse to allow slight perspective distortion.
        ellipse = cv2.fitEllipse(contour)

        (
            local_center_x,
            local_center_y,
        ), (
            ellipse_width,
            ellipse_height,
        ), ellipse_angle = ellipse

        if ellipse_width <= 0 or ellipse_height <= 0:
            continue

        major_axis = max(
            ellipse_width,
            ellipse_height,
        )

        minor_axis = min(
            ellipse_width,
            ellipse_height,
        )

        axis_ratio = minor_axis / major_axis

        # Convert the ellipse size to an equivalent radius.
        radius = (
            ellipse_width + ellipse_height
        ) / 4.0

        if radius < minimum_radius:
            continue

        if radius > maximum_radius:
            continue

        # Reject highly elongated contours.
        if axis_ratio < 0.65:
            continue

        full_center_x = local_center_x + x_offset
        full_center_y = local_center_y + y_offset

        # Score temporal consistency when a previous result exists.
        if previous_circle is not None:
            previous_x, previous_y, previous_radius = previous_circle

            center_change = np.hypot(
                full_center_x - previous_x,
                full_center_y - previous_y,
            )

            radius_change = abs(
                radius - previous_radius
            )

            # Reject sudden switches to a different ring.
            if center_change > 150:
                continue

            if radius_change > 120:
                continue

            stability_score = (
                center_change
                + 2.0 * radius_change
            )

        else:
            # Prefer contours near the image center initially.
            image_center_x = image_width / 2.0
            image_center_y = image_height / 2.0

            stability_score = np.hypot(
                full_center_x - image_center_x,
                full_center_y - image_center_y,
            )

        # Strongly prefer the large outer iris.
        score = (
            8.0 * radius
            + 800.0 * circularity
            + 500.0 * axis_ratio
            - 0.25 * stability_score
        )

        candidates.append(
            (
                score,
                full_center_x,
                full_center_y,
                radius,
                ellipse,
                contour_area,
                circularity,
                axis_ratio,
            )
        )

    if not candidates:
        if previous_circle is not None:
            return previous_circle

        return None, None, None

    # Highest score should correspond to the large outer iris.
    candidates.sort(
        key=lambda candidate: candidate[0],
        reverse=True,
    )

    (
        best_score,
        iris_x,
        iris_y,
        radius,
        best_ellipse,
        contour_area,
        circularity,
        axis_ratio,
    ) = candidates[0]

    # Smooth frame-to-frame movement.
    if previous_circle is not None:
        previous_x, previous_y, previous_radius = previous_circle

        alpha = 0.2

        iris_x = (
            (1.0 - alpha) * previous_x
            + alpha * iris_x
        )

        iris_y = (
            (1.0 - alpha) * previous_y
            + alpha * iris_y
        )

        radius = (
            (1.0 - alpha) * previous_radius
            + alpha * radius
        )

    iris_x = int(round(iris_x))
    iris_y = int(round(iris_y))
    radius = int(round(radius))

    return iris_x, iris_y, radius

def find_iris_grid(
    image,
    previous_circle=None,
    camera_index=0,
):
    mask, offset = create_iris_mask(
        image,
        camera_index=camera_index,
        debug=True,
    )

    return find_iris_from_mask(
        image=image,
        mask=mask,
        offset=offset,
        previous_circle=previous_circle,
        camera_index=camera_index,
    )

def find_laser_center(
    image,
    iris_x=None,
    iris_y=None,
    radius=None,
):
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # Restrict laser search to the detected aperture.
    if (
        iris_x is not None
        and iris_y is not None
        and radius is not None
    ):
        mask = np.zeros_like(gray)

        search_radius = max(
            int(radius - 15),
            1,
        )

        cv2.circle(
            mask,
            (iris_x, iris_y),
            search_radius,
            255,
            -1,
        )

        gray = cv2.bitwise_and(
            gray,
            gray,
            mask=mask,
        )

    _, max_val, _, max_loc = cv2.minMaxLoc(gray)

    if max_val < 200:
        return None, None

    _, thresh = cv2.threshold(
        gray,
        200,
        255,
        cv2.THRESH_BINARY,
    )

    moments = cv2.moments(thresh)

    if moments["m00"] != 0:
        laser_x = int(
            moments["m10"] / moments["m00"]
        )

        laser_y = int(
            moments["m01"] / moments["m00"]
        )
    else:
        laser_x = int(max_loc[0])
        laser_y = int(max_loc[1])

    cv2.drawMarker(
        image,
        (laser_x, laser_y),
        (255, 0, 0),
        cv2.MARKER_CROSS,
        40,
        2,
    )

    return laser_x, laser_y

# Sends a relative move command to one motor channel.
def move_motor_no_wait(oUSB, strDeviceKey, channel, steps):
    if steps == 0: return
    strBldr = StringBuilder(128)
    oUSB.Query(strDeviceKey, f"{channel}PR{steps}", strBldr)

# Stops the two motor channels assigned to one mirror mount.
def stop_motors(oUSB, strDeviceKey, ch1, ch2):
    strBldr = StringBuilder(128)
    try:
        oUSB.Query(strDeviceKey, f"{ch1}ST", strBldr)
        oUSB.Query(strDeviceKey, f"{ch2}ST", strBldr)
    except: pass

class PIDController:
    def __init__(
        self,
        kp,
        ki=0.0,
        kd=0.0,
        integral_limit=1000.0,
    ):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.integral_limit = abs(integral_limit)

        self.integral = 0.0
        self.previous_error = 0.0
        self.initialized = False

    def update_error(self, error, dt):
        if dt <= 0:
            return 0.0

        self.integral += error * dt
        self.integral = max(
            -self.integral_limit,
            min(self.integral, self.integral_limit),
        )

        if self.initialized:
            derivative = (
                error - self.previous_error
            ) / dt
        else:
            derivative = 0.0
            self.initialized = True

        output = (
            self.kp * error
            + self.ki * self.integral
            + self.kd * derivative
        )

        self.previous_error = error
        return output

    def reset(self):
        self.integral = 0.0
        self.previous_error = 0.0
        self.initialized = False

# Converts camera error into motor steps for the selected actuator.
# Converts X and Y camera errors into motor steps.

def adjust_hardware_alignment(
    oUSB,
    strDeviceKey,
    error_x,
    error_y,
    actuator_num,
    pid_x,
    pid_y,
    dt,
):
    if actuator_num == 1:
        channel_x, channel_y = 1, 2

        # Motor-direction calibration.
        sign_x, sign_y = 1, -1

        # Independent X/Y tolerances.
        tolerance_x = 10
        tolerance_y = 10

        # Independent output limits.
        max_steps_x = 600
        max_steps_y = 600

    elif actuator_num == 2:
        channel_x, channel_y = 3, 4

        sign_x, sign_y = -1, -1

        tolerance_x = 12
        tolerance_y = 12

        max_steps_x = 4
        max_steps_y = 4

    else:
        raise ValueError("actuator_num must be 1 or 2")

    if dt <= 0:
        return 0.0

    # ------------------------------------------
    # X-axis PID control
    # ------------------------------------------
    if abs(error_x) <= tolerance_x:
        pid_x.reset()
        steps_x = 0
    else:
        output_x = pid_x.update_error(error_x, dt)
        steps_x = int(round(output_x * sign_x))

    # ------------------------------------------
    # Y-axis PID control
    # ------------------------------------------
    if abs(error_y) <= tolerance_y:
        pid_y.reset()
        steps_y = 0
    else:
        output_y = pid_y.update_error(error_y, dt)
        steps_y = int(round(output_y * sign_y))

    # Clamp each axis independently.
    steps_x = max(
        -max_steps_x,
        min(steps_x, max_steps_x),
    )

    steps_y = max(
        -max_steps_y,
        min(steps_y, max_steps_y),
    )

    # Send only nonzero commands.
    if steps_x != 0:
        move_motor_no_wait(
            oUSB,
            strDeviceKey,
            channel_x,
            steps_x,
        )

    if steps_y != 0:
        move_motor_no_wait(
            oUSB,
            strDeviceKey,
            channel_y,
            steps_y,
        )

    # Use the larger movement to estimate settling time.
    max_steps_taken = max(abs(steps_x), abs(steps_y))

    return (max_steps_taken / 2000.0) + 0.05

# ==========================================
# 2. THE HARDWARE THREAD (The Bridge)
# ==========================================
# Runs camera acquisition, image processing, and motor control off the GUI thread.
class HardwareThread(QThread):
    # Signals send frames, logs, status, and laser coordinates to the GUI.
    frame_ready = pyqtSignal(int, np.ndarray)  
    log_msg = pyqtSignal(str)                  
    status_msg = pyqtSignal(str)               
    laser_pos_update = pyqtSignal(int, int, int) 

    # Initializes alignment state, manual target state, and image saving settings.
    def __init__(self):
        super().__init__()
        self.is_running = True
        self.camera_settings = {
            0: CAMERA_SETTINGS[0].copy(),
            1: CAMERA_SETTINGS[1].copy(),
        }

        self.is_aligning = False  
        self.was_aligning = False
        self.alignment_cooldown = 0.0 
        
        self.pid_m1_x = PIDController(kp=1.0, ki=0.0, kd=0.0)
        self.pid_m1_y = PIDController(kp=1.0, ki=0.0, kd=0.0)

        self.pid_m2_x = PIDController(kp=0.5, ki=0.0, kd=0.0)
        self.pid_m2_y = PIDController(kp=0.5, ki=0.0, kd=0.0)

        self.last_pid_time_m1 = None
        self.last_pid_time_m2 = None


        self.manual_target_active = False
        self.was_manual_aligning = False
        self.manual_cam_idx = 0
        self.manual_x = 0
        self.manual_y = 0
        
        self.cam1_locked = False
        self.cam2_locked = False
        self.sentry_timer = 0.0  
        
        self.cam1_stable_count = 0
        self.cam2_stable_count = 0
        
        self.cam1_drift_count = 0
        self.cam2_drift_count = 0
        self.system_locked_stop_sent = False
        
        self.save_images = False
        self.save_interval = 3
        self.last_save_time = time.time()

    def get_pid_dt(self, actuator_num):
        now = time.monotonic()
        if actuator_num == 1:
            previous_time = self.last_pid_time_m1
            self.last_pid_time_m1 = now
        elif actuator_num == 2:
            previous_time = self.last_pid_time_m2
            self.last_pid_time_m2 = now
        else:
            raise ValueError("actuator_num must be 1 or 2")

        if previous_time is None:
            dt = 0.05
        else:
            dt = now - previous_time

    # Prevent unstable derivative values from extreme timing.
        dt = max(0.001, min(dt, 1.0))
        return now, dt

    def reset_pid_controllers(self):
        self.pid_m1_x.reset()
        self.pid_m1_y.reset()
        self.pid_m2_x.reset()
        self.pid_m2_y.reset()

        self.last_pid_time_m1 = None
        self.last_pid_time_m2 = None


    # Applies exposure and gain settings to both cameras.
        # Applies separate exposure and gain settings to each camera.
    def update_camera_settings(self, camera_settings):
        if not hasattr(self, "cameras") or self.cameras is None:
            self.log_msg.emit("Camera array is unavailable.")
            return

        if not self.cameras.IsOpen():
            self.log_msg.emit("Cameras are not open.")
            return

        messages = []

        for i, cam in enumerate(self.cameras):
            settings = camera_settings.get(i)

            if settings is None:
                messages.append(
                    f"Camera {i + 1}: no settings provided."
                )
                continue

            exposure = float(settings["exposure"])
            gain = float(settings["gain"])

            try:
                try:
                    cam.ExposureMode.SetValue("Timed")
                except Exception:
                    pass

                try:
                    cam.ExposureAuto.SetValue("Off")
                except Exception:
                    pass

                if hasattr(cam, "ExposureTime") and cam.ExposureTime.IsWritable():
                    exposure_node = cam.ExposureTime
                elif hasattr(cam, "ExposureTimeAbs") and cam.ExposureTimeAbs.IsWritable():
                    exposure_node = cam.ExposureTimeAbs
                else:
                    raise RuntimeError(
                        "No writable exposure parameter found."
                    )

                safe_exposure = max(
                    exposure_node.GetMin(),
                    min(
                        exposure,
                        exposure_node.GetMax(),
                    ),
                )

                exposure_node.SetValue(safe_exposure)
                actual_exposure = exposure_node.GetValue()

                try:
                    cam.GainAuto.SetValue("Off")
                except Exception:
                    pass

                if hasattr(cam, "Gain") and cam.Gain.IsWritable():
                    gain_node = cam.Gain
                    requested_gain = float(gain)
                elif hasattr(cam, "GainRaw") and cam.GainRaw.IsWritable():
                    gain_node = cam.GainRaw
                    requested_gain = int(gain)
                else:
                    raise RuntimeError(
                        "No writable gain parameter found."
                    )

                safe_gain = max(
                    gain_node.GetMin(),
                    min(
                        requested_gain,
                        gain_node.GetMax(),
                    ),
                )

                gain_node.SetValue(safe_gain)
                actual_gain = gain_node.GetValue()

                messages.append(
                    f"Camera {i + 1}: "
                    f"exposure={actual_exposure:.0f} us, "
                    f"gain={actual_gain}"
                )

            except Exception as exc:
                messages.append(
                    f"Camera {i + 1} update failed: {exc}"
                )

        self.log_msg.emit(" | ".join(messages))

    # Starts a manual move toward a clicked camera target.
    def execute_manual_move(self, cam_idx, x, y):
        self.is_aligning = False 
        self.manual_target_active = True
        self.manual_cam_idx = cam_idx
        self.manual_x = x
        self.manual_y = y

        self.reset_pid_controllers()
        self.alignment_cooldown = 0.0

    # Cancels active alignment modes and holds motor position.
    def stop_all_movement(self):
        self.is_aligning = False
        self.manual_target_active = False

        self.reset_pid_controllers()

        self.status_msg.emit(
        "All movement stopped. Holding position."
    )

    # Main hardware loop for USB setup, camera acquisition, and alignment control.
    def run(self):
        self.previous_iris = {
            0: None,
            1: None,
        }
        # --- SPLIT TOLERANCES ---
        CAM1_TOLERANCE_PX = 10  
        CAM2_TOLERANCE_PX = 12  
        DRIFT_TOLERANCE_PX = 22 # Bumped slightly so normal noise doesn't instantly wake it up
        
        STABLE_FRAMES_REQUIRED = 5
        DRIFT_FRAMES_REQUIRED = 5

        # Initialize Newport USB controller.
        if not NEWPORT_AVAILABLE or USB is None:
            self.log_msg.emit("ERROR: Newport USB library is unavailable.")
            return

        try:
            self.oUSB = USB(True)
            if not self.oUSB.OpenDevices(0, True):
                self.log_msg.emit("ERROR: Could not open Newport USB devices.")
                return
            oDeviceTable = self.oUSB.GetDeviceTable()
            oEnumerator = oDeviceTable.GetEnumerator()
            oEnumerator.MoveNext()
            self.strDeviceKey = str(oEnumerator.Key)
            self.log_msg.emit(f"Connected Newport Controller: {self.strDeviceKey}")
        except Exception as e:
            self.log_msg.emit(f"Hardware init skipped or failed: {e}")
            return

        # Initialize and configure the two Basler cameras.
        try:
            tlFactory = pylon.TlFactory.GetInstance()
            devices = tlFactory.EnumerateDevices()
            CAM1_SN = "25191527" 
            CAM2_SN = "25191524" 
            
            # Match detected devices to the expected serial numbers.
            self.cameras = pylon.InstantCameraArray(2)
            cam1_found, cam2_found = False, False
            for dev in devices:
                sn = dev.GetSerialNumber()
                if sn == CAM1_SN:
                    self.cameras[0].Attach(tlFactory.CreateDevice(dev))
                    self.cameras[0].SetCameraContext(0)
                    cam1_found = True
                elif sn == CAM2_SN:
                    self.cameras[1].Attach(tlFactory.CreateDevice(dev))
                    self.cameras[1].SetCameraContext(1)
                    cam2_found = True
                    
            if not (cam1_found and cam2_found):
                self.log_msg.emit("ERROR: Could not find both cameras!")
                return

            # Disable auto settings and apply fixed acquisition parameters.
            self.cameras.Open()

            for i, cam in enumerate(self.cameras):
                exposure = self.camera_settings[i]["exposure"]
                gain = self.camera_settings[i]["gain"]

                try:
                    cam.ExposureAuto.SetValue("Off")
                except Exception:
                    pass

                try:
                    cam.ExposureTime.SetValue(exposure)
                except Exception:
                    try:
                        cam.ExposureTimeAbs.SetValue(exposure)
                    except Exception:
                        pass

                try:
                    cam.GainAuto.SetValue("Off")
                except Exception:
                    pass

                try:
                    cam.Gain.SetValue(gain)
                except Exception:
                    try:
                        cam.GainRaw.SetValue(int(gain))
                    except Exception:
                        pass

            # Start acquisition using the most recent frame from each camera.
            self.cameras.StartGrabbing(pylon.GrabStrategy_LatestImageOnly, pylon.GrabLoop_ProvidedByUser)
            self.log_msg.emit("Cameras started at 30 FPS. Waiting for frames...")
            latest_frames = {0: None, 1: None}

            # Continuous acquisition and alignment loop.
            while self.is_running and self.cameras.IsGrabbing():
                grab_result = self.cameras.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)

                # Store the latest frame from whichever camera returned data.
                if grab_result.GrabSucceeded():
                    cam_idx = grab_result.GetCameraContext()
                    raw_image = grab_result.Array
                    image = cv2.cvtColor(raw_image, cv2.COLOR_GRAY2BGR) if len(raw_image.shape) == 2 else raw_image.copy()
                    latest_frames[cam_idx] = image
                grab_result.Release()

                # Process only when a synchronized pair of frames is available.
                # Process only when a synchronized pair of frames is available.
                if latest_frames[0] is not None and latest_frames[1] is not None:
                    img1 = latest_frames[0]
                    img2 = latest_frames[1]

                    # ------------------------------------------
                    # Camera 1 iris and laser detection
                    # ------------------------------------------
                    i1_x, i1_y, r1 = find_iris_grid(
                        img1,
                        previous_circle=self.previous_iris[0],
                        camera_index=0,
                    )

                    if i1_x is not None:
                        self.previous_iris[0] = (
                            i1_x,
                            i1_y,
                            r1,
                        )

                        cv2.circle(
                            img1,
                            (i1_x, i1_y),
                            r1,
                            (0, 255, 0),
                            2,
                        )

                        cv2.circle(
                            img1,
                            (i1_x, i1_y),
                            5,
                            (0, 0, 255),
                            -1,
                        )

                    l1_x, l1_y = find_laser_center(
                        img1,
                        i1_x,
                        i1_y,
                        r1,
                    )

                    err1_x = 0
                    err1_y = 0

                    if i1_x is not None and l1_x is not None:
                        self.laser_pos_update.emit(
                            0,
                            l1_x,
                            l1_y,
                        )

                        err1_x = l1_x - i1_x
                        err1_y = l1_y - i1_y

                        cv2.line(
                            img1,
                            (i1_x, i1_y),
                            (l1_x, l1_y),
                            (0, 255, 255),
                            2,
                        )

                    # ------------------------------------------
                    # Camera 2 iris and laser detection
                    # ------------------------------------------
                    i2_x, i2_y, r2 = find_iris_grid(
                        img2,
                        previous_circle=self.previous_iris[1],
                        camera_index=1,
                    )

                    if i2_x is not None:
                        self.previous_iris[1] = (
                            i2_x,
                            i2_y,
                            r2,
                        )

                        cv2.circle(
                            img2,
                            (i2_x, i2_y),
                            r2,
                            (0, 255, 0),
                            2,
                        )

                        cv2.circle(
                            img2,
                            (i2_x, i2_y),
                            5,
                            (0, 0, 255),
                            -1,
                        )

                    l2_x, l2_y = find_laser_center(
                        img2,
                        i2_x,
                        i2_y,
                        r2,
                    )

                    err2_x = 0
                    err2_y = 0

                    if i2_x is not None and l2_x is not None:
                        self.laser_pos_update.emit(
                            1,
                            l2_x,
                            l2_y,
                        )

                        err2_x = l2_x - i2_x
                        err2_y = l2_y - i2_y

                        cv2.line(
                            img2,
                            (i2_x, i2_y),
                            (l2_x, l2_y),
                            (0, 255, 255),
                            2,
                        )
                        
                    # Compute pixel error for convergence checks.
                    err_dist1 = max(abs(err1_x), abs(err1_y)) if l1_x is not None else 999
                    err_dist2 = max(abs(err2_x), abs(err2_y)) if l2_x is not None else 999

                    err_pct1 = 0.0
                    err_pct2 = 0.0

                    # Express alignment error as a percentage of iris radius.
                    if r1 is not None and r1 > 0:
                        err_mag1 = (err1_x ** 2 + err1_y ** 2) ** 0.5
                        err_pct1 = (err_mag1 / r1) * 100

                    if r2 is not None and r2 > 0:
                        err_mag2 = (err2_x ** 2 + err2_y ** 2) ** 0.5
                        err_pct2 = (err_mag2 / r2) * 100

                    # Automatic alignment state machine.
                    if self.is_aligning:
                        # Once locked, stop motors and monitor for drift.
                        if self.cam1_locked and self.cam2_locked:
                            if not self.system_locked_stop_sent:
                                stop_motors(self.oUSB, self.strDeviceKey, 1, 2)
                                stop_motors(self.oUSB, self.strDeviceKey, 3, 4)
                                self.system_locked_stop_sent = True
                                self.log_msg.emit("System aligned. Motors stopped; monitoring for drift.")

                            if err_dist1 > DRIFT_TOLERANCE_PX:
                                self.cam1_drift_count += 1
                            else:
                                self.cam1_drift_count = 0

                            if self.cam1_drift_count >= DRIFT_FRAMES_REQUIRED:
                                self.cam1_locked = False
                                self.cam1_stable_count = 0
                                self.cam1_drift_count = 0
                                self.system_locked_stop_sent = False
                                self.log_msg.emit("Camera 1 drift detected. Re-aligning actuator 1.")
                                
                            if err_dist2 > DRIFT_TOLERANCE_PX:
                                self.cam2_drift_count += 1
                            else:
                                self.cam2_drift_count = 0

                            if self.cam2_drift_count >= DRIFT_FRAMES_REQUIRED:
                                self.cam2_locked = False
                                self.cam2_stable_count = 0
                                self.cam2_drift_count = 0
                                self.system_locked_stop_sent = False
                                self.log_msg.emit("Camera 2 drift detected. Re-aligning actuator 2.")
 
                                drift_detected = False
                                
                                if err_dist1 > DRIFT_TOLERANCE_PX:
                                    self.cam1_locked = False
                                    self.cam1_stable_count = 0
                                    drift_detected = True
                                if err_dist2 > DRIFT_TOLERANCE_PX:
                                    self.cam2_locked = False
                                    self.cam2_stable_count = 0
                                    drift_detected = True
                                    
                                if drift_detected:
                                    self.log_msg.emit("Drift detected! Waking up motors...")
                                    
                                self.sentry_timer = time.monotonic() + 3.0
                        else:
                            # Update lock state for each camera before moving motors.
                            if self.cam1_locked:
                                if err_dist1 > DRIFT_TOLERANCE_PX:
                                    self.cam1_locked = False
                                    self.cam1_stable_count = 0
                            else:
                                if err_dist1 <= CAM1_TOLERANCE_PX:
                                    self.cam1_stable_count += 1
                                    if self.cam1_stable_count >= STABLE_FRAMES_REQUIRED:
                                        self.cam1_locked = True
                                        stop_motors(self.oUSB, self.strDeviceKey, 1, 2)
                                        self.alignment_cooldown = time.monotonic() + 0.2
                                else:
                                    self.cam1_stable_count = 0
                                    
                            if self.cam2_locked:
                                if err_dist2 > DRIFT_TOLERANCE_PX:
                                    self.cam2_locked = False
                                    self.cam2_stable_count = 0
                            else:
                                if err_dist2 <= CAM2_TOLERANCE_PX:
                                    self.cam2_stable_count += 1
                                    if self.cam2_stable_count >= STABLE_FRAMES_REQUIRED:
                                        self.cam2_locked = True
                                        stop_motors(self.oUSB, self.strDeviceKey, 3, 4)
                                        self.alignment_cooldown = time.monotonic() + 0.2 
                                else:
                                    self.cam2_stable_count = 0

                            if self.cam1_locked and self.cam2_locked:
                                self.sentry_timer = time.monotonic() + 3.0

                        # Select the active alignment action and update overlays.
                        new_status = "" 
                        if l1_x is None:
                            cv2.putText(img1, "BEAM LOST", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                            new_status = "Beam lost! Waiting for manual intervention..."
                        elif not self.cam1_locked:
                            cv2.putText(img1, "ALIGNING M1", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                            new_status = "Aligning Camera 1..."

                            if time.monotonic() > self.alignment_cooldown:
                                now, dt = self.get_pid_dt(1)

                                self.log_msg.emit(
                                    f"Aligning Camera 1 | Beam: X={l1_x}, Y={l1_y} | "
                                    f"dX={err1_x}px, dY={err1_y}px | "
                                    f"Error={err_pct1:.2f}% | dt={dt:.4f}s"
                                )

                                cooldown = adjust_hardware_alignment(
                                    self.oUSB,
                                    self.strDeviceKey,
                                    error_x=err1_x,
                                    error_y=err1_y,
                                    actuator_num=1,
                                    pid_x=self.pid_m1_x,
                                    pid_y=self.pid_m1_y,
                                    dt=dt,
                                )
                                self.alignment_cooldown = now + cooldown
                        
                        elif self.cam1_locked and not self.cam2_locked:
                            cv2.putText(img1, "M1 LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            cv2.putText(img2, "ALIGNING M2", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                            new_status = "Camera 1 Locked. Aligning Camera 2..."
                            
                            if l2_x is None or l2_y is None:
                                 new_status = "Camera 2 beam lost."

                            elif time.monotonic() > self.alignment_cooldown:
                                now, dt = self.get_pid_dt(2)
                                
                                self.log_msg.emit(
                                    f"Aligning Camera 2 | Beam: X={l2_x}, Y={l2_y} | "
                                    f"dX={err2_x}px, dY={err2_y}px | "
                                    f"Error={err_pct2:.2f}% | dt={dt:.4f}s"
                                )
                                
                                cooldown = adjust_hardware_alignment(
                                    self.oUSB,
                                     self.strDeviceKey,
                                     error_x=err2_x,
                                     error_y=err2_y,
                                     actuator_num=2,
                                     pid_x=self.pid_m2_x,
                                     pid_y=self.pid_m2_y,
                                     dt=dt,
                                )    
                                 
                                self.alignment_cooldown = now + cooldown
                                
                        elif self.cam1_locked and self.cam2_locked:
                            cv2.putText(img1, "SYSTEM LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            cv2.putText(img2, "SYSTEM LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            time_left = max(0, int(self.sentry_timer - time.monotonic()))
                            new_status = f"System Aligned. Double-checking drift in {time_left}s..."

                        if not hasattr(self, 'current_status') or new_status != self.current_status:
                            self.status_msg.emit(new_status)
                            self.current_status = new_status
                            
                    # Manual target mode uses a clicked point instead of the iris center.
                    elif self.manual_target_active:
                        new_status = ""
                        
                        # Move actuator 1 toward a Camera 1 target.
                        if self.manual_cam_idx == 0:
                            if l1_x is None:
                                cv2.putText(img1, "BEAM LOST", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                                new_status = "Beam lost! Cannot complete manual move."
                            else:
                                err_x = l1_x - self.manual_x
                                err_y = l1_y - self.manual_y
                                dist = max(abs(err_x), abs(err_y))
                                
                                cv2.drawMarker(img1, (self.manual_x, self.manual_y), (0, 255, 0), cv2.MARKER_STAR, 30, 2)
                                cv2.line(img1, (self.manual_x, self.manual_y), (l1_x, l1_y), (0, 255, 255), 2)
                                
                                if dist <= CAM1_TOLERANCE_PX:
                                    self.manual_target_active = False
                                    stop_motors(self.oUSB, self.strDeviceKey, 1, 2)

                                    self.pid_m1_x.reset()
                                    self.pid_m1_y.reset()
                                    self.last_pid_time_m1 = None

                                    new_status = "Manual target reached!"
                                    self.log_msg.emit("Manual mode complete. Brakes engaged.")
                                else:
                                    new_status = "Moving to manual target on Camera 1..."
                                    if time.monotonic() > self.alignment_cooldown:
                                        now, dt = self.get_pid_dt(1)
                                        self.log_msg.emit(f"Manual Cam 1 Pos: X={l1_x}, Y={l1_y} | Target: X={self.manual_x}, Y={self.manual_y} -> Adjusting...")
                                        cooldown = adjust_hardware_alignment(
                                            self.oUSB,
                                            self.strDeviceKey,
                                            error_x=err_x,
                                            error_y=err_y,
                                            actuator_num=1,
                                            pid_x=self.pid_m1_x,
                                            pid_y=self.pid_m1_y,
                                            dt=dt,
                                        )

                                        self.alignment_cooldown = now + cooldown
                                        
                        # Move actuator 2 toward a Camera 2 target.
                        elif self.manual_cam_idx == 1:
                            if l2_x is None:
                                cv2.putText(img2, "BEAM LOST", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                                new_status = "Beam lost! Cannot complete manual move."
                            else:
                                err_x = l2_x - self.manual_x
                                err_y = l2_y - self.manual_y
                                dist = max(abs(err_x), abs(err_y))
                                
                                cv2.drawMarker(img2, (self.manual_x, self.manual_y), (0, 255, 0), cv2.MARKER_STAR, 30, 2)
                                cv2.line(img2, (self.manual_x, self.manual_y), (l2_x, l2_y), (0, 255, 255), 2)
                                
                                if dist <= CAM2_TOLERANCE_PX:
                                    self.manual_target_active = False
                                    stop_motors(self.oUSB, self.strDeviceKey, 3, 4)

                                    self.pid_m2_x.reset()
                                    self.pid_m2_y.reset()
                                    self.last_pid_time_m2 = None

                                    new_status = "Manual target reached!"
                                    self.log_msg.emit("Manual mode complete. Brakes engaged.")
                                else:
                                    new_status = "Moving to manual target on Camera 2..."
                                    if time.monotonic() > self.alignment_cooldown:
                                        now, dt = self.get_pid_dt(2)

                                        self.log_msg.emit(f"Manual Cam 2 Pos: X={l2_x}, Y={l2_y} | Target: X={self.manual_x}, Y={self.manual_y} -> Adjusting...")
                                        cooldown = adjust_hardware_alignment(
                                            self.oUSB,
                                            self.strDeviceKey,
                                            error_x=err_x,
                                            error_y=err_y,
                                            actuator_num=2,
                                            pid_x=self.pid_m2_x,
                                            pid_y=self.pid_m2_y,
                                            dt=dt,
                                        )

                                        self.alignment_cooldown = now + cooldown

                        if not hasattr(self, 'current_status') or new_status != self.current_status:
                            self.status_msg.emit(new_status)
                            self.current_status = new_status

                    else:
                        # Stop motors when leaving an active movement mode.
                        if self.was_aligning or self.was_manual_aligning:
                            stop_motors(self.oUSB, self.strDeviceKey, 1, 2)
                            stop_motors(self.oUSB, self.strDeviceKey, 3, 4)
                    
                    self.was_aligning = self.is_aligning
                    self.was_manual_aligning = self.manual_target_active

                    # Save annotated camera frames at the selected interval.
                    if self.save_images and (time.time() - self.last_save_time >= self.save_interval):
                        cv2.imwrite(f"cam1_{int(time.time())}.png", img1)
                        cv2.imwrite(f"cam2_{int(time.time())}.png", img2)
                        self.log_msg.emit("Images saved to disk.")
                        self.last_save_time = time.time()

                    # Push processed frames to the GUI.
                    self.frame_ready.emit(0, img1)
                    self.frame_ready.emit(1, img2)
                    latest_frames = {0: None, 1: None} 

        except Exception as e:
            self.log_msg.emit(f"Camera Loop Error: {e}")

    # Stops acquisition and closes hardware connections.
    def stop(self):
        self.is_running = False
        self.wait() 
        try:
            self.cameras.StopGrabbing()
            self.cameras.Close()
            self.oUSB.CloseDevices()
        except: pass

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

        self.setWindowTitle("Camera Settings")
        self.setMinimumWidth(400)

        layout = QFormLayout()

        self.save_images_checkbox = QCheckBox(
            "Save images during alignment"
    )

        self.save_interval_input = QSpinBox()
        self.save_interval_input.setRange(1, 60)
        self.save_interval_input.setSuffix(" sec")

        self.cam1_gain_input = QDoubleSpinBox()
        self.cam1_gain_input.setRange(0.0, 100.0)
        self.cam1_gain_input.setDecimals(1)
        self.cam1_gain_input.setSingleStep(0.1)

        self.cam1_exposure_input = QDoubleSpinBox()
        self.cam1_exposure_input.setRange(100.0, 10000000.0)
        self.cam1_exposure_input.setDecimals(0)
        self.cam1_exposure_input.setSingleStep(1000.0)
        self.cam1_exposure_input.setSuffix(" us")

        self.cam2_gain_input = QDoubleSpinBox()
        self.cam2_gain_input.setRange(0.0, 100.0)
        self.cam2_gain_input.setDecimals(1)
        self.cam2_gain_input.setSingleStep(0.1)

        self.cam2_exposure_input = QDoubleSpinBox()
        self.cam2_exposure_input.setRange(100.0, 10000000.0)
        self.cam2_exposure_input.setDecimals(0)
        self.cam2_exposure_input.setSingleStep(1000.0)
        self.cam2_exposure_input.setSuffix(" us")

        layout.addRow(
            "Save images:",
        self.save_images_checkbox
    )

        layout.addRow(
            "Save interval:",
            self.save_interval_input
        )

        layout.addRow(
            QLabel("<b>Camera 1</b>")
    )

        layout.addRow(
            "Exposure:",
            self.cam1_exposure_input
        )

        layout.addRow(
            "Gain:",
            self.cam1_gain_input
        )

        layout.addRow(
            QLabel("<b>Camera 2</b>")
        )

        layout.addRow(
            "Exposure:",
            self.cam2_exposure_input
        )

        layout.addRow(
            "Gain:",
            self.cam2_gain_input
        )

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
        |   QDialogButtonBox.StandardButton.Cancel
        )

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
        self.camera_settings = {
            0:{
                "gain": 0.0,
                "exposure": 50000,
            },
            1:{
                "gain": 0.0,
                "exposure": 50000,
            },
        }

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

        self.camera_settings = {
            0: self.camera_settings[0].copy(),
            1: self.camera_settings[1].copy(),
        }

        alignment_panel = self.create_alignment_panel()
        main_layout.addWidget(alignment_panel)

        self.hw_thread = HardwareThread()
        self.hw_thread.camera_settings = {
            0: self.camera_settings[0].copy(),
            1: self.camera_settings[1].copy(),
        }

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

        layout.addWidget(
            QLabel(
                "<b>Alignment Controls</b>",
                alignment=Qt.AlignmentFlag.AlignCenter
            )
        )

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

        settings_button = QPushButton("Camera Settings")
        settings_button.clicked.connect(self.open_settings)
        layout.addWidget(settings_button)

        self.save_images_label = QLabel()
        layout.addWidget(self.save_images_label)

        self.update_settings_display()

        layout.addStretch()
        return panel

    # Opens the settings dialog and applies accepted changes.
    def open_settings(self):
        dialog = SettingsDialog()

        dialog.save_images_checkbox.setChecked(
            self.save_images
        )

        dialog.save_interval_input.setValue(
            self.save_interval
        )

        dialog.cam1_gain_input.setValue(
            self.camera_settings[0]["gain"]
        )

        dialog.cam1_exposure_input.setValue(
            self.camera_settings[0]["exposure"]
        )

        dialog.cam2_gain_input.setValue(
            self.camera_settings[1]["gain"]
        )

        dialog.cam2_exposure_input.setValue(
            self.camera_settings[1]["exposure"]
        )

        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.save_images = (
                dialog.save_images_checkbox.isChecked()
            )

            self.save_interval = (
                dialog.save_interval_input.value()
            )

            self.camera_settings[0]["gain"] = (
                dialog.cam1_gain_input.value()
            )

            self.camera_settings[0]["exposure"] = (
                dialog.cam1_exposure_input.value()
            )

            self.camera_settings[1]["gain"] = (
                dialog.cam2_gain_input.value()
            )

            self.camera_settings[1]["exposure"] = (
                dialog.cam2_exposure_input.value()
            )

            self.hw_thread.save_images = self.save_images
            self.hw_thread.save_interval = self.save_interval

            self.hw_thread.camera_settings = {
                0: self.camera_settings[0].copy(),
                1: self.camera_settings[1].copy(),
            }

            self.hw_thread.update_camera_settings(
                self.hw_thread.camera_settings
            )

            self.update_settings_display()

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

        # Refreshes the camera and image-saving settings label.
    def update_settings_display(self):
        if self.save_images:
            save_text = (
                f"Save images: ON "
                f"({self.save_interval}s)"
            )
        else:
            save_text = "Save images: OFF"

        cam1_exposure_ms = (
            self.camera_settings[0]["exposure"]
            / 1000.0
        )

        cam2_exposure_ms = (
            self.camera_settings[1]["exposure"]
            / 1000.0
        )

        cam1_gain = self.camera_settings[0]["gain"]
        cam2_gain = self.camera_settings[1]["gain"]

        self.save_images_label.setText(
            f"{save_text}\n"
            f"Camera 1: {cam1_exposure_ms:.1f} ms, gain {cam1_gain:.1f}\n"
            f"Camera 2: {cam2_exposure_ms:.1f} ms, gain {cam2_gain:.1f}"
        )

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

        #resetting all pid controllers
        self.hw_thread.reset_pid_controllers()

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