# ==========================================
# FUNCTIONAL AND HARDWARE-CONTROL CODE
# ==========================================
# This file contains the backend logic for camera acquisition,
# image processing, Newport Picomotor control, and the alignment loop.

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

# --- PyQt6 Threading Imports ---
# QThread keeps hardware work off the GUI thread; signals send data back to the UI.
from PyQt6.QtCore import pyqtSignal, QThread

# ==========================================
# NEWPORT DLL CONFIGURATION
# ==========================================
# Add the local DLL folder so the Newport wrapper can be imported.
strCurrFile = os.path.abspath(inspect.stack()[0][1])
strPathDllFolder = os.path.dirname(strCurrFile)
sys.path.append(strPathDllFolder)

# Load the Newport USB communication wrapper.
try:
    clr.AddReference("UsbDllWrap")
    from Newport.USBComm import *
except Exception as e:
    print(f"Warning: Could not load Newport DLL. {e}")

# ==========================================
# 1. IMAGE & HARDWARE FUNCTIONS 
# ==========================================

# Detect the iris circle used as the optical reference point.
def find_iris_grid(image):
    # Convert frame to grayscale for circle detection.
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # Reduce noise and apply Hough Circle Transform.
    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1, minDist=100, 
        param1=50, param2=30, minRadius=50, maxRadius=400
    )

    # Use the first detected circle as the iris candidate.
    if circles is not None:
        circles = np.uint16(np.around(circles))
        first_circle = circles[0][0]
        iris_x, iris_y, radius = int(first_circle[0]), int(first_circle[1]), int(first_circle[2])
        
        # Draw iris overlay for operator feedback.
        cv2.circle(image, (iris_x, iris_y), radius, (0, 255, 0), 2)
        cv2.circle(image, (iris_x, iris_y), 5, (0, 0, 255), -1)
        return iris_x, iris_y, radius
    return None, None, None

# Finds the laser centroid used to compute alignment error.
def find_laser_center(image, iris_x=None, iris_y=None, radius=None):
    # Convert frame to grayscale for intensity processing.
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
        
    # Limit the search to the iris aperture when available.
    if iris_x is not None and iris_y is not None and radius is not None:
        mask = np.zeros_like(gray)
        cv2.circle(mask, (iris_x, iris_y), int(radius - 15), 255, -1)
        gray = cv2.bitwise_and(gray, mask)
        
    # Reject frames where the beam is too dim to track reliably.
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(gray)
    
    if max_val < 200: return None, None

    # Threshold the beam and calculate its centroid.
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    M = cv2.moments(thresh)
    
    if M["m00"] != 0: 
        laser_x, laser_y = int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
    else:
        laser_x, laser_y = int(max_loc[0]), int(max_loc[1])
    
    # Mark the detected beam position on the display image.
    cv2.drawMarker(image, (laser_x, laser_y), (255, 0, 0), cv2.MARKER_CROSS, 40, 2)
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

