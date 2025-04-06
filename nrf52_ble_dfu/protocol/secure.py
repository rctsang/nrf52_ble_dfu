import os
import asyncio
import logging
import time
import traceback
from abc import ABC, abstractmethod
from typing import Any, assert_never
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
        # log.info(f"REQUEST: {request.description}")
        await self.write_gatt_char(DFU_CTRL_POINT_UUID, request.data, response=response)

    async def write_pkt(self, pkt_data: bytearray):
        """send data to packet characteristic"""
        assert len(pkt_data) <= 20, "gatt packet data must be <= 20 bytes"
        # log.debug(f"Sending GATT packet: [ {hexlify(pkt_data, ' ')} ]")
        await self.write_gatt_char(DFU_PACKET_UUID, pkt_data, response=False)


class SecureTxState(Enum):
    DISCONNECTED            = 0
    CONNECTING              = 1
    TRANSFER_READY          = 2
    PREPARING_DATA_OBJECT   = 3
    SELECT_OBJECT           = 4
    CREATE_OBJECT           = 5
    TRANSFERRING_OBJECT     = 6
    VALIDATE_OBJECT         = 7
    EXECUTE_OBJECT          = 8
    TRANSFER_DONE           = 9

    @property
    def handler(self):
        match self:
            case self.DISCONNECTED:            return DisconnectedStateHandler
            case self.CONNECTING:              return ConnectingStateHandler
            case self.TRANSFER_READY:          return TransferReadyStateHandler
            case self.PREPARING_DATA_OBJECT:   return PreparingDataObjectStateHandler
            case self.SELECT_OBJECT:           return SelectObjectStateHandler
            case self.CREATE_OBJECT:           return CreateObjectStateHandler
            case self.TRANSFERRING_OBJECT:     return TransferringObjectStateHandler
            case self.VALIDATE_OBJECT:         return ValidateObjectStateHandler
            case self.EXECUTE_OBJECT:          return ExecuteObjectStateHandler
            case self.TRANSFER_DONE:           return TransferDoneStateHandler

    async def entry(self, context):
        return await self.handler.entry(context)

    async def handle(self, context):
        return await self.handler.handle(context)

    def exit(self, context):
        return self.handler.exit(context)


class SecureDFUContext:
    __slots__ = (
        "state",        # SecureTxState current state
        "prev_state",   # SecureTxState previous state
        # "status",       # TxStatus      state handler status
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
        "obj_type",     # ProcedureType current object type
        "object",       # bytearray     remaining object data to be transferred
        "pkt",          # bytearray     current raw data packet being transferred
        "prn",          # int           current packet receipt notification number
        "local_crc",    # int           current object crc on controller
        "bytes_sent",   # int           txdata bytes sent
        "objects_sent", # int           number of current data objects successfully sent
        "num_objects",  # int           number of data objects in current image
        "attempts",     # int           number of attempts to send current object
        "max_size",     # int           maximum object size in bytes
        "offset",       # int           current object offset on target
        "target_crc",   # int           current object crc on target
    )

    def __init__(self,
        name: str,
        pkg: DFUPackage,
        log: Logger = None,
        timeout: int = None,
    ):
        self.state      = SecureTxState.DISCONNECTED
        self.prev_state = None
        
        self.name           = name
        self.target         = None
        self.pkg            = pkg
        self.log            = log if log else logging.getLogger()
        self.timeout        = timeout
        self.img_queue      = [
            img for img in ['bootloader', 'softdevice', 'application'] \
            if img in self.pkg.images
        ]
        
        self.client         = None
        self.responses      = Queue()

        self.img            = None
        self.txdata         = None
        self.obj_type       = ProcedureType.INVALID
        self.object         = None
        self.pkt            = None
        self.prn            = 0
        self.local_crc      = 0
        self.bytes_sent     = 0
        self.objects_sent   = 0
        self.num_objects    = 0
        self.attempts       = 0
        self.max_size       = 0
        self.offset         = 0
        self.target_crc     = 0


    def transition(self, new_state: SecureTxState) -> TxStatus:
        """helper to transition states"""
        self.prev_state = self.state
        self.state = new_state
        return TxStatus.TRANSITIONED


    def get_response_nowait(self) -> Response | None:
        """get the last response from the client if there was one
        return None if there were no responses
        """
        try:
            notification = self.responses.get_nowait()
        except QueueEmpty as e:
            return None
        
        response = Response(notification.data)
        self.log.info(f"{notification.sender.description} {notification.time}: {response.description}")
        return response

    async def get_response(self) -> Response:
        """get the last response from the client"""
        notification = await self.responses.get()
        response = Response(notification.data)
        self.log.info(f"{notification.sender.description} {notification.time}: {response.description}")
        return response

    def get_prn_nowait(self) -> Response | None:
        """get the last response from client as a PRN
        return None if there were no responses
        """
        try:
            notification = self.responses.get_nowait()
        except QueueEmpty as e:
            return None

        prn_response = PRN(notification.data)
        assert prn_response.req_opcode == Opcode.CRC_GET
        self.log.debug(f"{notification.sender.description} {notification.time}: {prn_response.description}")
        return prn_response

    async def get_prn(self) -> Response:
        """get the last response from client as a PRN"""
        notification = await self.responses.get()
        prn_response = PRN(notification.data)
        assert prn_response.req_opcode == Opcode.CRC_GET
        self.log.debug(f"{notification.sender.description} {notification.time}: {prn_response.description}")
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

        return await self.get_response()

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

    async def crc_get(self):
        """send CRC_GET request"""
        return await self.client.write_ctl(Request(Opcode.CRC_GET))

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

        try:
            while status != TxStatus.COMPLETE:
                match status:
                    case TxStatus.ERROR:
                        assert_never("error handling not implemented")
                    case (TxStatus.INIT
                        | TxStatus.HANDLED
                        | TxStatus.IGNORED
                    ):
                        status = await self.context.state.handle(self.context)
                    case TxStatus.TRANSITIONED:
                        check_status(self.context.prev_state.exit(self.context))
                        status = await self.context.state.entry(self.context)
                    case TxStatus.COMPLETE:
                        assert_never("unreachable")
        finally:
            await self.context.client.disconnect()


