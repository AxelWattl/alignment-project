import sys

# will be using pyqt6 for this  GUI, from this library I import classes QApplication etc.
# Classes are like blueprints so we don't have to reinvent the wheel
from PyQt6.QtWidgets import QApplication, QWidget, QLabel, QVBoxLayout


# making a variable called app which is the whole GUI application
# sys.argv lets the application read the command that was used to launch it eg. "python main.py" 
app = QApplication(sys.argv)

# QWidget handles the window / container that will pop up when the script is run
window1 = QWidget()

window2 = QWidget
# self explanitory, the variable will have a title
window1.setWindowTitle("PyQt6 test run")

# resizing the window 
window1.resize(700, 500)

# QVBoxLayout class handles the layout of the objects inside the application, showing from top to bottom
layout = QVBoxLayout()

# text to be displayed
label = QLabel("Alignment Tool Version 0.5")

'# This takes the variable "label" and displays it.
layout.addWidget(label)

window1.setLayout(layout)

window1.show()

window2.show()

sys.exit(app.exec())
