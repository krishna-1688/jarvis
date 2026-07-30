import openwakeword
openwakeword.utils.download_models()

from openwakeword.model import Model
import os
import openwakeword as oww

# Find model directory
oww_dir = os.path.dirname(oww.__file__)
print(f"OpenWakeWord directory: {oww_dir}")

# List all files
for root, dirs, files in os.walk(oww_dir):
    for file in files:
        if file.endswith('.onnx') or file.endswith('.tflite'):
            print(f"Model found: {file}")