#!/usr/bin/env python

"""
Heavily modified version of the original code from bacpypes:
https://github.com/JoelBender/bacpypes/blob/master/samples/COVClient.py
"""

import logging
import os
import socket
import sys

from bacpypes.apdu import (
    ErrorPDU,
    IAmRequest,
    ReadPropertyACK,
    ReadPropertyRequest,
    RejectPDU,
    RejectReason,
    SimpleAckPDU,
    SubscribeCOVPropertyRequest,
    SubscribeCOVRequest,
    WhoIsRequest
)
from bacpypes.app import BIPSimpleApplication
from bacpypes.basetypes import PropertyIdentifier, PropertyReference
from bacpypes.consolelogging import ConsoleLogHandler
from bacpypes.constructeddata import ArrayOf
from bacpypes.core import run, enable_sleeping
from bacpypes.debugging import bacpypes_debugging, ModuleLogger
from bacpypes.errors import DecodingError, ExecutionError
from bacpypes.iocb import IOCB
from bacpypes.local.device import LocalDeviceObject
from bacpypes.pdu import LocalBroadcast
from bacpypes.primitivedata import ObjectIdentifier
from bacpypes.settings import os_settings, settings, Settings
from bacpypes.task import RecurringFunctionTask

# some debugging
_debug = 0
_log = ModuleLogger(globals())

# globals
this_application = None

subscription_contexts = {}
next_proc_id = 1

# how the application should respond
rsvp = (True, None, None)


#
#   RequestContext
#

@bacpypes_debugging
class RequestContext:

    def __init__(self, device_id, device_addr):
        self.device_id = device_id
        self.device_addr = device_addr

        self.object_list = []
        self.object_names = []

        self._object_list_queue = None

    def completed(self, had_error=None):
        if had_error:
            print(f"had error: {had_error}", flush=True)
        else:
            for objid, objname in zip(self.object_list, self.object_names):
                print("%s: %s" % (objid, objname))


#
#  SubscriptionContext
#

@bacpypes_debugging
class SubscriptionContext:

    def __init__(self, address, obj_id, confirmed=None, lifetime=None):
        if _debug:
            self.__class__._debug(
                f"__init__ {address} {obj_id} confirmed={confirmed} lifetime={lifetime}")
        global subscription_contexts, next_proc_id

        # destination for subscription requests
        self.address = address

        # assign a unique process identifer and keep track of it
        self.subscriberProcessIdentifier = next_proc_id
        next_proc_id += 1
        subscription_contexts[self.subscriberProcessIdentifier] = self

        self.monitoredObjectIdentifier = obj_id
        self.issueConfirmedNotifications = confirmed
        self.lifetime = lifetime

    def cov_notification(self, apdu):
        if _debug:
            self.__class__._debug("cov_notification %r", apdu)

        # make a rash assumption that the property value is going to be
        # a single application encoded tag
        print("{} {} changed\n    {}".format(
            apdu.initiatingDeviceIdentifier,
            apdu.monitoredObjectIdentifier,
            ",\n    ".join("{} = {}".format(
                element.propertyIdentifier,
                str(element.value.tagList[0].app_to_object().value),
                ) for element in apdu.listOfValues),
            ), flush=True)

    def completed(self, had_error=None):
        if had_error:
            if isinstance(had_error, RejectPDU):
                print(f"Subscribing to {self.monitoredObjectIdentifier} produced {had_error.__class__.__name__}: {RejectReason(had_error.apduAbortRejectReason).value}", flush=True)
            elif isinstance(had_error, ErrorPDU):
                print(f"Subscribing to {self.monitoredObjectIdentifier} produced {had_error.__class__.__name__}: {had_error.errorClass} - {had_error.errorCode}", flush=True)
            else:
                print("had error: %r" % (had_error,), flush=True)
        else:
            for objid, objname in zip(self.object_list, self.object_names):
                print("%s: %s" % (objid, objname), flush=True)


#
#   SubscribeCOVApplication
#

