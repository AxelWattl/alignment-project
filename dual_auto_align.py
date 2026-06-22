import sys
import os
import inspect
import time
import clr
import numpy as np
from pathlib import Path
from System.Text import StringBuilder

# Basler Pylon & OpenCV Imports
from pypylon import pylon 
import cv2

print("Python %s\n" % sys.version)

# --- NEWPORT DLL PATH CONFIGURATION ---
strCurrFile = os.path.abspath(inspect.stack()[0][1])
strPathDllFolder = os.path.dirname(strCurrFile)
sys.path.append(strPathDllFolder)

# Load Newport USB DLL wrapper
clr.AddReference("UsbDllWrap")
from Newport.USBComm import *

# ==========================================
# 1. IMAGE PROCESSING FUNCTIONS
# ==========================================

def find_iris_grid(image):
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    gray = cv2.medianBlur(gray, 5)
    circles = cv2.HoughCircles(
        gray, cv2.HOUGH_GRADIENT, dp=1, minDist=100, 
        param1=50, param2=30, minRadius=50, maxRadius=400
    )

    if circles is not None:
        circles = np.uint16(np.around(circles))
        first_circle = circles[0][0]
        iris_x = int(first_circle[0])
        iris_y = int(first_circle[1])
        radius = int(first_circle[2])
        
        cv2.circle(image, (iris_x, iris_y), radius, (0, 255, 0), 2)
        cv2.circle(image, (iris_x, iris_y), 5, (0, 0, 255), -1)
        return iris_x, iris_y, radius
    return None, None, None


def find_laser_center(image, iris_x=None, iris_y=None, radius=None):
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()
        
    if iris_x is not None and iris_y is not None and radius is not None:
        mask = np.zeros_like(gray)
        cv2.circle(mask, (iris_x, iris_y), int(radius - 15), 255, -1)
        gray = cv2.bitwise_and(gray, mask)
        
    min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(gray)
    
    if max_val < 200:
        return None, None

    # Image Moments (Center of Mass)
    _, thresh = cv2.threshold(gray, 200, 255, cv2.THRESH_BINARY)
    M = cv2.moments(thresh)
    
    if M["m00"] != 0: 
        laser_x = int(M["m10"] / M["m00"])
        laser_y = int(M["m01"] / M["m00"])
    else:
        laser_x = int(max_loc[0])
        laser_y = int(max_loc[1])
    
    cv2.drawMarker(image, (laser_x, laser_y), (255, 0, 0), cv2.MARKER_CROSS, 20, 2)
    return laser_x, laser_y

# ==========================================
# 2. HARDWARE CONTROL FUNCTIONS
# ==========================================

def move_motor_and_wait(oUSB, strDeviceKey, channel, steps):
    if steps == 0: return
    strBldr = StringBuilder(128)
    oUSB.Query(strDeviceKey, f"{channel}PR{steps}", strBldr)
    
    is_moving = True
    while is_moving:
        strBldr.Remove(0, strBldr.Length)
        oUSB.Query(strDeviceKey, f"{channel}MD?", strBldr)
        if strBldr.ToString().strip() == "1":
            is_moving = False
        else:
            time.sleep(0.02)

def adjust_hardware_alignment(oUSB, strDeviceKey, delta_x, delta_y, actuator_num):
    """
    actuator_num: 1 (Channels 1 & 2) or 2 (Channels 3 & 4)
    """
    # Proportional Gain - Lowered to 0.5 for smoother "Beam Walking"
    GAIN = 0.5 
    MIN_STEPS = 15 

    # INDEPENDENT CALIBRATIONS
    if actuator_num == 1:
        # Actuator 1 (Cam 1)
        CH_X, CH_Y = 1, 2
        STEPS_PER_PIXEL_X = 20.0  
        STEPS_PER_PIXEL_Y = -20.0 # Adjust sign if it moves backwards!
    else:
        # Actuator 2 (Cam 2)
        CH_X, CH_Y = 3, 4
        STEPS_PER_PIXEL_X = 20.0  
        STEPS_PER_PIXEL_Y = -20.0 # Adjust sign if it moves backwards!

    steps_x = int(delta_x * STEPS_PER_PIXEL_X * GAIN)
    steps_y = int(delta_y * STEPS_PER_PIXEL_Y * GAIN)

    # Minimum Step Floor X
    if 0 < steps_x < MIN_STEPS: steps_x = MIN_STEPS
    elif 0 > steps_x > -MIN_STEPS: steps_x = -MIN_STEPS

    # Minimum Step Floor Y
    if 0 < steps_y < MIN_STEPS: steps_y = MIN_STEPS
    elif 0 > steps_y > -MIN_STEPS: steps_y = -MIN_STEPS

    # Execute moves
    if steps_x != 0:
        print(f"  -> Actuator {actuator_num} (Ch {CH_X}) moving {steps_x} steps...")
        move_motor_and_wait(oUSB, strDeviceKey, CH_X, steps_x)
    if steps_y != 0:
        print(f"  -> Actuator {actuator_num} (Ch {CH_Y}) moving {steps_y} steps...")
        move_motor_and_wait(oUSB, strDeviceKey, CH_Y, steps_y)

