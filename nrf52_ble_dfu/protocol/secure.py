import os
import asyncio
import logging
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any
from logging import Logger
from enum import Enum
from asyncio import Queue, QueueEmpty
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

from ..protocol import (
    Notification,
    DFU_CTRL_POINT_UUID,
    DFU_PACKET_UUID,
)

from ..models import (
    TxStatus,
    TxStateHandler,
    BaseDFUManager,
)

from ..models.package import (
    DFUImage,
    DFUPackage,
)

from ..models.secure import (
    SecureDFUOpcode as Opcode,
    SecureDFUExtendedErrorCode as ExtendedErrorCode,
    SecureDFUProcedureType as ProcedureType,
    SecureDFUImageType as ImageType,
    SecureDFUResultCode as ResultCode,
    SecureDFURequest as Request,
    SecureDFUResponse as Response,
    SecureDFUPacketReceiptNotification as PRN,
)

def check_response(res: Response):
    """a helper function to check responses and raise errors as needed"""
    if not res:
        raise DFUErrorCode.DEVICE_DISCONNECTED.as_err()
    elif not res.ok():
        raise res.status.error().as_err()

def check_status(state: TxStatus):
    """a helper function to check handler status codes"""
    # not yet implemented
    pass


class SecureDFUClient(BleakClient):
    """a wrapper class for the dfu bleak client
    automatically sets up notifications
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def write_ctl(self, request: Request, response=True):
        """send request to control point characteristic"""
        self.log.info(f"REQUEST: {request.description}")
        await self.write_gatt_char(DFU_CTRL_POINT_UUID, request.data, response=response)

    async def write_pkt(self, pkt_data: bytearray):
        """send data to packet characteristic"""
        assert len(data) <= 20, "gatt packet data must be <= 20 bytes"
        self.log.debug(f"Sending GATT packet: [ {hexlify(data, ' ')} ]")
        await self.write_gatt_char(DFU_PACKET_UUID, data, response=False)


class SecureDFUContext:
    __slots__ = [
        "state",        # SecureTxState current state
        "prev_state",   # SecureTxState previous state
        "status",       # TxStatus      state handler status
        "name",         # str           name of target to update
        "target",       # BLEDevice     target to update
        "pkg",          # DFUPackage    dfu update package
        "log",          # Logger        logger
        "timeout",      # int           default ble connection timeout
        "img_queue",    # list[str]     list of images to send
        "client",       # BleakClient   bleak client object
        "responses",    # Queue         client notification queue

        "img",          # DFUImage      current image being transferred
        "txdata",       # bytearray     remaining image data to be tranferred
        "obj_type"      # ProcedureType current object type
        "object",       # bytearray     remaining object data to be transferred
        "pkt",          # bytearray     current raw data packet being transferred
        "prn",          # int           current packet receipt notification number
        "local_crc",    # int           current object crc on controller
        "bytes_sent",   # int           txdata bytes sent
        "attempts",     # int           number of attempts to send current object

        "max_size",     # int           maximum object size in bytes
        "offset",       # int           current object offset on target
        "target_crc",   # int           current object crc on target
    ]

    def __init__(self,
        name: str,
        pkg: DFUPackage,
        log: Logger = None,
        timeout: int = None,
    ):
        self.name       = name
        self.pkg        = pkg
        self.log        = log if log else logging.getLogger()
        self.timeout    = timeout
        self.img_queue  = [
            img for img in ['bootloader', 'softdevice', 'application'] \
            if img in self.pkg.images
        ]

        self.local_crc  = 0
        self.prn        = 0
        
        self.client     = None
        self.responses  = Queue()

        self.prev_state = None
        self.state      = SecureTxState.DISCONNECTED


    def transition(self, new_state: SecureTxState) -> TxStatus:
        """helper to transition states"""
        self.prev_state = self.state
        self.state = new_state
        return TxStatus.TRANSITIONED


    def get_response(self) -> Response | None:
        """get the last DFUResponse from the client if there was one.
        if there have been no responses, returns None
        """
        try:
            notification = self.responses.get_nowait()
        except QueueEmpty as e:
            return None
        
        response = Response(notification.data)
        self.log.info(f"{notification.sender.description} {notification.time}: {response.description}")
        return response

    def get_prn(self) -> Response | None:
        """get the last response from client as a PRN.
        return None if there were no responses
        """
        try:
            notification = self.responses.get_nowait()
        except QueueEmpty as e:
            return None

        prn_response = PRN(notification.data)
        assert prn_response.req_opcode == Opcode.GET_CRC
        self.log.info(f"{notification.sender.description} {notification.time}: {prn_response.description}")
        return prn_response

    async def set_prn_value(self, value) -> Response | None:
        """send RECEIPT_NOTIF_SET request
        unlike the other requests, we process this command within this
        function because it is called so often. state handlers calling
        this function must handle the response immediately
        """
        self.prn = value
        await self.client.write_ctl(
            Request(Opcode.RECEIPT_NOTIF_SET, prn_value=value))

        return self.get_response()

    async def clear_prn_value(self, force=False):
        """send RECEIPT_NOTIF_SET request
        to set the PRN value to 0. will raise errors if unsuccessful.
        """
        if not force and self.prn == 0:
            return
        
        res = await self.set_prn_value(0)
        check_response(res)

    async def object_select(self, object_type: ProcedureType):
        """send OBJECT SELECT request"""
        assert self.prn == 0
        return await self.client.write_ctl(
            Request(Opcode.OBJECT_SELECT, object_type=object_type))

    async def object_create(self, object_type, object_size):
        """send OBJECT CREATE request"""
        assert self.prn == 0
        return await self.client.write_ctl(
            Request(Opcode.OBJECT_CREATE, object_type=object_type, object_size=object_size))

    async def object_execute(self):
        """send OBJECT EXECUTE request"""
        assert self.prn == 0
        return await self.client.write_ctl(Request(Opcode.OBJECT_EXECUTE))

    async def abort(self):
        """send ABORT request"""
        return await self.client.write_ctl(Request(Opcode.ABORT))

    async def get_crc(self):
        """send GET_CRC request"""
        return await self.client.write_ctl(Request(Opcode.GET_CRC))


class SecureTxState(TxStateHandler, Enum):
    DISCONNECTED            = DisconnectedStateHandler
    CONNECTING              = ConnectingStateHandler
    TRANSFER_READY          = TransferReadyStateHandler
    PREPARING_DATA_OBJECT   = PreparingDataObjectStateHandler
    SELECT_OBJECT           = SelectObjectStateHandler
    CREATE_OBJECT           = CreateObjectStateHandler
    TRANSFERRING_OBJECT     = TransferringObjectStateHandler
    VALIDATE_OBJECT         = ValidateObjectStateHandler
    EXECUTE_OBJECT          = ExecuteObjectStateHandler
    TRANSFER_DONE           = TransferDoneStateHandler


class SecureDFUManager(BaseDFUManager):
    """
    a state-based dfu update manager

    not truly event-driven, but can probably adapted to be
    """
    def __init__(self, name: str, pkg: DFUPackage, log: Logger = None, timeout: int=None):
        self.context = SecureDFUContext(name, pkg, log, timeout)

        assert len(pkg.images), "pkg must contain at least 1 image"
        assert not (
            "bootloader" in pkg.images 
            and "application" in pkg.images
            and "softdevice" not in images
        ), "cannot perform DFU with bootloader + application"

    async def run(self):
        """do dfu update"""
        status = TxStatus.INIT

        # would normally get next event from event queue here, but the async model
        # doesn't really have events...
        # instead we'll just loop on the last called handler status.

        while status != TxStatus.COMPLETE:
            match status:
                case TxStatus.ERROR:
                    assert_never("error handling not implemented")
                case TxStatus.INIT:
                case TxStatus.HANDLED:
                case TxStatus.IGNORED:
                    status = await self.state.handle(self.context)
                case TxStatus.TRANSITIONED:
                    check_status(self.prev_state.exit(self.context))
                    status = await self.state.entry(self.context)
                case TxStatus.COMPLETE:
                    assert_never("unreachable")

        self.context.log.info("DFU completed")


############################################################
# DFU STATE MACHINE HANDLERS
############################################################


class DisconnectedStateHandler(TxStateHandler):
    @classmethod
    async def handle(cls, context) -> TxStatus:
        """attempt to locate target device"""
        context.target = await BleakScanner.find_device_by_name(context.name)
        if not context.target:
            context.log.error(f"FAILURE: could not find target {target_name}")
            # raise DFUErrorCode.FAILED_TO_CONNECT.as_err()
            return TxStatus.HANDLED
        
        return context.transition(SecureTxState.CONNECTING)


class ConnectingStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        """on entry, attempt to connect to the target device"""
        context.client = await BleakClient(mgr.target.address).connect()
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        """handle connection attempt
        
        on successful connection, start notifications and continue to img transfer 
        """
        async def response_callback(sender: BleakGATTCharacteristic, data: bytearray):
            """a helper function to forward notifications to the manager context"""
            t = time.time()
            await context.responses.put(
                Notification(sender=sender, time=t, data=data))


        if not context.client.is_connected:
            await context.client.start_notify(DFU_CTRL_POINT_UUID, response_callback)
            return context.transition(SecureTxState.TRANSFER_READY)
        else:
            return context.transition(SecureTxState.DISCONNECTED)


class TransferReadyStateHandler(TxStateHandler):
    @classmethod
    async def handle(cls, context):
        assert context.pkg.images, "package contains no images to send!"
        
        if not context.img_queue:
            context.log.info("all images sent!")
            return context.transition(SecureTxState.TRANSFER_DONE)

        img_type = context.img_queue[0]

        # prepare to send image
        context.bytes_sent = 0
        context.local_crc = 0
        context.img = context.pkg.images[img_type]
        context.obj_type = ProcedureType.COMMAND
        context.txdata = bytearray(context.img.init_data)
        context.attempts = 0

        return context.transition(SecureTxState.SELECT_OBJECT)


class PreparingDataObjectStateHandler(TxStateHandler):
    @classmethod
    async def handle(cls, context):
        """prepare the next data object from txdata to send"""

        context.bytes_sent = 0
        context.local_crc = 9
        context.txdata = bytearray(context.img.img_data)
        context.attempts = 0

        return context.transition(SecureTxState)



class SelectObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        # always set prn to 0
        await context.clear_prn_value(force=True)

        await context.object_select(context.obj_type)
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        res = context.get_response()
        check_response(res)

        context.max_size = res.max_size
        context.offset = res.offset
        context.target_crc = res.crc

        if (context.offset == len(context.txdata)
            and context.target_crc == crc32(context.txdata)
        ):
            # if the init packet has already been successfully sent, 
            # skip to execute
            context.object.clear()
            return context.transition(SecureTxState.EXECUTE_OBJECT)
        elif (context.offset != 0
            and context.offset <= len(context.txdata)
            and context.target_crc == crc32(context.txdata[context.offset])
        ):
            # if init packet partially sent without error, resume sending data
            context.object = context.object[context.offset:]
            return context.transition(SecureTxState.TRANSFERRING_OBJECT)
        else:
            return context.transition(SecureTxState.CREATE_COMMAND_OBJECT)

class CreateObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        # reset prn to 0 if necessary
        await context.clear_prn_value()
        assert len(context.object) <= context.max_size, \
            "for now, assume the init packet is always smaller than the max object size"

        context.object = context.txdata[:context.max_size]

        await context.object_create(context.obj_type, len(context.object))
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        res = context.get_response()
        check_response(res)

        return context.transition(SecureTxState.TRANSFERRING_OBJECT)

class TransferringObjectStateHandler(TxStateHandler):
    DEFAULT_PRN = 10
    GATT_PKT_SIZE = 20

    # state-specific class variables
    object_data = None
    total_pkts = None
    pkts_sent = None

    @classmethod
    async def entry(cls, context) -> TxStatus:
        """before transfer, set PRN value and reset class variables"""
        # set prn to default
        res = await context.set_prn_value(DEFAULT_PRN)
        check_response(res)

        # calculate total packets in the object being sent
        total_pkts = (len(context.object) + GATT_PKT_SIZE - 1) // GATT_PKT_SIZE
        object_data = bytearray(context.object)
        pkts_sent = 0

        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        """send the next object packet"""

        if pkts_sent >= total_pkts:
            # finished sending all packets, go to validation
            return context.transition(SecureTxState.VALIDATE_OBJECT)

        remaining_pkts = total_pkts - pkts_sent
        if not (pkts_sent % DEFAULT_PRN) and remaining_pkts < context.prn:
            # fewer packets left to send than the prn, update it now
            res = await context.set_prn_value(remaining_pkts)
            check_response(res)

        # prepare the next packet
        context.pkt = object_data[:GATT_PKT_SIZE]
        object_data = object_data[GATT_PKT_SIZE:]
        context.local_crc = crc32(context.pkt, context.local_crc)
        
        # send the next packet
        self.log.debug(f"sending pkt: [ {hexlify(context.pkt, ' ')} ]")
        await context.client.write_pkt(context.pkt)
        pkts_sent += 1
        context.bytes_sent += len(context.pkt)


        if pkts_sent % context.prn:
            # no PRN expected
            return TxStatus.HANDLED
            
        # expecting a notification
        res = context.get_prn()
        check_response(res)

        # validate data so far
        if res.offset != context.bytes_sent:
            self.log.error(f"offset mismatch! expected: {context.bytes_sent:#x}, got: {res.offset:#x}")
            raise DFUErrorCode.BYTES_LOST.as_err()
        elif res.crc != context.local_crc:
            self.log.error(f"crc mismatch! expected: {crc:#x}, got: {prn_res.crc:#x}")
            # raise DFUErrorCode.CRC_ERROR.as_err()
            return context.transition(SecureTxState.VALIDATE_OBJECT)

        return TxStatus.HANDLED

    @classmethod
    def exit(cls, context):
        """always increment number of attempts to send object on exit"""
        context.attempts += 1

class ValidateObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        """on entry, send GET_CRC request"""
        await context.clear_prn_value()

        await context.get_crc()
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        """validate GET_CRC response"""
        res = context.get_response()
        assert res.req_opcode == Opcode.GET_CRC
        check_response(res)

        if res.crc != context.local_crc:
            if context.attempts >= 3:
                raise DFUErrorCode.CRC_ERROR.as_err()
            self.log.info(f"object CRC mismatch! trying again... (attempts: {context.attempts})")
            return context.transition(SecureTxState.CREATE_OBJECT)

        self.log.info("object CRC matched, continuing to EXECUTE")
        return context.transition(SecureTxState.EXECUTE_OBJECT)

class ExecuteObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        """on entry, send EXECUTE request"""
        await context.object_execute()
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        """handle EXECUTE response"""
        res = context.get_response()
        assert res.req_opcode == Opcode.EXECUTE_OBJECT
        check_response(res)

        if context.obj_type == ProcedureType.COMMAND:
            # init command should be transferred, continue to send image data
            return context.transition(SecureTxState.PREPARING_DATA_OBJECT)
        elif context.bytes_sent < len(context.txdata):
            # image transfer incomplete, send next data object
            context.txdata = context.txdata[len(context.object):]
            return context.transition(SecureTxState.CREATE_OBJECT)
        elif context.bytes_sent == len(context.txdata):
            # image transferred
            # remove this image from queue
            context.img_queue.pop(0)
            # the bootloader should automatically reset the device,
            # so we should expect a disconnect
            return context.transition(SecureTxState.DISCONNECTED)

        assert_never("unreachable")

class TransferDoneStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        """when transfer done, disconnect from client """
        await context.client.disconnect()
        return TxStatus.COMPLETE

