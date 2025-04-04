import os
import asyncio
import logging
import time
import traceback
from logging import Logger
from enum import Enum
from asyncio import Queue
from collections import namedtuple
from zlib import crc32
from binascii import hexlify

import bleak
from bleak import BleakScanner, BleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTCharacteristic

from ..error import (
    DFUError,
    DFUErrorCode ,
    DFURemoteErrorCode,
)

from ..package import (
    DFUImage,
    DFUPackage,
)

from ..remote import (
    Notification,
    DFU_CTRL_POINT_UUID,
    DFU_PACKET_UUID,
)
from ..protocol.secure import (
    SecureDFUOpcode as Opcode,
    SecureDFUExtendedErrorCode as ExtendedErrorCode,
    SecureDFUProcedureType as ProcedureType,
    SecureDFUImageType as ImageType,
    SecureDFUResultCode as ResultCode,
    SecureDFURequest as Request,
    SecureDFUResponse as Response,
    SecureDFUPacketReceiptNotification as PRN,
)

class SecureDFUClient(BleakClient):
    """a wrapper class for the dfu bleak client
    automatically sets up notifications
    """

    def __init__(self, target: BLEDevice, log: Logger=None, pkt_size=20, prn=0):
        self.log = log if log else logging.getLogger("SecureDFUClient")
        self.res_q = Queue()
        self.pkt_size = pkt_size
        self.prn = prn
        self.target = target
        super().__init__(target.address,
            disconnected_callback=None,
            timeout=None,
        )

    async def __aenter__(self):
        await self.connect()
        async def callback(sender: BleakGATTCharacteristic, data: bytearray):
            t = time.time()
            await self.res_q.put(
                Notification(sender=sender, time=t, data=data))
        await self.start_notify(DFU_CTRL_POINT_UUID, callback)
        return self

    async def __aexit__(self, *args):
        # await self.stop_notify(DFU_CTRL_POINT_UUID)
        await super().__aexit__(*args)

    async def read_response(self) -> Response:
        notification = await self.res_q.get()
        response = Response(notification.data)
        self.log.info(f"{notification.sender.description} {notification.time}: {response.description}")
        return response

    async def write_ctl(self, request: Request) -> (DFUErrorCode, Response):
        self.log.info(f"REQUEST: {request.description}")
        await self.write_gatt_char(DFU_CTRL_POINT_UUID, request.data, response=True)
        try:
            res = await self.read_response()
            return (res.status.error(), res)
        except AssertionError as e:
            tb = traceback.format_exception(e)
            self.log.error(f"{str(sender)} - {t} | {tb}")
            return (DFUErrorCode.WRITING_CHARACTERISITIC_FAILED, None)


    async def set_prn_value(self, value) -> (DFUErrorCode, Response):
        self.prn = value
        return await self.write_ctl(
            Request(Opcode.RECEIPT_NOTIF_SET, prn_value=value))

    async def object_select(self, object_type) -> (DFUErrorCode, Response):
        if self.prn != 0:
            err_code, res = await self.set_prn_value(0)
            if not err_code.ok():
                return err_code, res

        return await self.write_ctl(
            Request(Opcode.OBJECT_SELECT, object_type=object_type))

    async def object_create(self, object_type, object_size) -> (DFUErrorCode, Response):
        if self.prn != 0:
            err_code, res = await self.set_prn_value(0)
            if not err_code.ok():
                return err_code, res

        return await self.write_ctl(
            Request(
                Opcode.OBJECT_CREATE,
                object_type=object_type,
                object_size=object_size))

    async def crc_get(self) -> (DFUErrorCode, Response):
        if self.prn != 0:
            err_code, res = await self.set_prn_value(0)
            if not err_code.ok():
                return err_code, res

        return await self.write_ctl(Request(Opcode.CRC_GET))

    async def execute(self) -> (DFUErrorCode, Response):
        if self.prn != 0:
            err_code, res = await self.set_prn_value(0)
            if not err_code.ok():
                return err_code, res

        return await self.write_ctl(Request(Opcode.OBJECT_EXECUTE))

    async def abort(self):
        request = Request(Opcode.ABORT)
        await self.write_gatt_char(DFU_CTRL_POINT_UUID, request.data, response=False)

    async def write_pkt(self, data: bytearray, prn=10, crc=0, offset=0) -> (float, DFUErrorCode):
        total_pkts = (len(data) + self.pkt_size - 1) // self.pkt_size
        pkts_sent = 0

        # update the PRN number if needed
        if self.prn != prn:
            err_code, res = await self.set_prn_value(prn)
            if not res.ok():
                self.log.error("failed to set prn value!")
                self.log.error(f"{err_code.as_err().message}")
                return (0.0, err_code)

        # begin sending data in packet groups
        progress = pkts_sent / total_pkts
        bytes_sent = 0
        while data:
            # if fewer packets need to be sent than PRN number,
            # change the PRN to receive a notification on completion
            pkt_group_size = min(prn, total_pkts - pkts_sent)
            if pkt_group_size != self.prn:
                err_code, res = await self.set_prn_value(pkt_group_size)
                if not res.ok():
                    self.log.error("failed to set prn value!")
                    self.log.error(f"{err_code.as_err().message}")
                    return (progress, err_code)

            # send a group of packets
            while pkt_group_size:
                pkt, data = data[:self.pkt_size], data[self.pkt_size:]
                crc = crc32(pkt, crc)
                self.log.debug(f"sending pkt: [ {hexlify(pkt, ' ')} ]")
                await self.write_gatt_char(DFU_PACKET_UUID, pkt, response=False)
                bytes_sent += len(pkt)
                pkts_sent += 1
                pkt_group_size -= 1

            res = await self.res_q.get()
            prn_res = PRN(res.data)
            self.log.info(f"{res.sender.description} {res.time}: {prn_res.description}")

            progress = pkts_sent / total_pkts
            self.log.info(f"progress: {progress*100:>6.2f}%")

            assert prn_res.status == ResultCode.SUCCESS, \
                "PRN response should always be success"

            # check sent data matches
            if prn_res.offset != bytes_sent + offset:
                self.log.error(f"offset mismatch! expected: {bytes_sent:#x}, got: {prn_res.offset:#x}")
                err = DFUErrorCode.WRITING_CHARACTERISITIC_FAILED
                self.log.error(f"{err.as_err().message}")
                return (progress, err)
            elif prn_res.crc != crc:
                self.log.error(f"crc mismatch! expected: {crc:#x}, got: {prn_res.crc:#x}")
                err = DFUErrorCode.CRC_ERROR
                self.log.error(f"{err.as_err().message}")
                return (progress, err)

        assert pkts_sent == total_pkts, "failed to send all packets"

        self.log.debug("finished sending data to packet characteristic")
        return (progress, DFUErrorCode.REMOTE_SECURE_DFU_SUCCESS.as_err())


