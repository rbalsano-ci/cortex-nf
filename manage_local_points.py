import grpc
import os
import re
import sys

from grpc._channel import _InactiveRpcError as GrpcError
from time import sleep

from normalgw.bacnet.v1.bacenum_pb2 import EngineeringUnits, ObjectType, PropertyId
from normalgw.bacnet.v1.bacnet_pb2 import (
    ApplicationDataValue,
    CreateLocalObjectRequest,
    DeleteLocalObjectRequest,
    GetLocalObjectsRequest,
    ObjectId,
    PropertyValue,
    UpdateLocalObjectRequest,
)
from normalgw.bacnet.v1.bacnet_pb2_grpc import ConfigurationStub


def get_settings():
    return {
        "grpc_port": os.environ.get("GRPC_PORT", "8080"),
        "grpc_host": os.environ.get("GRPC_HOST", "localhost"),
    }


class ConfigurationApi(ConfigurationStub):
    RETRY_LIMIT = 5
    INITIAL_DELAY = 1
    BACKOFF_FACTOR = 2

    def retry_on_connection_error(self, func_name, *args, **kwargs):
        delay = self.INITIAL_DELAY
        for n in range(self.RETRY_LIMIT):
            print(f"Attempt {n + 1}/{self.RETRY_LIMIT}...", flush=True)
            try:
                func = getattr(super(), func_name)
                return func(*args, **kwargs)
            except GrpcError as e:
                if e.code == grpc.StatusCode.UNAVAILABLE:
                    print("Connection error, retrying...", flush=True)
                    sleep(delay)
                    if n >= self.RETRY_LIMIT - 1:
                        raise e
                    delay *= self.BACKOFF_FACTOR
                else:
                    raise e
            except Exception as e:
                print(f"Unexpected error: {e}")

    def __init__(self, channel):
        self.retry_on_connection_error("__init__", channel)

    def GetLocalObjects(self, *args, **kwargs):
        print("Getting local objects...")
        return self.retry_on_connection_error("GetLocalObjects", *args, **kwargs)

    def DeleteLocalObject(self, *args, **kwargs):
        return self.retry_on_connection_error("DeleteLocalObject", *args, **kwargs)

    def CreateLocalObject(self, *args, **kwargs):
        return self.retry_on_connection_error("CreateLocalObject", *args, **kwargs)

    def UpdateLocalObject(self, *args, **kwargs):
        return self.retry_on_connection_error("UpdateLocalObject", *args, **kwargs)


class LocalPointManager:

    bacnet_types = [
        'analog_input',
        'analog_output',
        'analog_value',
        'binary_input',
        'binary_output',
        'binary_value',
    ]

    analog_min = 55
    analog_max = 85
    analog_step = 1.5

    def __init__(self, channel):
        self.configuration_api = ConfigurationApi(channel)

    def object_id_to_string(self, object_id):
        return f"{ObjectType.Name(object_id.object_type)} {object_id.instance}"

    def clear_local_points(self):
        for obj in self.configuration_api.GetLocalObjects(GetLocalObjectsRequest()).objects:
            print(f"Deleting local object {self.object_id_to_string(obj.object_id)}...")
            self.configuration_api.DeleteLocalObject(DeleteLocalObjectRequest(object_id=obj.object_id))

    def create_local_points(self):
        for bacnet_type in self.bacnet_types:
            print(f"Creating local point for {bacnet_type}...")
            object_type_name = f"OBJECT_{bacnet_type.upper()}"
            if bacnet_type.startswith("analog"):
                present_value = ApplicationDataValue(real=70.0)
                additional_properties = {
                    "PROP_UNITS": ApplicationDataValue(enumerated=EngineeringUnits.Value("UNITS_DEGREES_FAHRENHEIT")),
                }
            elif bacnet_type.startswith("binary"):
                present_value = ApplicationDataValue(enumerated=1)
                additional_properties = {
                    "PROP_INACTIVE_TEXT": ApplicationDataValue(character_string="Inactive"),
                    "PROP_ACTIVE_TEXT": ApplicationDataValue(character_string="Active"),
                }
            properties = {
                "PROP_DESCRIPTION": ApplicationDataValue(character_string=f"Test object {bacnet_type}"),
                "PROP_OBJECT_IDENTIFIER": ApplicationDataValue(
                    character_string=re.sub(r"_(\w)", lambda x: x[1].upper(), bacnet_type)
                ),
                "PROP_OBJECT_NAME": ApplicationDataValue(character_string=f"test_{bacnet_type}"),
                "PROP_PRESENT_VALUE": present_value,
                **additional_properties,
            }
            instance = 1
            request = CreateLocalObjectRequest(
                object_id=ObjectId(
                    object_type=ObjectType.Value(object_type_name),
                    instance=instance
                )
            )
            for name, value in properties.items():
                pv = PropertyValue(property=PropertyId.Value(name), value=value)
                request.props.append(pv)

            try:
                self.configuration_api.CreateLocalObject(request)
            except GrpcError as e:
                print(f"Failed to create local object {object_type_name}:{instance}: {e}")

    def create_and_manage_local_points(self):
        self.clear_local_points()
        self.create_local_points()

    def update_values(self):
        for obj in self.configuration_api.GetLocalObjects(GetLocalObjectsRequest()).objects:
            present_value_property = next(prop for prop in obj.props if prop.property == PropertyId.Value("PROP_PRESENT_VALUE"))
            object_type = ObjectType.Name(obj.object_id.object_type)
            if re.search("BINARY", object_type):
                old_value = present_value_property.value.enumerated
                next_value = self.get_next_binary_value(old_value)
                new_present_value = ApplicationDataValue(enumerated=next_value)
            elif re.search("ANALOG", object_type):
                old_value = present_value_property.value.real
                next_value = self.get_next_analog_value(old_value)
                new_present_value = ApplicationDataValue(real=next_value)
            else:
                raise ValueError(f"Unsupported object type: {object_type}")

            print(f"Updating {self.object_id_to_string(obj.object_id)} from {str(old_value)} to {str(next_value)}...")
            self.configuration_api.UpdateLocalObject(
                UpdateLocalObjectRequest(
                    object_id=obj.object_id,
                    props=[PropertyValue(property=PropertyId.Value("PROP_PRESENT_VALUE"), value=new_present_value)],
                )
            )

    def get_next_binary_value(self, current_value):
        return 1 if current_value == 0 else 0

    def get_next_analog_value(self, current_value):
        next_value = current_value + self.analog_step
        while next_value > self.analog_max:
            next_value = self.analog_min + (next_value - self.analog_max)
        return next_value


if __name__ == '__main__':
    settings = get_settings()
    channel = grpc.insecure_channel(f"{settings['grpc_host']}:{settings['grpc_port']}")
    mgr = LocalPointManager(channel)
    mgr.create_and_manage_local_points()
    while True:
        mgr.update_values()
        sys.stdout.flush()
        sleep(10)
