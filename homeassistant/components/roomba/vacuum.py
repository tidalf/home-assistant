"""Support for Wi-Fi enabled iRobot Roombas."""
import asyncio
import async_timeout
import logging
import json
import voluptuous as vol

from homeassistant.components.vacuum import (
    PLATFORM_SCHEMA,
    SUPPORT_BATTERY,
    SUPPORT_FAN_SPEED,
    SUPPORT_PAUSE,
    SUPPORT_RETURN_HOME,
    SUPPORT_SEND_COMMAND,
    SUPPORT_STATUS,
    SUPPORT_STOP,
    SUPPORT_TURN_OFF,
    SUPPORT_TURN_ON,
    SUPPORT_LOCATE,
    SUPPORT_MAP,
    VacuumDevice,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_USERNAME,
    ATTR_ENTITY_ID,
)
from homeassistant.exceptions import PlatformNotReady

import homeassistant.helpers.config_validation as cv

_LOGGER = logging.getLogger(__name__)
SERVICE_SET_AREAS = "set_areas"


ATTR_BIN_FULL = "bin_full"
ATTR_BIN_PRESENT = "bin_present"
ATTR_CLEANING_TIME = "cleaning_time"
ATTR_CLEANED_AREA = "cleaned_area"
ATTR_AREAS = "areas"

ATTR_ERROR = "error"
ATTR_POSITION = "position"
ATTR_SOFTWARE_VERSION = "software_version"

ATTR_MAPS = "maps"
CAP_BIN_FULL = "bin_full"
CAP_POSITION = "position"
CAP_CARPET_BOOST = "carpet_boost"
CAP_MAPS = "maps"

CONF_CERT = "certificate"
CONF_CONTINUOUS = "continuous"

DEFAULT_CERT = "/etc/ssl/certs/ca-certificates.crt"
DEFAULT_CONTINUOUS = True
DEFAULT_NAME = "Roomba"

PLATFORM = "roomba"

FAN_SPEED_AUTOMATIC = "Automatic"
FAN_SPEED_ECO = "Eco"
FAN_SPEED_PERFORMANCE = "Performance"
FAN_SPEEDS = [FAN_SPEED_AUTOMATIC, FAN_SPEED_ECO, FAN_SPEED_PERFORMANCE]

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_HOST): cv.string,
        vol.Required(CONF_USERNAME): cv.string,
        vol.Required(CONF_PASSWORD): cv.string,
        vol.Optional(CONF_CERT, default=DEFAULT_CERT): cv.string,
        vol.Optional(CONF_CONTINUOUS, default=DEFAULT_CONTINUOUS): cv.boolean,
    },
    extra=vol.ALLOW_EXTRA,
)
VACUUM_SERVICE_SCHEMA = vol.Schema({vol.Optional(ATTR_ENTITY_ID): cv.comp_entity_ids})

VACUUM_SET_AREAS_SERVICE_SCHEMA = VACUUM_SERVICE_SCHEMA.extend(
    {vol.Required(ATTR_AREAS): vol.All(cv.ensure_list, [cv.string])}
)

SERVICE_SET_AREAS = "roomba_set_areas"
SERVICE_TO_METHOD = {
    SERVICE_SET_AREAS: {
        "method": "async_set_areas",
        "schema": VACUUM_SET_AREAS_SERVICE_SCHEMA,
    },
}

