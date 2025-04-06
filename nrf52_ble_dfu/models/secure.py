from typing import assert_never
from collections import namedtuple
from enum import Enum
from binascii import hexlify

from ..error import *
from ..dfu_cc_pb2 import *


class SecureDFUOpcode(int, Enum):
    """Opcodes for secure DFU bootloader"""

    PROTOCOL_VERSION    = 0x00 # unsupported
    OBJECT_CREATE       = 0x01
    RECEIPT_NOTIF_SET   = 0x02
    CRC_GET             = 0x03
    OBJECT_EXECUTE      = 0x04
    OBJECT_SELECT       = 0x06
    MTU_GET             = 0x07 # unsupported
    OBJECT_WRITE        = 0x08 # unsupported
    PING                = 0x09 # unsupported
    HARDWARE_VERSION    = 0x0A # unsupported
    FIRMWARE_VERSION    = 0x0B # unsupported
    ABORT               = 0x0C
    RESPONSE            = 0x60

    def __format__(self):
        return self.name

    def macro(self):
        return f"NRF_DFU_OP_{self.name}"

    @property
    def description(self) -> str:
        match self:
            case self.PROTOCOL_VERSION:  return "Get Protocol Version"
            case self.OBJECT_CREATE:     return "Create Object"
            case self.RECEIPT_NOTIF_SET: return "Set PRN Value"
            case self.CRC_GET:           return "Calculate Checksum"
            case self.OBJECT_EXECUTE:    return "Execute"
            case self.OBJECT_SELECT:     return "Select Object"
            case self.MTU_GET:           return "Get MTU"
            case self.OBJECT_WRITE:      return "Write"
            case self.PING:              return "Ping"
            case self.HARDWARE_VERSION:  return "Get Hw Version"
            case self.FIRMWARE_VERSION:  return "Get Fw Version"
            case self.ABORT:             return "Abort"
            case self.RESPONSE:          return "Response"
            case _:                 assert_never("Invalid opcode")

    def to_bytes(self) -> bytes:
        return self.value.to_bytes(1, 'little')


class SecureDFUExtendedErrorCode(int, Enum):
    """extended error codes that are converted to DFUErrorCode"""
    NO_ERROR                = 0x00
    # WRONG_COMMAND_FORMAT    = 0x02
    UNKNOWN_COMMAND         = 0x03
    INIT_COMMAND_INVALID    = 0x04
    FW_VERSION_FAILURE      = 0x05
    HW_VERSION_FAILURE      = 0x06
    SD_VERSION_FAILURE      = 0x07
    # SIGNATURE_MISSING       = 0x08
    WRONG_HASH_TYPE         = 0x09
    HASH_FAILED             = 0x0A
    WRONG_SIGNATURE_TYPE    = 0x0B
    VERIFICATION_FAILED     = 0x0C
    INSUFFICIENT_SPACE      = 0x0D

    def error(self):
        match self:
            case self.NO_ERROR:
                return DFUErrorCode.REMOTE_SECURE_DFU_SUCCESS
            case _:
                return DFUErrorCode[f"REMOTE_EXTENDED_ERROR_{self.name}"]

    @property
    def description(self):
        match self:
            case self.NO_ERROR:              return "No error"
            # case self.WRONG_COMMAND_FORMAT:  return "Wrong command format"
            case self.UNKNOWN_COMMAND:       return "Unknown command"
            case self.INIT_COMMAND_INVALID:  return "Init command was invalid"
            case self.FW_VERSION_FAILURE:    return "FW version check failed"
            case self.HW_VERSION_FAILURE:    return "HW version check failed"
            case self.SD_VERSION_FAILURE:    return "SD version check failed"
            # case self.SIGNATURE_MISSING:     return "Signature missing"
            case self.WRONG_HASH_TYPE:       return "Invalid hash type"
            case self.HASH_FAILED:           return "Hashing failed"
            case self.WRONG_SIGNATURE_TYPE:  return "Invalid signature type"
            case self.VERIFICATION_FAILED:   return "Verification failed"
            case self.INSUFFICIENT_SPACE:    return "Insufficient space for upgrade"
            case _:                     assert_never("Invalid error code")


class SecureDFUProcedureType(int, Enum):
    INVALID = 0
    COMMAND = 1
    DATA    = 2

    def macro(self):
        return f"NRF_DFU_OBJ_TYPE_{self.name}"

    def to_bytes(self):
        return self.value.to_bytes(1, 'little')