# ==========================================
# 3. MAIN EXECUTION LOOP
# ==========================================

def main():
    ALIGNMENT_TOLERANCE_PX = 3 
    
    # --- 1. INITIALIZE MOTORS ---
    oUSB = USB(True)
    if not oUSB.OpenDevices(0, True):
        print("ERROR: Could not open Newport USB devices.")
        sys.exit(1)
        
    try:
        oDeviceTable = oUSB.GetDeviceTable()
        if oDeviceTable.Count == 0: sys.exit(1)
        oEnumerator = oDeviceTable.GetEnumerator()
        oEnumerator.MoveNext()
        strDeviceKey = str(oEnumerator.Key)
        print("Connected Newport Controller:", strDeviceKey)

        # --- 2. INITIALIZE CAMERA ARRAY ---
        tlFactory = pylon.TlFactory.GetInstance()
        devices = tlFactory.EnumerateDevices()
        
        CAM1_SN = "25191527" # Looks at Iris 1, controlled by Actuator 1
        CAM2_SN = "25191524" # Looks at Iris 2, controlled by Actuator 2
        
        cameras = pylon.InstantCameraArray(2)
        cam1_found, cam2_found = False, False
        
        for dev in devices:
            sn = dev.GetSerialNumber()
            if sn == CAM1_SN:
                cameras[0].Attach(tlFactory.CreateDevice(dev))
                cameras[0].SetCameraContext(0) # Context 0 = Cam 1
                cam1_found = True
            elif sn == CAM2_SN:
                cameras[1].Attach(tlFactory.CreateDevice(dev))
                cameras[1].SetCameraContext(1) # Context 1 = Cam 2
                cam2_found = True
                
        if not (cam1_found and cam2_found):
            print(f"ERROR: Could not find both cameras!\nCam1 ({CAM1_SN}): {cam1_found}\nCam2 ({CAM2_SN}): {cam2_found}")
            sys.exit(1)

        cameras.Open()
        for i, cam in enumerate(cameras):
            try:
                cam.ExposureAuto.SetValue("Off")
                cam.ExposureTime.SetValue(7000.0) 
                cam.GainAuto.SetValue("Off")
                cam.Gain.SetValue(0.0)
            except Exception as e:
                print(f"Warning on Cam {i+1}: {e}")

        cameras.StartGrabbing(pylon.GrabStrategy_LatestImageOnly, pylon.GrabLoop_ProvidedByUser)
        
        print("\n--- Dual Closed-Loop Beam Walking Started ---")
        print("Press 'q' in any image window to stop.")
        
        # --- CREATE RESIZABLE WINDOWS ---
        cv2.namedWindow("Camera 1 (Iris 1)", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Camera 1 (Iris 1)", 800, 600)
        cv2.namedWindow("Camera 2 (Iris 2)", cv2.WINDOW_NORMAL)
        cv2.resizeWindow("Camera 2 (Iris 2)", 800, 600)
        
        # Buffer to ensure we act only when we have a fresh frame from BOTH cameras
        latest_frames = {0: None, 1: None}

        while cameras.IsGrabbing():
            grab_result = cameras.RetrieveResult(5000, pylon.TimeoutHandling_ThrowException)

            if grab_result.GrabSucceeded():
                cam_idx = grab_result.GetCameraContext()
                raw_image = grab_result.Array
                image = cv2.cvtColor(raw_image, cv2.COLOR_GRAY2BGR) if len(raw_image.shape) == 2 else raw_image.copy()
                latest_frames[cam_idx] = image
            
            grab_result.Release()

            # Process logic ONLY when we have a fresh frame from both cameras
            if latest_frames[0] is not None and latest_frames[1] is not None:
                img1 = latest_frames[0]
                img2 = latest_frames[1]

                # --- PROCESS CAM 1 (IRIS 1) ---
                i1_x, i1_y, r1 = find_iris_grid(img1)
                l1_x, l1_y = find_laser_center(img1, i1_x, i1_y, r1)
                
                cam1_aligned = False
                err1_x, err1_y = 0, 0
                if i1_x is not None and l1_x is not None:
                    err1_x, err1_y = l1_x - i1_x, l1_y - i1_y
                    cv2.line(img1, (i1_x, i1_y), (l1_x, l1_y), (0, 255, 255), 2)
                    cam1_aligned = (abs(err1_x) <= ALIGNMENT_TOLERANCE_PX and abs(err1_y) <= ALIGNMENT_TOLERANCE_PX)

                # --- PROCESS CAM 2 (IRIS 2) ---
                i2_x, i2_y, r2 = find_iris_grid(img2)
                l2_x, l2_y = find_laser_center(img2, i2_x, i2_y, r2)
                
                cam2_aligned = False
                err2_x, err2_y = 0, 0
                if i2_x is not None and l2_x is not None:
                    err2_x, err2_y = l2_x - i2_x, l2_y - i2_y
                    cv2.line(img2, (i2_x, i2_y), (l2_x, l2_y), (0, 255, 255), 2)
                    cam2_aligned = (abs(err2_x) <= ALIGNMENT_TOLERANCE_PX and abs(err2_y) <= ALIGNMENT_TOLERANCE_PX)

                # --- STATE MACHINE (UI & MOVEMENT) ---
                l1_present = l1_x is not None
                l2_present = l2_x is not None
                
                if not l1_present:
                    cv2.putText(img1, "BEAM LOST - HOLDING M1", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                    cv2.putText(img2, "WAITING FOR M1...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)
                    
                elif not cam1_aligned:
                    cv2.putText(img1, "ALIGNING M1...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                    if not l2_present:
                        cv2.putText(img2, "NO BEAM (WAITING FOR M1)", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)
                    else:
                        cv2.putText(img2, "WAITING FOR M1...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)
                    
                    # Priority 1: Fix Mirror 1
                    adjust_hardware_alignment(oUSB, strDeviceKey, err1_x, err1_y, actuator_num=1)

                elif cam1_aligned and not l2_present:
                    cv2.putText(img1, "M1 ALIGNED (WAITING)", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                    cv2.putText(img2, "BEAM LOST - HOLDING M2", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                    
                elif cam1_aligned and not cam2_aligned:
                    cv2.putText(img1, "WAITING FOR M2...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (200, 200, 200), 2)
                    cv2.putText(img2, "ALIGNING M2...", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 165, 255), 3)
                    
                    # Priority 2: Fix Mirror 2
                    adjust_hardware_alignment(oUSB, strDeviceKey, err2_x, err2_y, actuator_num=2)

                elif cam1_aligned and cam2_aligned:
                    cv2.putText(img1, "SYSTEM ALIGNED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)
                    cv2.putText(img2, "SYSTEM ALIGNED", (50, 50), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 3)

                # Show both windows side-by-side
                cv2.imshow("Camera 1 (Iris 1)", img1)
                cv2.imshow("Camera 2 (Iris 2)", img2)
                
                key = cv2.waitKey(1) & 0xFF
                if key == ord('q'):
                    print("\nUser requested exit.")
                    break

                # Reset frames buffer to wait for a fresh pair
                latest_frames = {0: None, 1: None}

    except KeyboardInterrupt:
        print("\nProcess interrupted by user.")

    finally:
        try:
            cameras.StopGrabbing()
            cameras.Close()
        except: pass
        cv2.destroyAllWindows()
        oUSB.CloseDevices()
        print("Hardware Communication Closed Cleanly.")

if __name__ == "__main__":
    main()