############################################################
# DFU STATE MACHINE HANDLERS
############################################################


class DisconnectedStateHandler(TxStateHandler):
    MAX_ATTEMPTS = 10
    search_attempts = 0

    @classmethod
    async def entry(cls, context) -> TxStatus:
        context.log.info("entering state: DISCONNECTED")
        cls.search_attempts = 0
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        """attempt to locate target device"""

        if not context.img_queue:
            context.log.info("all images sent!")
            return context.transition(SecureTxState.TRANSFER_DONE)

        # find_device_by_name seems to not always work...
        cls.search_attempts += 1
        context.log.info(f"searching for target (attempt {cls.search_attempts}): {context.name}")
        context.target = await BleakScanner.find_device_by_name(context.name)
        # devices = await BleakScanner.discover()
        # for device in devices:
        #     if device.name == context.name:
        #         context.target = device
        #         break

        if not context.target:
            if cls.search_attempts < cls.MAX_ATTEMPTS:
                return TxStatus.HANDLED
            else:
                raise DFUErrorCode.FAILED_TO_CONNECT.as_err()
        
        context.log.info(f"{context.name} found!")
        return context.transition(SecureTxState.CONNECTING)


class ConnectingStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        """on entry, attempt to connect to the target device"""
        context.log.info("entering state: CONNECTING")
        context.client = SecureDFUClient(context.target.address)
        await context.client.connect()
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


        if context.client.is_connected:
            context.log.info(f"connected to target {context.name}! beginning notifications...")
            await context.client.start_notify(DFU_CTRL_POINT_UUID, response_callback)
            return context.transition(SecureTxState.TRANSFER_READY)
        else:
            return context.transition(SecureTxState.DISCONNECTED)


class TransferReadyStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        context.log.info("entering state: TRANSFER_READY")
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        assert context.pkg.images, "package contains no images to send!"

        img_type = context.img_queue[0]
        context.log.info(f"preparing to send {img_type} image...")

        # prepare to send image
        context.bytes_sent = 0
        context.local_crc = 0
        context.img = context.pkg.images[img_type]
        context.obj_type = ProcedureType.COMMAND
        context.txdata = bytearray(context.img.init_data)
        context.objects_sent = 0
        context.num_objects = 0
        context.attempts = 0

        return context.transition(SecureTxState.SELECT_OBJECT)


class PreparingDataObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        context.log.info("entering state: PREPARING_DATA_OBJECT")
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        """prepare the next data object from txdata to send"""

        context.log.info(f"preparing to send {context.img.img_type} ({len(context.txdata)} bytes) data objects...")

        context.bytes_sent = 0
        context.local_crc = 0
        context.obj_type = ProcedureType.DATA
        context.txdata = bytearray(context.img.img_data)
        context.objects_sent = 0
        context.num_objects = 0
        context.attempts = 0

        return context.transition(SecureTxState.SELECT_OBJECT)


class SelectObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        # always set prn to 0
        context.log.info("entering state: SELECT_OBJECT")
        await context.clear_prn_value(force=True)

        context.log.info("sending OBJECT_SELECT request...")
        await context.object_select(context.obj_type)
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        res = await context.get_response()
        check_response(res)

        context.log.info("OBJECT_SELECT response received!")

        context.max_size = res.max_size
        context.offset = res.offset
        context.target_crc = res.crc

        
        if not context.num_objects:
            # if not already set, set the total number of objects that must be sent
            if context.obj_type == ProcedureType.COMMAND:
                context.num_objects = (len(context.img.init_data) + context.max_size - 1) // context.max_size
            elif context.obj_type == ProcedureType.DATA:
                context.num_objects = (len(context.img.img_data) + context.max_size - 1) // context.max_size

        if (context.offset == len(context.txdata)
            and context.target_crc == crc32(context.txdata)
        ):
            # if the init packet has already been successfully sent, 
            # skip to execute
            context.object = context.txdata[:context.max_size]
            
            context.log.info("init packet has already been successfully sent. skipping to EXECUTE_OBJECT...")
            return context.transition(SecureTxState.EXECUTE_OBJECT)

        elif (context.offset != 0
            and context.offset <= len(context.txdata)
            and context.target_crc == crc32(context.txdata[context.offset])
        ):
            # if init packet partially sent without error, resume sending data
            context.object = context.object[context.offset:]

            context.log.info("init packet transfer incomplete. resuming transfer...")
            return context.transition(SecureTxState.TRANSFERRING_OBJECT)

        else:
            context.log.info("init packet not yet sent. creating init command object...")
            return context.transition(SecureTxState.CREATE_OBJECT)

class CreateObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context) -> TxStatus:
        context.log.info("entering state: CREATE_OBJECT")

        # reset prn to 0 if necessary
        await context.clear_prn_value()

        # copy the object from front of txdata
        context.object = context.txdata[:context.max_size]

        context.log.info(f"creating {context.obj_type.name} object {context.objects_sent + 1} ({len(context.object):#x} bytes) ...")
        context.log.info("sending OBJECT_CREATE request...")
        await context.object_create(context.obj_type, len(context.object))
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        res = await context.get_response()
        check_response(res)

        context.log.info(f"OBJECT_CREATE response received!")

        context.log.info("beginning object transfer...")
        return context.transition(SecureTxState.TRANSFERRING_OBJECT)