class SecureDFUImageType(int, Enum):
    """corresponding to nrf_dfu_firmware_type_t received in response"""
    SOFTDEVICE  = 0x00
    APPLICATION = 0x01
    BOOTLOADER  = 0x02
    UNKNOWN     = 0xFF

    @classmethod
    def _missing_(cls, value):
        return cls.UNKNOWN

    def to_bytes(self):
        return self.value.to_bytes(1, 'little')

class SecureDFUResultCode(int, Enum):
    INVALID                 = 0x00
    SUCCESS                 = 0x01
    OPCODE_NOT_SUPPORTED    = 0x02
    INVALID_PARAMETER       = 0x03
    INSUFFICIENT_RESOURCES  = 0x04
    INVALID_OBJECT          = 0x05
    UNSUPPORTED_TYPE        = 0x07
    OPERATION_NOT_PERMITTED = 0x08
    OPERATION_FAILED        = 0x0A
    EXTENDED_ERROR          = 0x0B

    def macro(self):
        return f"NRF_DFU_RES_CODE_{self.name}"

    def error(self):
        return DFUErrorCode[f"REMOTE_SECURE_DFU_{self.name}"]

    @property
    def description(self):
        match self:
            case self.INVALID:                   return "INVALID CODE"
            case self.SUCCESS:                   return "SUCCESS"
            case self.OPCODE_NOT_SUPPORTED:      return "OPERATION NOT SUPPORTED"
            case self.INVALID_PARAMETER:         return "INVALID PARAMETER"
            case self.INSUFFICIENT_RESOURCES:    return "INSUFFICIENT RESOURCES"
            case self.INVALID_OBJECT:            return "INVALID OBJECT"
            case self.UNSUPPORTED_TYPE:          return "UNSUPPORTED TYPE"
            case self.OPERATION_NOT_PERMITTED:   return "OPERATION NOT PERMITTED"
            case self.OPERATION_FAILED:          return "OPERATION FAILED"
            case self.EXTENDED_ERROR:            return "EXTENDED ERROR"

    @property
    def code(self):
        return self.value

