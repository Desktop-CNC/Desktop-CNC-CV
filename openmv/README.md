#OpenMV Camera Script Documentation

main.py is currently designed and planned to run on the OpenMV Cam H7 Plus. To run it on the camera, flash it to the camera as `main.py` with the OpenMV IDE (Tools -> Save script to OpenMV Cam)

Current Features:
- Live JPEG streaming to the bridge via serial.
- Supports firmware 4.x for the sensor and 4.8+/5.x for the csi.
- Support for custom CV tools. Add them to ANALYZERS, each uncompressed frame will run through these and return an event dictionary or None.

Notes:
- The OpenMV Cam H7 Plus has been discontinued, the camera has not currently been tested on other OpenMV cameras. In theory there should be no issues, but this is not a guarantee.
- Never use print() in this script as stdout shares the USB port with the binary stream. Use log("...") instead, which will send a packet to the bridge log.