class TransferringObjectStateHandler(TxStateHandler):
    DEFAULT_PRN = 10
    GATT_PKT_SIZE = 20

    # state-specific class variables
    total_pkts = None
    pkts_sent = None
    object_data = None

    @classmethod
    async def entry(cls, context) -> TxStatus:
        """before transfer, set PRN value and reset class variables"""
        context.log.info("entering state: TRANSFERRING_OBJECT")

        # set prn to default
        context.log.info(f"setting PRN = {cls.DEFAULT_PRN}")
        res = await context.set_prn_value(cls.DEFAULT_PRN)
        check_response(res)

        # calculate total packets in the object being sent
        cls.total_pkts = (len(context.object) + cls.GATT_PKT_SIZE - 1) // cls.GATT_PKT_SIZE
        cls.pkts_sent = 0
        cls.object_data = bytearray(context.object)

        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context) -> TxStatus:
        """send the next object packet"""

        if cls.pkts_sent >= cls.total_pkts:
            # finished sending all packets, go to validation
            context.log.info(f"object {context.objects_sent + 1} / {context.num_objects} transferred. proceeding to validate object...")
            context.objects_sent += 1
            return context.transition(SecureTxState.VALIDATE_OBJECT)

        remaining_pkts = cls.total_pkts - cls.pkts_sent
        if not (cls.pkts_sent % cls.DEFAULT_PRN) and remaining_pkts < context.prn:
            # fewer packets left to send than the prn, update it now
            res = await context.set_prn_value(remaining_pkts)
            check_response(res)

        # prepare the next packet
        context.pkt = cls.object_data[:cls.GATT_PKT_SIZE]
        cls.object_data = cls.object_data[cls.GATT_PKT_SIZE:]
        context.local_crc = crc32(context.pkt, context.local_crc)
        
        # send the next packet
        context.log.debug(f"sending pkt ({cls.pkts_sent + 1} / {cls.total_pkts}): [ {hexlify(context.pkt, ' ')} ]")
        await context.client.write_pkt(context.pkt)
        cls.pkts_sent += 1
        context.bytes_sent += len(context.pkt)

        if cls.pkts_sent % context.prn:
            # no PRN expected, send another packet
            return TxStatus.HANDLED
        else:
            # expecting a notification
            res = await context.get_prn()
            check_response(res)

            context.log.debug(f"PRN: {res.description}")

            context.offset = res.offset
            context.target_crc = res.crc

            # validate data so far
            if context.offset != context.bytes_sent:
                context.log.error(f"offset mismatch! expected: {context.bytes_sent:#x}, got: {context.offset:#x}")
                raise DFUErrorCode.BYTES_LOST.as_err()
            elif context.target_crc != context.local_crc:
                context.log.error(f"crc mismatch! expected: {context.local_crc:#x}, got: {context.target_crc:#x}")
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
        """on entry, send CRC_GET request"""
        context.log.info("entering state: VALIDATE_OBJECT")
        await context.clear_prn_value()

        context.log.info("sending CRC_GET request...")
        await context.crc_get()
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        """validate CRC_GET response"""
        res = await context.get_response()
        assert res.req_opcode == Opcode.CRC_GET
        check_response(res)

        context.log.info("CRC_GET response received!")
        context.target_crc = res.crc

        if context.target_crc != context.local_crc:
            if context.attempts >= 3:
                raise DFUErrorCode.CRC_ERROR.as_err()
            context.log.info(f"object CRC mismatch! trying again... (attempts: {context.attempts})")
            objects_sent -= 1
            return context.transition(SecureTxState.CREATE_OBJECT)

        context.log.info("object CRC matched, proceeding to execute object...")
        return context.transition(SecureTxState.EXECUTE_OBJECT)

class ExecuteObjectStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        """on entry, send EXECUTE request"""
        context.log.info("entering state: EXECUTE_OBJECT")
        context.log.info("sending OBJECT_EXECUTE request...")
        await context.object_execute()
        return TxStatus.HANDLED

    @classmethod
    async def handle(cls, context):
        """handle EXECUTE response"""
        res = await context.get_response()
        assert res.req_opcode == Opcode.OBJECT_EXECUTE
        check_response(res)

        context.log.info("OBJECT_EXECUTE response received!")

        if context.obj_type == ProcedureType.COMMAND:
            # init command should be transferred, continue to send image data
            context.log.info("proceeding to transfer image data...")
            return context.transition(SecureTxState.PREPARING_DATA_OBJECT)

        elif context.objects_sent < context.num_objects:
            # image transfer incomplete, send next data object
            context.txdata = context.txdata[len(context.object):]

            context.log.info("sending next data object...")
            return context.transition(SecureTxState.CREATE_OBJECT)

        elif context.objects_sent == context.num_objects:
            # image transferred
            # remove this image from queue
            context.img_queue.pop(0)
            # the bootloader should automatically reset the device,
            # so we should expect a disconnect

            context.log.info("image transfer completed!")
            return context.transition(SecureTxState.DISCONNECTED)

        assert_never("unreachable")

class TransferDoneStateHandler(TxStateHandler):
    @classmethod
    async def entry(cls, context):
        """when transfer done, disconnect from client """
        context.log.info("transfer done! disconnecting...")
        await context.client.disconnect()

        context.log.info("update complete!")
        return TxStatus.COMPLETE