class SecureTxState(int, Enum):
    BEGINNING_TX            = 0
    SELECTING_OBJECT        = 1
    CREATING_OBJECT         = 2
    TRANSMITTING_RAW_DATA   = 3
    VALIDATING_RAW_DATA     = 4
    EXECUTING               = 5
    TX_COMPLETE             = 6


class SecureTxManager:
    def __init__(self, proc_type: ProcedureType, log: Logger=None):
        self.state = SecureTxState.BEGINNING_TX
        self.proc_type = proc_type
        self.log = log if log else logging.getLogger("TxManager")

    async def transfer(self, client: SecureDFUClient, object_data: bytearray):
        match self.proc_type:
            case ProcedureType.COMMAND: return await self._cmd_transfer(client, object_data)
            case ProcedureType.DATA:    return await self._dat_transfer(client, object_data)
            case _:                     assert_never(self.proc_type.name)


    async def _cmd_transfer(self, client: SecureDFUClient, object_data: bytearray):
        object_crc = crc32(object_data)
        err_code = None
        res = None
        retries = 0
        max_size = None
        crc = 0

        # begin state machine
        while self.state < 6:
            match self.state:
                case SecureTxState.BEGINNING_TX:
                    # before beginning transmission, set PRN to 0
                    self.log.info(f"beginning {self.proc_type.name} transfer...")
                    err_code, res = await client.set_prn_value(0)
                    if not err_code.ok():
                        break

                    self.state = SecureTxState.SELECTING_OBJECT

                case SecureTxState.SELECTING_OBJECT:
                    # send select command
                    self.log.info(f"{self.state.name} {self.proc_type.name}")
                    err_code, res = await client.object_select(object_type=self.proc_type)
                    if not err_code.ok():
                        break

                    if (res.offset == len(object_data)
                        and res.crc == object_crc
                    ):
                        # if the object has already been sent, execute
                        self.log.info("object has already been sent, skipping to EXECUTE")
                        self.state = SecureTxState.EXECUTING
                    
                    elif (res.offset > 0
                        and res.offset <= len(object_data)
                        and res.crc == crc32(object_data[:res.offset])
                    ):
                        # if the object has been created, but is incomplete,
                        # continue sending data from offset
                        self.log.info("object is created, but incomplete, continuing data transmission")
                        object_data = object_data[res.offset:]
                        self.state = SecureTxState.TRANSMITTING_RAW_DATA

                    else:
                        # if the object has not yet been created, create it
                        self.log.info("no object created, continuing to object creation")
                        self.state = SecureTxState.CREATING_OBJECT

                case SecureTxState.CREATING_OBJECT:
                    # send create command
                    self.log.info(f"{self.state.name} {self.proc_type.name}")
                    err_code, res = await client.object_create(
                        object_type=self.proc_type,
                        object_size=len(object_data))
                    if not err_code.ok():
                        break

                    # if command object successfully created, transmit raw data
                    self.log.info("object successfully created, transmitting raw data")
                    self.state = SecureTxState.TRANSMITTING_RAW_DATA

                case SecureTxState.TRANSMITTING_RAW_DATA:
                    # transmit all object data
                    self.log.info(f"{self.state.name}: {self.proc_type.name}")
                    progress, err_code = await client.write_pkt(object_data)
                    if progress != 1.0 or not err_code.ok():
                        break

                    # if all object data successfully transmitted,
                    # validate data object
                    self.log.info("object successfully transmitted, continuing to validation")
                    self.state = SecureTxState.VALIDATING_RAW_DATA

                case SecureTxState.VALIDATING_RAW_DATA:
                    # request current object data crc
                    self.log.info(f"{self.state.name}: CRC_GET")
                    err_code, res = await client.crc_get()
                    if not err_code.ok():
                        break

                    if res.crc != object_crc:
                        # if check failed, try sending command again up to 3 times
                        if retries >= 3:
                            err_code = DFUErrorCode.CRC_ERROR
                            break

                        retries += 1
                        self.log.info(f"CRC mismatch! trying again... (attempts: {retries})")
                        self.state = SecureTxState.CREATING_OBJECT
                    else:
                        # if check successful, send execute request
                        self.log.info("CRC matched, continuing to EXECUTE")
                        self.state = SecureTxState.EXECUTING

                case SecureTxState.EXECUTING:
                    # send execute request
                    self.log.info(f"{self.state.name} {self.proc_type.name} object")
                    err_code, res = await client.execute()
                    if not err_code.ok():
                        break

                    # on success, transfer complete
                    self.log.info("EXECUTE request successful, transmission complete.")
                    self.state = SecureTxState.TX_COMPLETE

                case _:
                    assert_never("TX_ERROR and TX_COMPLETE should always terminate loop")
        
        if self.state != SecureTxState.TX_COMPLETE:
            self.log.error(f"{self.state.name}: Error occurred, transfer incomplete!")
            raise err_code.as_err()

        # transfer is complete

    async def _dat_transfer(self, client: SecureDFUClient, object_data: bytearray):
        object_crc = crc32(object_data)
        crc = 0
        err_code = None
        res = None
        retries = 0
        max_size = None
        data_chunk = bytearray()
        total_bytes_sent = 0

        # begin state machine
        while self.state < 6:
            match self.state:
                case SecureTxState.BEGINNING_TX:
                    # before beginning transmission, set PRN to 0
                    self.log.info(f"beginning {self.proc_type.name} transfer...")
                    err_code, res = await client.set_prn_value(0)
                    if not err_code.ok():
                        break

                    self.state = SecureTxState.SELECTING_OBJECT

                case SecureTxState.SELECTING_OBJECT:
                    # send select command
                    self.log.info(f"{self.state.name} {self.proc_type.name}")
                    err_code, res = await client.object_select(object_type=self.proc_type)
                    if not err_code.ok():
                        break

                    max_size = res.max_size

                    if (res.offset == len(data_chunk)
                        and res.crc == object_crc
                    ):
                        # if the object has already been sent, execute
                        self.log.info("object has already been sent, skipping to EXECUTE")
                        self.state = SecureTxState.EXECUTING
                    
                    elif (res.offset > 0
                        and res.offset <= len(data_chunk)
                        and res.crc == (crc32(object_data[:res.offset]))
                    ):
                        # if the object has been created, but is incomplete,
                        # continue sending data from offset
                        self.log.info("object is created, but incomplete, continuing data transmission")
                        data_chunk = data_chunk[res.offset:]
                        self.state = SecureTxState.TRANSMITTING_RAW_DATA

                    else:
                        # if the object has not yet been created, create it
                        self.log.info("no object created, continuing to object creation")
                        self.state = SecureTxState.CREATING_OBJECT

                case SecureTxState.CREATING_OBJECT:
                    assert len(object_data) > 0, "no object data to send"

                    # create the next chunk of data from object data
                    data_chunk = object_data[:max_size]
                    object_data = object_data[max_size:]

                    # crc = 0

                    # send create command
                    self.log.info(f"{self.state.name} {self.proc_type.name}")
                    err_code, res = await client.object_create(
                        object_type=self.proc_type,
                        object_size=len(data_chunk))
                    if not err_code.ok():
                        break

                    # if command object successfully created, transmit raw data
                    self.log.info("object successfully created, transmitting raw data")
                    self.state = SecureTxState.TRANSMITTING_RAW_DATA

                case SecureTxState.TRANSMITTING_RAW_DATA:
                    # transmit all object data
                    self.log.info(f"{self.state.name}: {self.proc_type.name}")
                    progress, err_code = await client.write_pkt(data_chunk, crc=crc, offset=total_bytes_sent)
                    if progress != 1.0 or not err_code.ok():
                        break

                    crc = crc32(data_chunk, crc)

                    # if all object data successfully transmitted,
                    # validate data object
                    self.log.info("object successfully transmitted, continuing to validation")
                    self.state = SecureTxState.VALIDATING_RAW_DATA

                case SecureTxState.VALIDATING_RAW_DATA:
                    # request current object data crc
                    self.log.info(f"{self.state.name}: CRC_GET")
                    err_code, res = await client.crc_get()
                    if not err_code.ok():
                        break

                    if res.crc != crc:
                        # if check failed, try sending command again up to 3 times
                        if retries >= 3:
                            err_code = DFUErrorCode.CRC_ERROR
                            break

                        retries += 1
                        self.log.info(f"CRC mismatch! trying again... (attempts: {retries})")
                        self.state = SecureTxState.CREATING_OBJECT
                    else:
                        # if check successful, send execute request
                        total_bytes_sent += len(data_chunk)
                        self.log.info("CRC matched, continuing to EXECUTE")
                        self.state = SecureTxState.EXECUTING

                case SecureTxState.EXECUTING:
                    # send execute request
                    self.log.info(f"{self.state.name} {self.proc_type.name} object")
                    err_code, res = await client.execute()
                    if not err_code.ok():
                        break

                    if len(object_data) > 0:
                        self.log.info("EXECUTE request successful, sending next data chunk")
                        self.state = SecureTxState.CREATING_OBJECT
                    else:
                        # all object data sent, send execute again and end transmission
                        self.log.info("EXECUTE request successful, transmission complete.")
                        self.state = SecureTxState.TX_COMPLETE

                case _:
                    assert_never("TX_ERROR and TX_COMPLETE should always terminate loop")
        
        if self.state != SecureTxState.TX_COMPLETE:
            self.log.error(f"{self.state.name}: Error occurred, transfer incomplete!")
            raise err_code.as_err()

        # transfer is complete