@bacpypes_debugging
class SubscribeCOVApplication(BIPSimpleApplication):
    def __init__(self):
        # get subscription parameters from environment variables
        self.target_device_id = int(os.environ.get("TARGET_DEVICE_ID", "10"))
        self.issue_confirmed = os.getenv("SUBSCRIBE_CONFIRMED", "False").lower() in ("true", "1", "t", "y", "yes")
        self.issue_property_request = os.getenv("SUBSCRIBE_PROPERTY_REQUEST", "False").lower() in ("true", "1", "t", "y", "yes")
        self.lifetime = int(os.getenv("SUBSCRIPTION_LIFETIME", "0"))
        subnet_bits = int(os.getenv("SUBNET_BITS", 24))

        if _debug:
            self.__class__._debug(f"__init__ target device id: {self.target_device_id} confirmed_cov_requests: {self.issue_confirmed} use_cov_property_request: {self.issue_property_request} lifetime: {self.lifetime}")

        self.ip_address = None
        self.get_ip_address()

        # make a device object
        this_device = LocalDeviceObject(ini=self.get_settings())
        if _debug:
            _log.debug("    - this_device: %r", this_device)

        # normal initialization
        if _debug:
            self.__class__._debug(f"    - address {self.ip_address}/{subnet_bits}")
        BIPSimpleApplication.__init__(self, this_device, f"{self.ip_address}/{subnet_bits}")

        # keep track of requests to pair with responses
        self._request = None

        # track device lookups
        self.device_info_cache = {}
        self.object_list = []
        self.subscriptions = {}

    def get_ip_address(self):
        # use the socket library to get the IP address
        if self.ip_address is None:
            # use socket to get all external IP addresses
            all_ip_addresses = socket.gethostbyname_ex(socket.gethostname())[-1]
            # make the list of IP addresses unique, filter out the local address and get the first remaining address
            if _debug:
                self.__class__._debug("all_ip_addresses: %r", all_ip_addresses)
            non_local_ip_addresses = [ip for ip in list(set(all_ip_addresses)) if ip != "127.0.0.1"]
            self.ip_address = non_local_ip_addresses[0]

        if _debug:
            self.__class__._debug("get_ip_address: %r", self.ip_address)
        return self.ip_address

    def get_settings(self):
        if _debug:
            self.__class__._debug("get_settings")
        return Settings(
            {
                "objectname": "SubscribeCOVClient",
                "objectidentifier": int(os.environ.get("LOCAL_DEVICE_IDENTIFIER", "599")),
                "maxapdulengthaccepted": 1476,
                "vendoridentifier": 15,
            }
        )

    def do_ConfirmedCOVNotificationRequest(self, apdu):
        if _debug:
            self.__class__._debug("do_ConfirmedCOVNotificationRequest %r", apdu)

        # look up the process identifier
        context = subscription_contexts.get(apdu.subscriberProcessIdentifier, None)
        if not context or apdu.pduSource != context.address:
            if _debug:
                self.__class__._debug("    - no context")

            # this is turned into an ErrorPDU and sent back to the client
            raise ExecutionError('services', 'unknownSubscription')

        # now tell the context object
        context.cov_notification(apdu)

        # success
        response = SimpleAckPDU(context=apdu)
        if _debug:
            self.__class__._debug("    - simple_ack: %r", response)

        # return the result
        self.response(response)

    def do_UnconfirmedCOVNotificationRequest(self, apdu):
        if _debug:
            self.__class__._debug("do_UnconfirmedCOVNotificationRequest %r", apdu)

        # look up the process identifier
        context = subscription_contexts.get(apdu.subscriberProcessIdentifier, None)
        if not context or apdu.pduSource != context.address:
            if _debug:
                self.__class__._debug("    - no context")
            return

        # now tell the context object
        context.cov_notification(apdu)

    def do_RequestDeviceAddress(self):
        device_instance = self.target_device_id
        if _debug:
            self.__class__._debug(
                f"Requesting device info over BACnet for device {device_instance}"
            )
        context = RequestContext(device_instance, None)

        request = WhoIsRequest(
            destination=LocalBroadcast(),
            deviceInstanceRangeLowLimit=device_instance,
            deviceInstanceRangeHighLimit=device_instance
        )
        if _debug:
            self.__class__._debug(f"Request: {request}")

        iocb = IOCB(request)
        iocb.context = context
        self._request = request
        self.request_io(iocb)

    def do_RequestObjectList(self):
        device_instance = self.target_device_id
        if _debug:
            self.__class__._debug(
                f"Requesting object list over BACnet for device {device_instance} with address {self.device_info_cache[device_instance]}"
            )
        context = RequestContext(device_instance, self.device_info_cache[device_instance])

        request = ReadPropertyRequest(
            destination=context.device_addr,
            objectIdentifier=ObjectIdentifier("device", device_instance),
            propertyIdentifier="objectList",
        )
        if _debug:
            self.__class__._debug("    - request: %r", request)

        iocb = IOCB(request)
        iocb.context = context
        iocb.add_callback(self.object_list_results)
        self.request_io(iocb)

    def do_SubscribeCOV(self):
        issue_confirmed = self.issue_confirmed
        issue_property_request = self.issue_property_request
        lifetime = self.lifetime

        for obj_id in self.object_list:
            if _debug:
                self.__class__._debug(f"    - object: {obj_id}, type = {obj_id[0]}")
            if obj_id[0] == "device":
                continue

            context = SubscriptionContext(
                self.device_info_cache[self.target_device_id],
                obj_id,
                confirmed=issue_confirmed,
                lifetime=lifetime
            )

            if issue_property_request:
                request = SubscribeCOVPropertyRequest(
                    subscriberProcessIdentifier=context.subscriberProcessIdentifier,
                    monitoredObjectIdentifier=obj_id,
                    monitoredPropertyIdentifier=PropertyReference(propertyIdentifier=PropertyIdentifier.presentValue),
                    issueConfirmedNotifications=issue_confirmed,
                    lifetime=lifetime
                )
            else:
                request = SubscribeCOVRequest(
                    subscriberProcessIdentifier=context.subscriberProcessIdentifier,
                    monitoredObjectIdentifier=obj_id,
                    issueConfirmedNotifications=issue_confirmed,
                    lifetime=lifetime
                )
            if _debug:
                self.__class__._debug("    - request: %r", request)
            request.pduDestination = self.device_info_cache[self.target_device_id]
            iocb = IOCB(request)
            iocb.context = context
            iocb.add_callback(self.cov_results)
            self.request_io(iocb)

    def object_list_results(self, iocb):
        if _debug:
            self.__class__._debug("object_list_results %r", iocb)

        # extract the context
        context = iocb.context

        # do something for error/reject/abort
        if iocb.ioError:
            context.completed(iocb.ioError)
            return

        # do something for success
        apdu = iocb.ioResponse

        # should be an ack
        if not isinstance(apdu, ReadPropertyACK):
            if _debug:
                __class__._debug("    - not an ack")
            context.completed(RuntimeError("read property ack expected"))
            return

        # pull out the content
        self.object_list = apdu.propertyValue.cast_out(ArrayOf(ObjectIdentifier))
        if _debug:
            __class__._debug("    - object_list: %r", self.object_list)

        # store it in the context
        context.object_list = self.object_list

    def cov_results(self, iocb):
        if _debug:
            self.__class__._debug("cov_results %r", iocb)

        # extract the context
        context = iocb.context

        # do something for error/reject/abort
        if iocb.ioError:
            context.completed(iocb.ioError)
            self.subscriptions[iocb.context.monitoredObjectIdentifier] = False
            return

        # do something for success
        apdu = iocb.ioResponse

        # should be an ack
        if not isinstance(apdu, SimpleAckPDU):
            if _debug:
                __class__._debug("    - not an ack")
            context.completed(RuntimeError("COV ack expected"))
            return

        self.subscriptions[iocb.context.monitoredObjectIdentifier] = True

    def indication(self, apdu):
        if _debug:
            self.__class__._debug(f"indication {apdu}")

        if not self._request:
            if _debug:
                self.__class__._debug("    - no pending request")

        elif isinstance(apdu, IAmRequest):
            device_type, device_instance = apdu.iAmDeviceIdentifier
            if device_type != "device":
                raise DecodingError("invalid object type")
            self.device_info_cache[int(apdu.iAmDeviceIdentifier[1])] = apdu.pduSource
            # print out the contents
            if _debug:
                self.__class__._debug("pduSource = " + repr(apdu.pduSource))
                self.__class__._debug(
                    "iAmDeviceIdentifier = " + str(apdu.iAmDeviceIdentifier)
                )

        # forward it along
        BIPSimpleApplication.indication(self, apdu)

    def request(self, apdu):
        if _debug:
            self.__class__._debug(f"request {apdu}")
        if isinstance(apdu, WhoIsRequest):
            self._request = apdu
        BIPSimpleApplication.request(self, apdu)

    def confirmation(self, apdu):
        if _debug:
            self.__class__._debug(f"confirmation {apdu}")

        # forward it along
        BIPSimpleApplication.confirmation(self, apdu)

    def do_RunTasks(self):
        if _debug:
            self.__class__._debug("do_RunTasks")
        if self.device_info_cache.get(self.target_device_id, None) is None:
            self.do_RequestDeviceAddress()
        elif len(self.object_list) == 0:
            self.do_RequestObjectList()
        elif len(self.subscriptions.keys()) == 0:
            self.do_SubscribeCOV()


#
#   __main__
#


@bacpypes_debugging
def update_logging(handler=None):
    # These modules seem to unconditionally print debug messages, so we'll suppress them
    for module in ["bacpypes.task.OneShotFunction", "bacpypes.task.TaskManager"]:
        ConsoleLogHandler(module, handler=handler, level=logging.ERROR)
    for _, debug_name in enumerate(settings.debug):
        try:
            ConsoleLogHandler(debug_name, handler=handler, level=logging.DEBUG)
        except RuntimeError as e:
            if not e.args[0].startswith("not a valid logger name"):
                raise e


def main():
    global this_application

    # parse the command line arguments
    # args = ConfigArgumentParser(description=__doc__).parse_args()

    os_settings()

    update_logging()

    if _debug:
        _log.debug("initialization")

    # make a simple application
    this_application = SubscribeCOVApplication()
    task = RecurringFunctionTask(2.0 * 1000, this_application.do_RunTasks)
    task.install_task()

    # enable sleeping will help with threads
    enable_sleeping()

    _log.debug("running")

    run()

    _log.debug("fini")


if __name__ == "__main__":
    main()
