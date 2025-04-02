from collections import namedtuple
from enum import Enum

# the following error enums are based on Nordic's iOS DFU library
# https://github.com/NordicSemiconductor/IOS-DFU-Library

class DFUError(Exception):
    """An error occured during device firmware update"""
    def __init__(self, code: int, message: str, *args):
        self.message = message
        self.code = code
        super().__init__(code, message, *args)

    def ok(self):
        return self.code in [1, 11, 91, 9001]


class DFURemoteErrorCode(DFUError, Enum):
    """Offsets for types of remote dfu error codes"""
    
    LEGACY                  = (0,    "A remote error returned from Legacy DFU bootloader")
    SECURE                  = (10,   "A remote error returned from Secure DFU bootloader")
    SECURE_EXTENDED         = (20,   "An extended error returned from Secure DFU bootloader")
    BUTTONLESS              = (90,   "A remote error returned from Buttonless service")
    EXPERIMENTAL_BUTTONLESS = (9000, "A remote error returned from the experimental Buttonless service from SDK 12")

    def as_err(self):
        return self.value

class DFUErrorCode(DFUError, Enum):
    """DFU error codes and descriptions"""

    # legacy errors
    REMOTE_LEGACY_DFU_SUCCESS                   = (1,    "Legacy DFU bootloader reported success")
    REMOTE_LEGACY_DFU_INVALID_STATE             = (2,    "Legacy DFU bootloader is in invalid stat")
    REMOTE_LEGACY_DFU_NOT_SUPPORTED             = (3,    "Requested operation not supported")
    REMOTE_LEGACY_DFU_DATA_EXCEEDS_LIMIT        = (4,    "Firmware size exceeds limit")
    REMOTE_LEGACY_DFU_CRC_ERROR                 = (5,    "CRC checksum error")
    REMOTE_LEGACY_DFU_OPERATION_FAILED          = (6,    "Operation failed for unknown reason")

    # secure dfu errors (received value + 10, to prevent overlap)
    REMOTE_SECURE_DFU_SUCCESS                   = (11,   "Secure DFU bootloader reported success")
    REMOTE_SECURE_DFU_OPCODE_NOT_SUPPORTED      = (12,   "Requested Opcode is not supported")
    REMOTE_SECURE_DFU_INVALID_PARAMETER         = (13,   "Invalid Parameter")
    REMOTE_SECURE_DFU_INSUFFICIENT_RESOURCES    = (14,   "Secure DFU bootloader cannot complete due to insufficient resources")
    REMOTE_SECURE_DFU_INVALID_OBJECT            = (15,   "Object is invalid")
    # REMOTE_SECURE_DFU_SIGNATURE_MISMATCH        = DFUError(16,   "Firmware signature is invalid") # unused
    REMOTE_SECURE_DFU_UNSUPPORTED_TYPE          = (17,   "Requested type is not supported")
    REMOTE_SECURE_DFU_OPERATION_NOT_PERMITTED   = (18,   "Requested operation is not permitted")
    REMOTE_SECURE_DFU_OPERATION_FAILED          = (20,   "Operation failed for an unknown reason")
    REMOTE_SECURE_DFU_EXTENDED_ERROR            = (21,   "Secure DFU bootloader reported a detailed error")

    # detailed extended errors
    # REMOTE_EXTENDED_ERROR_WRONG_COMMAND_FORMAT  = DFUError(22,   "Format of the command was incorrect") # unused
    REMOTE_EXTENDED_ERROR_UNKNOWN_COMMAND       = (23,   "Command successfully parsed, but not supported or unknown")
    REMOTE_EXTENDED_ERROR_INIT_COMMAND_INVALID  = (24,   "Init command has invalid update type or missing requred fields")
    REMOTE_EXTENDED_ERROR_FW_VERSION_FAILURE    = (25,   "Firmware version is older than current version, cannot downgrade")
    REMOTE_EXTENDED_ERROR_HW_VERSION_FAILURE    = (26,   "Hardware version of device does not match required version for update")
    REMOTE_EXTENDED_ERROR_SD_VERSION_FAILIRE    = (27,   "Current SoftDevice FWID does not support the update, or first FWID is '0' on bootloader that requires SoftDevice")
    # REMOTE_EXTENDED_ERROR_SIGNATURE_MISSING     = DFUError(28,   "Init packet does not contain a signature") # unused
    REMOTE_EXTENDED_ERROR_WRONG_HASH_TYPE       = (29,   "Hash type specified by init packet is not supported by the DFU bootloader")
    REMOTE_EXTENDED_ERROR_HASH_FAILED           = (30,   "Firmware image hash cannot be calculated")
    REMOTE_EXTENDED_ERROR_WRONG_SIGNATURE_TYPE  = (31,   "Signature type is unknown or not supported by the DFU bootloader")
    REMOTE_EXTENDED_ERROR_VERIFICATION_FAILED   = (32,   "Hash of received firmware image does not match hash in init packet")
    REMOTE_EXTENDED_ERROR_INSUFFICIENT_SPACE    = (33,   "Available space on device is insufficient to hold firmware")

    # experimental buttonless dfu errors (received value + 9000 due to overlap)
    REMOTE_EXPERIMENTAL_BUTTONLESS_DFU_SUCCESS              = (9001, "Experimental Buttonless DFU service reported success")
    REMOTE_EXPERIMENTAL_BUTTONLESS_DFU_OPCODE_NOT_SUPPORTED = (9002, "Opcode not supported")
    REMOTE_EXPERIMENTAL_BUTTONLESS_DFU_OPERATION_FAILED     = (9004, "Jumping to bootloader mode failed")

    # buttonless dfu errors (recieved value + 90 due to overlap)
    REMOTE_BUTTONLESS_DFU_SUCCESS                       = (91,   "Buttonless DFU service reported success")
    REMOTE_BUTTONLESS_DFU_OPCODE_NOT_SUPPORTED          = (92,   "Opcode not supported")
    REMOTE_BUTTONLESS_DFU_OPERATION_FAILED              = (94,   "Jumping to bootloader mode failed")
    REMOTE_BUTTONLESS_DFU_INVALID_ADVERTISEMENT_NAME    = (95,   "Requested advertising name is invalid, maximum name length is 20 bytes")
    REMOTE_BUTTONLESS_DFU_BUSY                          = (96,   "Service is busy")
    REMOTE_BUTTONLESS_DFU_NOT_BONDED                    = (97,   "Buttonless service requires device to be bonded")

    # non-remote errors (100+)
    FILE_NOT_SPECIFIED                  = (101,  "Providing DFU firmware is required")
    FILE_INVALID                        = (102,  "Given firmware file is not supported")
    EXTENDED_INIT_PACKET_REQUIRED       = (103,  "DFU bootloader requires extended Init Packet (>= v7.0.0 sdk)")
    INIT_PACKET_REQUIRED                = (104,  "Init packet is required and has not been found")

    FAILED_TO_CONNECT                   = (201,  "DFU service failed to connect to target peripheral")
    DEVICE_DISCONNECTED                 = (202,  "DFU target disconnected unexpectedly")
    BLUETOOTH_DISABLED                  = (203,  "Bluetooth adapter is disabled")

    SERVICE_DISCOVERY_FAILED            = (301,  "Service discovery has failed")
    DEVICE_NOT_SUPPORTED                = (302,  "Selected device does not support legacy, secure, or buttonless DFU")
    READING_VERSION_FAILED              = (303,  "Reading DFU version characteristic has failed")
    ENABLING_CONTROL_POINT_FAILED       = (304,  "Enabling control point notifications has failed")
    WRITING_CHARACTERISITIC_FAILED      = (305,  "Failed to write to characteristic")
    RECEIVING_NOTIFICATIONS_FAILED      = (306,  "An error was reported for a notification")
    UNSUPPORTED_RESPONSE                = (307,  "Received response is not supported")
    BYTES_LOST                          = (308,  "Number of bytes sent is not equal to number of bytes confirmed in packet receipt notification during upload")
    CRC_ERROR                           = (309,  "CRC reported by remote device does not match after 3 attempts to send data")
    INVALID_INTERNAL_STATE              = (500,  "Service went into an invalid state. Attempt to close without crashing. Returning to known state impossible")

    def as_err(self):
        return self.value

    def is_remote(self):
        """true if error was caused by remote device or occurred locally"""
        return self.value.code < 100 or self.value.code > 9000


