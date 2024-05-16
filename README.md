# cortex-nf

Cortex experimentation with Normal Framework (NF). Currently only focused on CoV subscription.

The samples here rely on `docker`. Two containers are running standard NF images. Two additional images run two processes:
* `manage_local_points.py` - attempts to create basic object types on the NF server (analog and binary input, output and value). After they are created the `present-value` of each point is periodically updated.
* `cov_client.py` - discovers the NF server, finds all objects and requests a CoV subscription to the `present-value` of each. How the requests are issued is controlled by some environment variables (see `Configuration` below). CoV updates are printed out.

## Installation

Run `bin/setup` to set up the local environment. The script targets OS X and uses Cortex's credentials on NF's `docker` registry.

## Configuration

* Tweak environment variables in `docker_cov_client.yml`
 * `TARGET_DEVICE_ID` defaults to `10` to match the NF default
 * `BACPYPES_DEBUG` defaults to `""` useful for debugging `bacpypes` used by `cov_client.py`; example `"__main__ bacpypes.udp bacpypes.bvllservice bacpypes.app"`
 * `SUBSCRIBE_CONFIRMED` defaults to `"false"`; set to `"true"` to issue confirmed subscriptions
 * `SUBSCRIBE_PROPERTY_REQUEST` defaults to `"false"`; if `"true"`, switch from `subscribeCOV` to `subscribeCOVProperty`
 * `SUBSCRIPTION_LIFETIME` defaults to 60
 * `SUBNET_BITS` `docker` subnet bits (defaults to 20); used to generate the correct broadcast address

## Running

To run against NF version 3:

`docker-compose -f docker-compose-nf-3.yml up`

To run against version 2, just change the `3` to a `2`.

### Monitoring

Logs from each of the containers can be monitored for debugging purposes. The output in `cov-client` container is the one to look at for the end results of the CoV subscriptions.
