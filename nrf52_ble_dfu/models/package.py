import json
from collections import namedtuple
from pathlib import Path

from zipfile import ZipFile, is_zipfile
from tempfile import TemporaryDirectory as TmpDir
from hashlib import sha256, sha512
from zlib import crc32

from ..dfu_cc_pb2 import *

class DFUImage:
    __slots__ = [
        'img_type',     # image type (bootloader, softdevice, application)
        'img_file',     # image file in zip
        'init_file',    # init packet file in zip
        'img_data',     # image binary
        'init_data',    # init packet binary
        'init_pkt',     # init packet as Packet object
    ]

    def __init__(self, 
        img_type: str,
        img_file: str,
        init_file: str,
        img_data: bytearray,
        init_data: bytearray,
        init_pkt: Packet,
    ):
        self.img_type   = img_type
        self.img_file   = img_file
        self.init_file  = init_file
        self.img_data   = img_data
        self.init_data  = init_data
        self.init_pkt   = init_pkt

class DFUPackage:
    def __init__(self, path: Path):
        assert isinstance(path, Path)
        assert path.exists(), "file not found: {}".format(str(path))
        assert is_zipfile(str(path)), "not a zipfile: {}".format(str(path))
        self.path = path
        self.images = {}

        zf = ZipFile(str(path))
        with TmpDir() as tmpdir:
            zf.extractall(tmpdir)
            with open(f"{tmpdir}/manifest.json", 'r') as f:
                self.manifest = json.load(f)['manifest']

            for target, data in self.manifest.items():
                data['img_type'] = target
                assert "bin_file" in data
                assert "dat_file" in data

                # rename items in package to something less confusing
                data['img_file'] = data['bin_file']
                data['init_file'] = data['dat_file']
                del data['bin_file']
                del data['dat_file']

                with open(f"{tmpdir}/{data['img_file']}", 'rb') as img_file:
                    data["img_data"] = img_file.read()
                
                with open(f"{tmpdir}/{data['init_file']}", 'rb') as init_file:
                    data["init_data"] = init_file.read()
                    data["init_pkt"] = Packet()
                    data["init_pkt"].ParseFromString(data["init_data"])

                self.images[target] = DFUImage(**data)

    def get_fw_data(self, fwtype: [int, str]) -> DFUImage:
        if isinstance(fwtype, int):
            fwtype = FwType.Name(fwtype)
        assert isinstance(fwtype, str), "invalid fwtype param type: {}".format(type(fwtype))
        fwtype = fwtype.upper()
        assert fwtype in FwType.keys(), "invalid fwtype: {}".format(fwtype)
        fwtype = fwtype.lower()
        assert fwtype in self.images, "package missing fwtype: {}".format(fwtype)
        return self.images[fwtype]

    @property
    def has_bl(self):
        return "bootloader" in self.images

    @property
    def has_sd(self):
        return "softdevice" in self.images

    @property
    def has_app(self):
        return "app" in self._images

    def get_init_packet(self, fwtype: [int, str]) -> Packet:
        return self.get_fw_data(fwtype).init_pkt

    def get_fw_bin(self, fwtype: [int, str]) -> bytes:
        return self.get_fw_data(fwtype).img_data

    def gen_fw_hash(self, fwtype: [int, str]) -> [bytes, None]:
        fw_data = self.get_fw_data(fwtype)
        packet = fw_data.init_pkt
        fw_bin = fw_data.img_data
        assert len(packet.ListFields()), "init packet missing fields!"
        if packet.HasField("signed_command"):
            command = packet.signed_command.command
        else:
            command = packet.command

        assert command.HasField("init"), "not an InitCommand!"

        hashtype = command.init.hash.hashimg_type

        match hashtype:
            case HashType.NO_HASH:
                return None
            case HashType.CRC:
                return crc32(fw_bin).to_bytes(4, 'little')
            case HashType.SHA128:
                assert False, (
                    "SHA128 is not a real hash function, "
                    "the protobuf definition is wrong.")
            case HashType.SHA256:
                h = sha256()
                h.update(fw_bin)
                return bytes(reversed(h.digest()))
            case HashType.SHA512:
                h = sha512()
                h.update(fw_bin)
                return bytes(reversed(h.digest()))
            case _:
                assert False, "unreachable, invalid hash type: {}".format(hashtype)