async def transfer_image(client: SecureDFUClient, image: DFUImage, log: Logger = None) -> DFUErrorCode:
    if not log:
        log = logging.getLogger()

    log.info(f"transferring {image.img_type} image: {image.bin_file}")
    cmd_txmgr = SecureTxManager(ProcedureType.COMMAND, log=log)
    try:
        await cmd_txmgr.transfer(client, image.dat_data)
    except Exception as e:
        log.error(f"INIT transfer failed. aborting firmware update...")
        await client.abort()
        if isinstance(e, DFUError):
            return DFUErrorCode(e)
        raise e

    dat_txmgr = SecureTxManager(ProcedureType.DATA, log=log)
    try:
        await dat_txmgr.transfer(client, image.bin_data)
    except Exception as e:
        log.error(f"DATA transfer failed. aborting firmware update...")
        await client.abort()
        if isinstance(e, DFUError):
            return DFUErrorCode(e)
        raise e

    return DFUErrorCode.REMOTE_SECURE_DFU_SUCCESS

async def dfu(target_name: str, pkg: DFUPackage, log: Logger = None):
    if not log:
        log = logging.getLogger()

    if len(pkg.manifest) > 1:
        # there are 2 updates contained
        # updates should be applied by going through the full 
        # update process in succession for application and 
        # softdevice update.
        # softdevice and bootloader might be able to be applied
        # simultaneously based on the sdk implementation, but I need
        # to double check to be sure
        # there should be at most 2 updates contained (i think)
        assert_never("todo")

    log.info(f"connecting to client...")
    target = await BleakScanner.find_device_by_name(target_name)
    if not target:
        log.error(f"FAILURE: could not find target {target_name}")
        raise DFUErrorCode.FAILED_TO_CONNECT.as_err()
    async with SecureDFUClient(target, log=log) as client:
        for image_type in ["bootloader", "softdevice", "application"]:
            log.info(f"starting {image_type} transfer...")
            if image_type not in pkg.manifest:
                continue

            err_code = await transfer_image(client, pkg.objects[image_type], log=log)
            if not err_code.ok():
                log.error(f"FAILURE: {err_code.as_err()}")
                raise err_code.as_err()


