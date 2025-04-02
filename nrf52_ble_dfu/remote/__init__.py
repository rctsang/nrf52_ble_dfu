from collections import namedtuple

# the DfuTarg BLEDevice can also be scanned for, it should
# be named "DfuTarg".
# this was the address found during testing
DFUTARG_ADDRESS = "266dfb7f-a487-da11-0b0c-b9764c63681b"

# these are the DFU Service characteristics' UUIDs,
# as defined in the nordic DFU BLE spec:
# https://docs.nordicsemi.com/bundle/sdk_nrf5_v17.0.2/page/lib_dfu_transport_ble.html
DFU_CTRL_POINT_UUID = "8ec90001-f315-4f60-9fb8-838830daea50"
DFU_PACKET_UUID     = "8ec90002-f315-4f60-9fb8-838830daea50"

Notification = namedtuple(
    "Notification", ["sender", "time", "data"])