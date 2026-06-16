#TODO:
# - Utilize henry's script to obtain input parameters for motor movement.


import time
import sys
import clr

# IMPORTANT: point to Newport DLL folder will be different for each user, check in the application folder for starters

sys.path.append(r"C:\Program Files\New Focus\New Focus Picomotor Application\Bin")

clr.AddReference("UsbDllWrap")

from Newport.USBComm import USB
from System.Text import StringBuilder

oUSB = USB(True)

oUSB.OpenDevices(0, True)

device = "8742 104479"

def move(motor, steps):
    oUSB.Query(device, f"{motor}PR{steps}", StringBuilder(64))

def position(motor):
    sb = StringBuilder(64)
    oUSB.Query(device, f"{motor}TP?", sb)
    return sb.ToString()

move(1, 300)
time.sleep(1)

move(1, -300)
time.sleep(1)

print("Position:", position(1))

oUSB.CloseDevices()