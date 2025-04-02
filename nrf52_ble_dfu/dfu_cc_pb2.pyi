from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class FwType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    APPLICATION: _ClassVar[FwType]
    SOFTDEVICE: _ClassVar[FwType]
    BOOTLOADER: _ClassVar[FwType]
    SOFTDEVICE_BOOTLOADER: _ClassVar[FwType]
    EXTERNAL_APPLICATION: _ClassVar[FwType]

class HashType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    NO_HASH: _ClassVar[HashType]
    CRC: _ClassVar[HashType]
    SHA128: _ClassVar[HashType]
    SHA256: _ClassVar[HashType]
    SHA512: _ClassVar[HashType]

class OpCode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    INIT: _ClassVar[OpCode]

class ValidationType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    NO_VALIDATION: _ClassVar[ValidationType]
    VALIDATE_GENERATED_CRC: _ClassVar[ValidationType]
    VALIDATE_SHA256: _ClassVar[ValidationType]
    VALIDATE_ECDSA_P256_SHA256: _ClassVar[ValidationType]

class SignatureType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ECDSA_P256_SHA256: _ClassVar[SignatureType]
    ED25519: _ClassVar[SignatureType]
APPLICATION: FwType
SOFTDEVICE: FwType
BOOTLOADER: FwType
SOFTDEVICE_BOOTLOADER: FwType
EXTERNAL_APPLICATION: FwType
NO_HASH: HashType
CRC: HashType
SHA128: HashType
SHA256: HashType
SHA512: HashType
INIT: OpCode
NO_VALIDATION: ValidationType
VALIDATE_GENERATED_CRC: ValidationType
VALIDATE_SHA256: ValidationType
VALIDATE_ECDSA_P256_SHA256: ValidationType
ECDSA_P256_SHA256: SignatureType
ED25519: SignatureType

class Hash(_message.Message):
    __slots__ = ("hash_type", "hash")
    HASH_TYPE_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    hash_type: HashType
    hash: bytes
    def __init__(self, hash_type: _Optional[_Union[HashType, str]] = ..., hash: _Optional[bytes] = ...) -> None: ...

class BootValidation(_message.Message):
    __slots__ = ("type", "bytes")
    TYPE_FIELD_NUMBER: _ClassVar[int]
    BYTES_FIELD_NUMBER: _ClassVar[int]
    type: ValidationType
    bytes: bytes
    def __init__(self, type: _Optional[_Union[ValidationType, str]] = ..., bytes: _Optional[bytes] = ...) -> None: ...

class InitCommand(_message.Message):
    __slots__ = ("fw_version", "hw_version", "sd_req", "type", "sd_size", "bl_size", "app_size", "hash", "is_debug", "boot_validation")
    FW_VERSION_FIELD_NUMBER: _ClassVar[int]
    HW_VERSION_FIELD_NUMBER: _ClassVar[int]
    SD_REQ_FIELD_NUMBER: _ClassVar[int]
    TYPE_FIELD_NUMBER: _ClassVar[int]
    SD_SIZE_FIELD_NUMBER: _ClassVar[int]
    BL_SIZE_FIELD_NUMBER: _ClassVar[int]
    APP_SIZE_FIELD_NUMBER: _ClassVar[int]
    HASH_FIELD_NUMBER: _ClassVar[int]
    IS_DEBUG_FIELD_NUMBER: _ClassVar[int]
    BOOT_VALIDATION_FIELD_NUMBER: _ClassVar[int]
    fw_version: int
    hw_version: int
    sd_req: _containers.RepeatedScalarFieldContainer[int]
    type: FwType
    sd_size: int
    bl_size: int
    app_size: int
    hash: Hash
    is_debug: bool
    boot_validation: _containers.RepeatedCompositeFieldContainer[BootValidation]
    def __init__(self, fw_version: _Optional[int] = ..., hw_version: _Optional[int] = ..., sd_req: _Optional[_Iterable[int]] = ..., type: _Optional[_Union[FwType, str]] = ..., sd_size: _Optional[int] = ..., bl_size: _Optional[int] = ..., app_size: _Optional[int] = ..., hash: _Optional[_Union[Hash, _Mapping]] = ..., is_debug: bool = ..., boot_validation: _Optional[_Iterable[_Union[BootValidation, _Mapping]]] = ...) -> None: ...

class Command(_message.Message):
    __slots__ = ("op_code", "init")
    OP_CODE_FIELD_NUMBER: _ClassVar[int]
    INIT_FIELD_NUMBER: _ClassVar[int]
    op_code: OpCode
    init: InitCommand
    def __init__(self, op_code: _Optional[_Union[OpCode, str]] = ..., init: _Optional[_Union[InitCommand, _Mapping]] = ...) -> None: ...

class SignedCommand(_message.Message):
    __slots__ = ("command", "signature_type", "signature")
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    command: Command
    signature_type: SignatureType
    signature: bytes
    def __init__(self, command: _Optional[_Union[Command, _Mapping]] = ..., signature_type: _Optional[_Union[SignatureType, str]] = ..., signature: _Optional[bytes] = ...) -> None: ...

class Packet(_message.Message):
    __slots__ = ("command", "signed_command")
    COMMAND_FIELD_NUMBER: _ClassVar[int]
    SIGNED_COMMAND_FIELD_NUMBER: _ClassVar[int]
    command: Command
    signed_command: SignedCommand
    def __init__(self, command: _Optional[_Union[Command, _Mapping]] = ..., signed_command: _Optional[_Union[SignedCommand, _Mapping]] = ...) -> None: ...