# Commonly supported features
SUPPORT_ROOMBA = (
    SUPPORT_BATTERY
    | SUPPORT_PAUSE
    | SUPPORT_RETURN_HOME
    | SUPPORT_SEND_COMMAND
    | SUPPORT_STATUS
    | SUPPORT_STOP
    | SUPPORT_TURN_OFF
    | SUPPORT_TURN_ON
    | SUPPORT_LOCATE
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the iRobot Roomba vacuum cleaner platform."""

    from irbt import Cloud, Robot

    if PLATFORM not in hass.data:
        hass.data[PLATFORM] = {}

    name = config.get(CONF_NAME)
    username = config.get(CONF_USERNAME)
    password = config.get(CONF_PASSWORD)
    roomba = None
    cloud = False
    host = None

    if config.get(CONF_HOST):
        from roomba import Roomba

        host = config.get(CONF_HOST)
        certificate = config.get(CONF_CERT)
        continuous = config.get(CONF_CONTINUOUS)

        roomba = Roomba(
            address=host,
            blid=username,
            password=password,
            cert_name=certificate,
            continuous=continuous,
        )
        _LOGGER.debug("Initializing communication with host %s", host)
        try:
            with async_timeout.timeout(9):
                await hass.async_add_job(roomba.connect)
        except asyncio.TimeoutError:
            raise PlatformNotReady
    else:
        cloud = Cloud(username=username, password=password)
        roomba = Robot(rid=list(cloud.robots())[0], cloud=cloud)
        _LOGGER.debug("Initializing communication with irbt cloud")
        roomba.connect()
        cloud = True

    roomba_vac = RoombaVacuum(name, roomba, cloud=cloud)
    hass.data[PLATFORM][host] = roomba_vac

    async_add_entities([roomba_vac], True)


class RoombaVacuum(VacuumDevice):
    """Representation of a Roomba Vacuum cleaner robot."""

    def __init__(self, name, roomba, cloud=True):
        """Initialize the Roomba handler."""
        self._available = False
        self._battery_level = None
        self._capabilities = {}
        self._fan_speed = None
        self._is_on = False
        self._name = name
        self._state_attrs = {}
        self._status = None
        self.vacuum = roomba
        self.vacuum_state = None
        self._areas = None
        self._areas_list = []
        self._cloud = cloud

    @property
    def supported_features(self):
        """Flag vacuum cleaner robot features that are supported."""
        capabilities = SUPPORT_ROOMBA
        if self._capabilities.get(CAP_MAPS):
            capabilities = capabilities | SUPPORT_MAP
        if self._capabilities.get(CAP_CARPET_BOOST):
            capabilities = capabilities | SUPPORT_FAN_SPEED

        return capabilities

    @property
    def fan_speed(self):
        """Return the fan speed of the vacuum cleaner."""
        return self._fan_speed

    @property
    def fan_speed_list(self):
        """Get the list of available fan speed steps of the vacuum cleaner."""
        if self._capabilities.get(CAP_CARPET_BOOST):
            return FAN_SPEEDS

    @property
    def areas(self):
        """Return the fan speed of the vacuum cleaner."""
        return self._areas

    @property
    def areas_list(self):
        """Get the list of available fan speed steps of the vacuum cleaner."""
        return self._areas_list

    @property
    def battery_level(self):
        """Return the battery level of the vacuum cleaner."""
        return self._battery_level

    @property
    def status(self):
        """Return the status of the vacuum cleaner."""
        return self._status

    @property
    def is_on(self) -> bool:
        """Return True if entity is on."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return self._available

    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def device_state_attributes(self):
        """Return the state attributes of the device."""
        return self._state_attrs

    async def async_turn_on(self, **kwargs):
        """Turn the vacuum on."""
        if (
            not self._cloud
            or self._areas is None
            or "All" in self._areas
            or len(self._areas) == 0
        ):
            await self.hass.async_add_job(self.vacuum.send_command, "start")
        else:
            local_room_ids = []
            for room_name in self._areas:
                local_room_ids.append(self.vacuum.get_room_id(room_name))
            await self.hass.async_add_job(
                self.vacuum.send_command, "start", ",".join(local_room_ids)
            )

        self._is_on = True

    async def async_turn_off(self, **kwargs):
        """Turn the vacuum off and return to home."""
        await self.async_stop()
        await self.async_return_to_base()

    async def async_stop(self, **kwargs):
        """Stop the vacuum cleaner."""
        await self.hass.async_add_job(self.vacuum.send_command, "stop")
        self._is_on = False

    async def async_resume(self, **kwargs):
        """Resume the cleaning cycle."""
        await self.hass.async_add_job(self.vacuum.send_command, "resume")
        self._is_on = True

    async def async_pause(self):
        """Pause the cleaning cycle."""
        await self.hass.async_add_job(self.vacuum.send_command, "pause")
        self._is_on = False

    async def async_start_pause(self, **kwargs):
        """Pause the cleaning task or resume it."""
        if self.vacuum_state and self.is_on:  # vacuum is running
            await self.async_pause()
        elif self._status == "Stopped":  # vacuum is stopped
            await self.async_resume()
        else:  # vacuum is off
            await self.async_turn_on()

    async def async_return_to_base(self, **kwargs):
        """Set the vacuum cleaner to return to the dock."""
        await self.hass.async_add_job(self.vacuum.send_command, "dock")
        self._is_on = False

    async def async_locate(self, **kwargs):
        """Located vacuum."""
        await self.hass.async_add_job(self.vacuum.send_command, "find")

    async def async_set_areas(self, areas, **kwargs):
        """Set areas from ui dropdown."""
        _LOGGER.debug("Areas: %s", areas)
        self._areas = areas

    async def async_set_fan_speed(self, fan_speed, **kwargs):
        """Set fan speed."""
        if fan_speed.capitalize() in FAN_SPEEDS:
            fan_speed = fan_speed.capitalize()
        _LOGGER.debug("Set fan speed to: %s", fan_speed)
        high_perf = None
        carpet_boost = None
        if fan_speed == FAN_SPEED_AUTOMATIC:
            high_perf = False
            carpet_boost = True
            self._fan_speed = FAN_SPEED_AUTOMATIC
        elif fan_speed == FAN_SPEED_ECO:
            high_perf = False
            carpet_boost = False
            self._fan_speed = FAN_SPEED_ECO
        elif fan_speed == FAN_SPEED_PERFORMANCE:
            high_perf = True
            carpet_boost = False
            self._fan_speed = FAN_SPEED_PERFORMANCE
        else:
            _LOGGER.error("No such fan speed available: %s", fan_speed)
            return
        # The set_preference method does only accept string values
        await self.hass.async_add_job(
            self.vacuum.set_preference, "carpetBoost", str(carpet_boost)
        )
        await self.hass.async_add_job(
            self.vacuum.set_preference, "vacHigh", str(high_perf)
        )

    async def async_send_command(self, command, params=None, **kwargs):
        """Send raw command."""
        _LOGGER.debug("async_send_command %s (%s), %s", command, params, kwargs)
        await self.hass.async_add_job(self.vacuum.send_command, command, params)
        return True

    def set_state(self, payload, response_status, token):
        """Set state."""
        if not payload:
            return
        payload_dict = json.loads(payload)
        if not payload_dict:
            return
        # self.vacuum.disconnect()
        self.vacuum_state = payload_dict["state"]["reported"]

    async def async_update(self):
        """Fetch state from the device."""
        if not self._cloud:
            if not self.vacuum.master_state:
                _LOGGER.debug("Roomba %s has no data yet. Skip update", self.name)
                return
            state = self.vacuum.master_state.get("state", {}).get("reported", {})
            _LOGGER.debug("Got new state from the vacuum: %s", state)
            self.vacuum_state = state
        else:
            self.vacuum.command.status(print_output=self.set_state)
            state = self.vacuum_state

        self._available = True
        if not state:
            return 0
        # Get the capabilities of our unit
        capabilities = state.get("cap", {})
        cap_bin_full = capabilities.get("binFullDetect")
        cap_carpet_boost = capabilities.get("carpetBoost")
        cap_pos = False  # capabilities.get("pose")
        cap_maps = capabilities["maps"]
        # Store capabilities
        self._capabilities = {
            CAP_BIN_FULL: cap_bin_full == 1,
            CAP_MAPS: cap_maps == 3,
            CAP_CARPET_BOOST: cap_carpet_boost == 1,
            CAP_POSITION: cap_pos == 1,
        }

        bin_state = state.get("bin", {})

        # Roomba software version
        software_version = state.get("softwareVer")

        # Error message in plain english
        error_msg = "None"
        if hasattr(self.vacuum, "error_message"):
            error_msg = self.vacuum.error_message

        self._battery_level = state.get("batPct")
        if not self._cloud:
            self._status = self.vacuum.current_state
        else:
            self._status = self.vacuum.current_state(
                state["cleanMissionStatus"]["phase"]
            )
        self._is_on = self._status in ["Running"]

        # Set properties that are to appear in the GUI
        self._state_attrs = {
            ATTR_BIN_PRESENT: bin_state.get("present"),
            ATTR_SOFTWARE_VERSION: software_version,
        }

        # Only add cleaning time and cleaned area attrs when the vacuum is
        # currently on
        if self._is_on:
            # Get clean mission status
            mission_state = state.get("cleanMissionStatus", {})
            cleaning_time = mission_state.get("mssnM")
            cleaned_area = mission_state.get("sqft")  # Imperial
            # Convert to m2 if the unit_system is set to metric
            if cleaned_area and self.hass.config.units.is_metric:
                cleaned_area = round(cleaned_area * 0.0929)
            self._state_attrs[ATTR_CLEANING_TIME] = cleaning_time
            self._state_attrs[ATTR_CLEANED_AREA] = cleaned_area

        # Skip error attr if there is none
        if error_msg and error_msg != "None":
            self._state_attrs[ATTR_ERROR] = error_msg

        # Not all Roombas expose position data
        # https://github.com/koalazak/dorita980/issues/48
        if self._capabilities[CAP_POSITION]:
            pos_state = state.get("pose", {})
            position = None
            pos_x = pos_state.get("point", {}).get("x")
            pos_y = pos_state.get("point", {}).get("y")
            theta = pos_state.get("theta")
            if all(item is not None for item in [pos_x, pos_y, theta]):
                position = f"({pos_x}, {pos_y}, {theta})"
            self._state_attrs[ATTR_POSITION] = position

        # Not all Roombas have a bin full sensor
        if self._capabilities[CAP_BIN_FULL]:
            self._state_attrs[ATTR_BIN_FULL] = bin_state.get("full")

        # Fan speed mode (Performance, Automatic or Eco)
        # Not all Roombas expose carpet boost
        if self._capabilities[CAP_CARPET_BOOST]:
            fan_speed = None
            carpet_boost = state.get("carpetBoost")
            high_perf = state.get("vacHigh")

            if carpet_boost is not None and high_perf is not None:
                if carpet_boost:
                    fan_speed = FAN_SPEED_AUTOMATIC
                elif high_perf:
                    fan_speed = FAN_SPEED_PERFORMANCE
                else:  # carpet_boost and high_perf are False
                    fan_speed = FAN_SPEED_ECO

            self._fan_speed = fan_speed

        if self._capabilities[CAP_MAPS]:
            self._areas_list = []
            for room in self.vacuum.rooms():
                self._areas_list.append(room["name"])
