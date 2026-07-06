# Automated Laser Alignment System

This project is a Python-based automated laser alignment system designed to align a laser beam through two iris reference points using camera feedback and motorized mirror mounts.

The system uses two Basler cameras to detect the position of the laser beam relative to the center of each iris. The software calculates the beam offset in pixel units and sends corrective movement commands to Newport Picomotor actuators. A PyQt6 graphical user interface displays live camera feeds, alignment status, current beam coordinates, manual target selection, and system logs.

## Project Overview

Manual laser alignment can be time-consuming and sensitive to small mirror adjustments, especially when a beam must pass through multiple fixed reference points. This project automates that process by using computer vision and motorized mirror control.

The program performs the following main tasks:

* Captures live images from two Basler cameras
* Detects the iris center using image processing
* Detects the laser beam center using intensity thresholding
* Calculates the beam error relative to the iris center
* Sends correction commands to Newport Picomotor actuators
* Displays live camera feeds and alignment status in a GUI
* Allows manual target selection by clicking on the camera image
* Stops motor motion once alignment is within tolerance
* Monitors the beam for drift after alignment

## Features

* Live dual-camera display
* Automatic laser-to-iris alignment
* Manual click-to-target movement
* Real-time laser coordinate tracking
* Alignment status messages
* System log window
* Image saving option during alignment
* Camera exposure and gain settings
* Drift monitoring after alignment
* Separate backend and GUI code structure

## File Structure

```text
.
├── Final_Alignment.py
├── functional_code.py
├── ui_code.py
└── README.md
```

### `Final_Alignment.py`

This is the combined version of the project. It contains both the hardware/control logic and the PyQt6 graphical user interface in one file.

Use this file if you want to run the entire program from a single script.

### `functional_code.py`

This file contains the backend functionality, including:

* Basler camera acquisition
* Newport Picomotor communication
* Iris detection
* Laser center detection
* Motor movement commands
* Alignment control loop
* Drift monitoring
* Hardware thread logic

### `ui_code.py`

This file contains the PyQt6 graphical user interface, including:

* Main application window
* Camera display panels
* Alignment control buttons
* Manual target selection
* Settings dialog
* Log display
* Status messages

The UI imports the hardware thread from `functional_code.py`.

## Hardware Requirements

This project was designed around the following hardware:

* Helium-Neon laser or another visible laser source
* Two optical irises
* Two steering mirrors
* Two-axis Newport/New Focus Picomotor mirror mounts
* Newport Picomotor controller
* Two Basler cameras
* Host computer with Python installed
* Required camera and motor controller drivers

## Software Requirements

The project requires Python and the following Python packages:

```bash
pip install numpy opencv-python PyQt6 pythonnet pypylon
```

Additional software/drivers may be required:

* Basler Pylon SDK
* Newport USB communication driver
* Newport `UsbDllWrap` DLL
* Proper camera network configuration if using GigE cameras

## Setup

1. Clone this repository:

```bash
git clone https://github.com/your-username/your-repository-name.git
cd your-repository-name
```

2. Install the required Python packages:

```bash
pip install numpy opencv-python PyQt6 pythonnet pypylon
```

3. Make sure the Newport DLL file is available in the same folder as the Python script or in a location accessible by Python.

4. Connect the Basler cameras and verify that they are detected by the Basler Pylon software.

5. Connect the Newport Picomotor controller and verify that the motors respond using the Newport software.

6. Confirm that the camera serial numbers in the code match the cameras being used.

In the code, the expected camera serial numbers are set as:

```python
CAM1_SN = "25191527"
CAM2_SN = "25191524"
```

Update these values if different cameras are being used.

## Running the Program

To run the combined version:

```bash
python Final_Alignment.py
```

## How the Alignment Works

The alignment process uses two camera views and two motorized mirror mounts.

1. Camera 1 detects the laser position and the center of the first iris.
2. The software calculates the error between the laser center and iris center.
3. Mirror 1 is adjusted until the beam is centered on Camera 1.
4. Once Camera 1 is locked, Camera 2 is used to align the beam through the second iris.
5. Mirror 2 is adjusted until the beam is centered on Camera 2.
6. When both cameras are within tolerance, the system stops motor movement.
7. The program continues monitoring the beam for drift.

The program uses different correction sizes depending on the size of the error:

* Large error: larger motor steps
* Medium error: smaller correction steps
* Small error: fine adjustment steps

This helps reduce overshoot while still allowing the system to correct large misalignments.

## User Interface

The GUI includes:

* A camera tab showing Camera 1 and Camera 2
* A log tab showing system messages
* Current laser position display
* Selected target position display
* `Go to Target` button for manual movement
* `Start Alignment` button for automatic alignment
* `Stop` button to halt motion
* Settings for saving images, exposure, and gain

## Manual Target Mode

The user can manually select a target point by clicking on either camera image. After selecting a point, pressing `Go to Target` commands the system to move the beam toward that clicked location.

This is useful for testing motor response or manually steering the beam before starting full automatic alignment. 

Disclaimer: 
This has not been fully tested yet and may not work properly. 

## Notes and Limitations

This project depends on physical hardware and may not run correctly without the required cameras, motor controller, drivers, and DLL files.

The current image processing method may be sensitive to:

* Ambient room lighting
* Camera exposure settings
* Laser brightness
* Iris visibility
* Reflections or unwanted bright spots
* Motor communication stability

Future improvements could include:

* Better filtering for laser spot detection
* More robust iris detection
* A proportional-integral control loop
* Improved motor communication error handling
* Calibration tools for steps-per-pixel values
* A demo mode for testing the GUI without hardware
* Saving alignment data to a CSV file
* Plotting error versus time during alignment


## Safety Notice

This project involves laser equipment and motorized optical components. Always follow proper laser safety procedures, including the use of appropriate eyewear, controlled beam paths, and safe laboratory practices.

## Acknowledgments

This project was developed as part of an optics and laser alignment research project involving automated beam steering, camera feedback, and programmable Picomotor mirror mounts. Special thanks to our mentor, Mikhail (Misha) Polyanskiy for inspiring this project, as well as to our professor, Viviana Vladutescu.