# Converts camera error into motor steps for the selected actuator.
def adjust_hardware_alignment(oUSB, strDeviceKey, delta_x, delta_y, actuator_num):
    error_distance = max(abs(delta_x), abs(delta_y))

    # --- ASYMMETRIC DEADBANDS ---
    # Stops the infinite hunting loop by respecting the physical optical lever arm.
    if actuator_num == 1 and error_distance <= 10:
        return 0.0
    elif actuator_num == 2 and error_distance <= 12:
        return 0.0

    # Select gain and motion limits based on error magnitude.
    if error_distance < 15:
        GAIN = 0.5
        MIN_STEPS = 1
        MAX_STEPS = 3    
    elif error_distance < 30:
        GAIN = 0.75  
        MIN_STEPS = 2  
        MAX_STEPS = 20   
    else:
        GAIN = 1.0  
        MIN_STEPS = 15  
        MAX_STEPS = 600  

    # Map the actuator number to motor channels and calibration signs.
    if actuator_num == 1:
        CH_X, CH_Y = 1, 2
        STEPS_PER_PIXEL_X, STEPS_PER_PIXEL_Y = 20.0, -20.0
    else:
        CH_X, CH_Y = 3, 4
        STEPS_PER_PIXEL_X, STEPS_PER_PIXEL_Y = -2.0, -2.0
        MAX_STEPS = min(MAX_STEPS, 4)

    # Convert pixel error to motor steps.
    steps_x = int(delta_x * STEPS_PER_PIXEL_X * GAIN)
    steps_y = int(delta_y * STEPS_PER_PIXEL_Y * GAIN)

    # Clamp motor steps to reduce stalling and overshoot.
    if 0 < steps_x < MIN_STEPS: steps_x = MIN_STEPS
    elif 0 > steps_x > -MIN_STEPS: steps_x = -MIN_STEPS
    elif steps_x > MAX_STEPS: steps_x = MAX_STEPS
    elif steps_x < -MAX_STEPS: steps_x = -MAX_STEPS

    if 0 < steps_y < MIN_STEPS: steps_y = MIN_STEPS
    elif 0 > steps_y > -MIN_STEPS: steps_y = -MIN_STEPS
    elif steps_y > MAX_STEPS: steps_y = MAX_STEPS
    elif steps_y < -MAX_STEPS: steps_y = -MAX_STEPS

    # Send nonzero movement commands to the hardware.
    if steps_x != 0: move_motor_no_wait(oUSB, strDeviceKey, CH_X, steps_x)
    if steps_y != 0: move_motor_no_wait(oUSB, strDeviceKey, CH_Y, steps_y)

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
        
        self.is_aligning = False  
        self.was_aligning = False
        self.alignment_cooldown = 0.0 
        
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

    # Applies exposure and gain settings to both cameras.
    def update_camera_settings(self, exposure, gain):
        if hasattr(self, 'cameras') and self.cameras.IsOpen():
            for i, cam in enumerate(self.cameras):
                try: cam.ExposureTime.SetValue(float(exposure))
                except: 
                    try: cam.ExposureTimeAbs.SetValue(float(exposure))
                    except: pass
                try: cam.Gain.SetValue(float(gain))
                except: 
                    try: cam.GainRaw.SetValue(int(gain))
                    except: pass
            self.log_msg.emit(f"Hardware updated: Exposure={exposure}, Gain={gain}")

    # Starts a manual move toward a clicked camera target.
    def execute_manual_move(self, cam_idx, x, y):
        self.is_aligning = False 
        self.manual_target_active = True
        self.manual_cam_idx = cam_idx
        self.manual_x = x
        self.manual_y = y

    # Cancels active alignment modes and holds motor position.
    def stop_all_movement(self):
        self.is_aligning = False
        self.manual_target_active = False
        self.status_msg.emit("All movement stopped. Holding position.")

    # Main hardware loop for USB setup, camera acquisition, and alignment control.
    def run(self):
        # --- SPLIT TOLERANCES ---
        CAM1_TOLERANCE_PX = 10  
        CAM2_TOLERANCE_PX = 12  
        DRIFT_TOLERANCE_PX = 22 # Bumped slightly so normal noise doesn't instantly wake it up
        
        STABLE_FRAMES_REQUIRED = 5
        DRIFT_FRAMES_REQUIRED = 5

        # Initialize Newport USB controller.
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
                try: cam.ExposureAuto.SetValue("Off")
                except: pass
                try: cam.ExposureTime.SetValue(7000.0)
                except: 
                    try: cam.ExposureTimeAbs.SetValue(7000.0)
                    except: pass
                try: cam.GainAuto.SetValue("Off")
                except: pass
                try: cam.Gain.SetValue(0.0)
                except: 
                    try: cam.GainRaw.SetValue(0)
                    except: pass

                try: cam.AcquisitionFrameRateEnable.SetValue(True)
                except: pass
                try: cam.AcquisitionFrameRate.SetValue(30.0)
                except: 
                    try: cam.AcquisitionFrameRateAbs.SetValue(30.0)
                    except: pass

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
                if latest_frames[0] is not None and latest_frames[1] is not None:
                    img1, img2 = latest_frames[0], latest_frames[1]

                    # Detect iris and laser position for Camera 1.
                    i1_x, i1_y, r1 = find_iris_grid(img1)
                    l1_x, l1_y = find_laser_center(img1, i1_x, i1_y, r1)
                    err1_x, err1_y = 0, 0
                    if i1_x is not None and l1_x is not None:
                        self.laser_pos_update.emit(0, l1_x, l1_y)
                        err1_x, err1_y = l1_x - i1_x, l1_y - i1_y
                        cv2.line(img1, (i1_x, i1_y), (l1_x, l1_y), (0, 255, 255), 2)

                    # Detect iris and laser position for Camera 2.
                    i2_x, i2_y, r2 = find_iris_grid(img2)
                    l2_x, l2_y = find_laser_center(img2, i2_x, i2_y, r2)
                    err2_x, err2_y = 0, 0
                    if i2_x is not None and l2_x is not None:
                        self.laser_pos_update.emit(1, l2_x, l2_y)
                        err2_x, err2_y = l2_x - i2_x, l2_y - i2_y
                        cv2.line(img2, (i2_x, i2_y), (l2_x, l2_y), (0, 255, 255), 2)
                        
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
                                    
                                self.sentry_timer = time.time() + 3.0
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
                                        self.alignment_cooldown = time.time() + 0.5 
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
                                        self.alignment_cooldown = time.time() + 0.5 
                                else:
                                    self.cam2_stable_count = 0

                            if self.cam1_locked and self.cam2_locked:
                                self.sentry_timer = time.time() + 3.0

                        # Select the active alignment action and update overlays.
                        new_status = "" 
                        if l1_x is None:
                            cv2.putText(img1, "BEAM LOST", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                            new_status = "Beam lost! Waiting for manual intervention..."
                        elif not self.cam1_locked:
                            cv2.putText(img1, "ALIGNING M1", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                            new_status = "Aligning Camera 1..."
                            if time.time() > self.alignment_cooldown:
                                self.log_msg.emit(
                                    f"Aligning Camera 1 | Beam: X= {l1_x}, Y={l1_y} | "
                                    f"dX={err1_x}px, dY={err1_y}px | Error={err_pct1:.2f}%"
                                )

                                cooldown = adjust_hardware_alignment(self.oUSB, self.strDeviceKey, err1_x, err1_y, 1)
                                self.alignment_cooldown = time.time() + cooldown
                        
                        elif self.cam1_locked and not self.cam2_locked:
                            cv2.putText(img1, "M1 LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            cv2.putText(img2, "ALIGNING M2", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                            new_status = "Camera 1 Locked. Aligning Camera 2..."
                            if time.time() > self.alignment_cooldown:
                                
                                self.log_msg.emit(
                                    f"Aligning Camera 2 | Beam: X={l2_x}, Y={l2_y} | "
                                    f"dX={err2_x}px, dY={err2_y}px | Error={err_pct2:.2f}%"
                                )
                                
                                cooldown = adjust_hardware_alignment(self.oUSB, self.strDeviceKey, err2_x, err2_y, 2)
                                self.alignment_cooldown = time.time() + cooldown
                        elif self.cam1_locked and self.cam2_locked:
                            cv2.putText(img1, "SYSTEM LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            cv2.putText(img2, "SYSTEM LOCKED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                            time_left = max(0, int(self.sentry_timer - time.time()))
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
                                    new_status = "Manual target reached!"
                                    self.log_msg.emit("Manual mode complete. Brakes engaged.")
                                else:
                                    new_status = "Moving to manual target on Camera 1..."
                                    if time.time() > self.alignment_cooldown:
                                        self.log_msg.emit(f"Manual Cam 1 Pos: X={l1_x}, Y={l1_y} | Target: X={self.manual_x}, Y={self.manual_y} -> Adjusting...")
                                        cooldown = adjust_hardware_alignment(self.oUSB, self.strDeviceKey, err_x, err_y, 1)
                                        self.alignment_cooldown = time.time() + cooldown
                                        
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
                                    new_status = "Manual target reached!"
                                    self.log_msg.emit("Manual mode complete. Brakes engaged.")
                                else:
                                    new_status = "Moving to manual target on Camera 2..."
                                    if time.time() > self.alignment_cooldown:
                                        self.log_msg.emit(f"Manual Cam 2 Pos: X={l2_x}, Y={l2_y} | Target: X={self.manual_x}, Y={self.manual_y} -> Adjusting...")
                                        cooldown = adjust_hardware_alignment(self.oUSB, self.strDeviceKey, err_x, err_y, 2)
                                        self.alignment_cooldown = time.time() + cooldown

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
