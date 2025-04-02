import json
from collections import namedtuple
from pathlib import Path

from zipfile import ZipFile, is_zipfile
from tempfile import TemporaryDirectory as TmpDir
from hashlib import sha256, sha512
from zlib import crc32

from .dfu_cc_pb2 import *

DFUImage = namedtuple("DFUImage", [
    'img_type',
    'bin_file',
    'dat_file',
    'bin_data',
    'dat_data',
    'dat_pkt',
])

class DFUPackage:
    def __init__(self, path: Path):
        assert isinstance(path, Path)
        assert path.exists(), "file not found: {}".format(str(path))
        assert is_zipfile(str(path)), "not a zipfile: {}".format(str(path))
        self.path = path
        self.objects = {}

        zf = ZipFile(str(path))
        with TmpDir() as tmpdir:
            zf.extractall(tmpdir)
            with open(f"{tmpdir}/manifest.json", 'r') as f:
                self.manifest = json.load(f)['manifest']

            for target, data in self.manifest.items():
                data['img_type'] = target

                assert "bin_file" in data
                with open(f"{tmpdir}/{data['bin_file']}", 'rb') as bin_file:
                    data["bin_data"] = bin_file.read()

                assert "dat_file" in data
                with open(f"{tmpdir}/{data['dat_file']}", 'rb') as dat_file:
                    data["dat_data"] = dat_file.read()
                    data["dat_pkt"] = Packet()
                    data["dat_pkt"].ParseFromString(data["dat_data"])

                self.objects[target] = DFUImage(**data)

    def get_fw_data(self, fwtype: [int, str]) -> DFUImage:
        if isinstance(fwtype, int):
            fwtype = FwType.Name(fwtype)
        assert isinstance(fwtype, str), "invalid fwtype param type: {}".format(type(fwtype))
        assert fwtype in FwType.keys(), "invalid fwtype: {}".format(fwtype)
        fwtype = fwtype.lower()
        assert fwtype in self.objects, "package missing fwtype: {}".format(fwtype)
        return self.objects[fwtype]

    def get_init_packet(self, fwtype: [int, str]) -> Packet:
        return self.get_fw_data(fwtype).dat_pkt

    def get_fw_bin(self, fwtype: [int, str]) -> bytes:
        return self.get_fw_data(fwtype).bin_data

    def gen_fw_hash(self, fwtype: [int, str]) -> [bytes, None]:
        fw_data = self.get_fw_data(fwtype)
        packet = fw_data.dat_pkt
        fw_bin = fw_data.bin_data
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