class SecureDFURequest:
    def __init__(self,
        opcode: SecureDFUOpcode,
        object_type: SecureDFUProcedureType = None,
        object_size: int = None,
        prn_value: int = None,
        payload: bytes = None,
        ping_id: int = None,
        image_type: SecureDFUImageType = None,
    ):
        self.opcode = opcode
        self.data = bytearray(opcode.to_bytes())
        match opcode:
            case SecureDFUOpcode.OBJECT_CREATE:
                assert object_type, "expected command or data type"
                assert object_size, "expected nonzero object size"
                self.data.extend(object_type.to_bytes())
                self.data.extend(object_size.to_bytes(4, 'little'))
                self.object_type = object_type
                self.object_size = object_size
            case SecureDFUOpcode.OBJECT_SELECT:
                assert object_type, "expected command or data type"
                self.data.extend(object_type.to_bytes())
                self.object_type = object_type
            case SecureDFUOpcode.RECEIPT_NOTIF_SET:
                assert prn_value is not None, "PRN value must be specified"
                self.data.extend(prn_value.to_bytes(2, 'little'))
                self.prn_value = prn_value
            case SecureDFUOpcode.OBJECT_WRITE:
                assert payload is not None, "expected a payload"
                assert len(payload) <= 20, "20 bytes can be sent at once at most"
                self.data.extend(payload)
                self.data.extend(len(payload).to_bytes(2, 'little'))
                self.payload = payload
            case SecureDFUOpcode.PING:
                assert ping_id is not None, "expected a ping_id"
                self.data.extend(ping_id.to_bytes(1, 'little'))
                self.ping_id = ping_id
            case SecureDFUOpcode.FIRMWARE_VERSION:
                assert image_type is not None, "expected an image type"
                self.data.extend(image_type.to_bytes())
                self.image_type = image_type
            case _:
                pass

    @property
    def description(self):
        match self.opcode:
            case SecureDFUOpcode.PROTOCOL_VERSION:  return f"GET PROTOCOL VERSION [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.OBJECT_CREATE:     return f"CREATE {self.object_type.name} OBJECT {{ size = {self.object_size:#x} }} [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.RECEIPT_NOTIF_SET: return f"PACKET RECEIPT NOTIF REQ {{ value = {self.prn_value} }} [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.CRC_GET:           return f"CALCULATE CHECKSUM [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.OBJECT_EXECUTE:    return f"EXECUTE OBJECT [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.OBJECT_SELECT:     return f"SELECT {self.object_type.name} OBJECT [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.MTU_GET:           return f"GET MTU [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.OBJECT_WRITE:      return f"WRITE [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.PING:              return f"PING {{ id={self.ping_id} }} [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.HARDWARE_VERSION:  return f"GET HW VERSION [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.FIRMWARE_VERSION:  return f"GET FW VERSION {{ type={self.image_type.name} }} [ {hexlify(self.data, ' ')} ]"
            case SecureDFUOpcode.ABORT:             return f"ABORT [ {hexlify(self.data, ' ')} ]"
            case _:                                 assert_never("Invalid opcode")
    

class SecureDFUResponse:
    __slots__ = [
        "data",
        "opcode",
        "req_opcode",
        "status",
        "max_size",
        "offset",
        "crc",
        "error",
    ]

    def __init__(self, data: bytearray):
        assert isinstance(data, bytearray)
        assert len(data) >= 3, "not enough data, invalid response"
        self.data = data
        self.opcode = SecureDFUOpcode(data[0])
        assert self.opcode == SecureDFUOpcode.RESPONSE, "not a DFU response"
        self.req_opcode = SecureDFUOpcode(data[1])
        self.status = SecureDFUResultCode(data[2])

        self.max_size = None
        self.offset = None
        self.crc = None
        self.error = None

        # self.proto_version = None
        # self.mtu_size = None
        # self.ping_id = None
        # self.part = None
        # self.variant = None
        # self.fw_type = None
        # self.fw_version = None
        # self.fw_start_address = None
        # self.fw_size = None

        if self.status == SecureDFUResultCode.EXTENDED_ERROR:
            assert len(data) >= 4, "not enough data, expected extended error code"
            self.error = SecureDFUExtendedErrorCode(int.from_bytes(data[3], 'little'))
            return
        elif self.status != SecureDFUResultCode.SUCCESS:
            return

        match self.req_opcode:
            case SecureDFUOpcode.OBJECT_SELECT:
                assert len(data) >= 15, "additional 12 bytes expected for SELECT response"
                self.max_size = int.from_bytes(data[3:7], 'little')
                self.offset = int.from_bytes(data[7:11], 'little')
                self.crc = int.from_bytes(data[11:15], 'little')
            case SecureDFUOpcode.CRC_GET:
                assert len(data) >= 11, "additional 8 bytes expected for CRC command"
                self.offset = int.from_bytes(data[3:7], 'little')
                self.crc = int.from_bytes(data[7:11], 'little')
            case _:
                pass

    @property
    def description(self):
        status = self.status.description
        if self.status == SecureDFUResultCode.EXTENDED_ERROR:
            details = self.error
        elif self.status != SecureDFUResultCode.SUCCESS:
            return f"RESPONSE: {self.req_opcode.name} - {status}"

        match self.req_opcode:
            case SecureDFUOpcode.CRC_GET:
                details = f"[ offset={self.offset:#x}; crc={self.crc:#010x} ]"
            case SecureDFUOpcode.OBJECT_SELECT:
                details = f"[ max_size={self.max_size:#x}; offset={self.offset:#x}; crc={self.crc:#010x} ]"
            case _:
                details = "[ ]"

        return f"RESPONSE: {self.req_opcode.name} - {status}:{details}"

    def ok(self):
        return self.status == SecureDFUResultCode.SUCCESS


class SecureDFUPacketReceiptNotification:
    __slots__ = [
        "opcode",
        "req_opcode",
        "status",
        "offset",
        "crc",
    ]

    def __init__(self, data):
        assert len(data) >= 3, "malformed response data: {}".format(hexlify(data, ' '))
        self.opcode = SecureDFUOpcode(data[0])
        self.req_opcode = SecureDFUOpcode(data[1])
        self.status = SecureDFUResultCode(data[2])

        if self.status == SecureDFUResultCode.EXTENDED_ERROR:
            assert len(data) >= 4, "not enough data, expected extended error code"
            self.error = SecureDFUExtendedErrorCode(int.from_bytes(data[3], 'little'))
            return
        elif self.status != SecureDFUResultCode.SUCCESS:
            return

        assert len(data) >= 11, "PRN always has at least 11 bytes"
        self.offset = int.from_bytes(data[3:7], 'little')
        self.crc = int.from_bytes(data[7:11], 'little')


    @property
    def description(self):
        status = self.status.description
        if self.status == SecureDFUResultCode.EXTENDED_ERROR:
            details = self.error
        elif self.status != SecureDFUResultCode.SUCCESS:
            return f"PRN: {self.req_opcode.name} - {status}"
        else:
            details = f"[ offset={self.offset:#x}; crc={self.crc:#010x} ]"

        return f"PRN: {self.req_opcode.name} - {status}:{details}"

    def ok(self):
        return self.status == SecureDFUResultCode.SUCCESS    
