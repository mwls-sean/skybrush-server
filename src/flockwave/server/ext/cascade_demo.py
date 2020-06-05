"""Experimental extension to demonstrate the connection between an ERP system
and a Skybrush server, in collaboration with Cascade Ltd
"""

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from time import time
from trio import open_memory_channel, sleep
from typing import Dict, List, Tuple
from zipfile import ZipFile, ZIP_DEFLATED

from flockwave.gps.vectors import GPSCoordinate

from .base import ExtensionBase
from .dock.model import Dock

CHOREO_STR = """# this is a choreography file generated by flockwave-server

[sequence]

wait_on_ground=waypoint
go_on_route=waypoint

[wait_on_ground]
waypoint.waypoint_file=waypoint_ground.cfg
waypoint.whats_next=loiter

[go_on_route]
waypoint.waypoint_file=waypoints.cfg
waypoint.minimum_altitude=3
waypoint.altitude_setpoint={agl:.2f}
waypoint.velocity_xy={velocity_xy:.2f}
waypoint.velocity_z={velocity_z:.2f}
waypoint.velocity_threshold=6
waypoint.manual_override_rp=stop
waypoint.manual_override_gas=shift
waypoint.whats_next=land
"""

META_NAME_STR = "cascade_demo"

META_VERSION_STR = "1"

MISSION_STR = """# this is a mission file generated by flockwave-server

[settings]
choreography_file=choreography.cfg
"""

WAYPOINT_INIT_STR = """# this is a waypoint file generated by flockwave-server

[init]
#angle=
ground_altitude=0
#origin=

[waypoints]
motoroff=10
"""

WAYPOINT_STR = """
# taking off towards station '{station}'
motoron=5
takeoff=5 4 1 0
waypoint=N{lat:.8f} E{lon:.8f} {agl:.2f} {velocity_xy:.2f} {velocity_z:.2f} 5 0
# landing at station '{station}'
land=4 1 0
motoroff=10
"""


@dataclass
class Station:
    """Model object representing a single station in the demo."""

    id: str
    position: GPSCoordinate

    @classmethod
    def from_json(cls, obj: Tuple[float, float], id: str):
        """Creates a station from its JSON representation."""
        pos = GPSCoordinate(lon=obj[0], lat=obj[1], agl=0)
        return cls(id=id, position=pos)

    def create_dock(self) -> Dock:
        """Creates a docking station object from this specification."""
        dock = Dock(id=self.id)
        dock.update_status(position=self.position)
        return dock


class TripStatus(Enum):
    """Enum class representing the possible statuses of a trip."""

    NEW = "new"
    UPLOADING = "uploading"
    UPLOADED = "uploaded"
    ERROR = "error"


@dataclass
class Trip:
    """Model object representing a single scheduled trip of a UAV in the demo."""

    uav_id: str
    start_time: float
    route: List[str]
    status: TripStatus = TripStatus.NEW


class ERPSystemConnectionDemoExtension(ExtensionBase):
    """Experimental extension to demonstrate the connection between an ERP system
    and a Skybrush server, in collaboration with Cascade Ltd
    """

    def __init__(self):
        super().__init__()

        self._stations = {}
        self._trips = defaultdict(Trip)
        self._command_queue_rx = self._command_queue_tx = None

    def configure(self, configuration):
        super().configure(configuration)
        self.configure_stations(configuration.get("stations"))

    def configure_stations(self, stations: Dict[str, Dict]):
        """Parses the list of stations from the configuration file so they
        can be added as docks later.
        """
        stations = stations or {}
        station_ids = sorted(stations.keys())
        self._stations = dict(
            (station_id, Station.from_json(stations[station_id], id=station_id))
            for station_id in station_ids
        )

        if self._stations:
            self.log.info(
                f"Loaded {len(self._stations)} stations.",
                extra={"semantics": "success"},
            )

    def generate_choreography_file_from_route(
        self, uav_id: str, velocity_xy: float = 4, velocity_z: float = 1, agl: float = 5
    ):
        """Generate a choreography file from a given route between stations."""
        return CHOREO_STR.format(
            agl=agl, velocity_xy=velocity_xy, velocity_z=velocity_z
        )

    def generate_mission_from_route(
        self, uav_id: str, velocity_xy: float = 4, velocity_z: float = 1, agl: float = 5
    ):
        """Generate a complete mission file as an in-memory .zip buffer
        for the given UAV with the given parameters."""
        # generate individual files to be contained in the zip file
        waypoint_ground_str = self.generate_waypoint_file_from_route(0)
        waypoint_str = self.generate_waypoint_file_from_route(
            uav_id, velocity_xy=velocity_xy, velocity_z=velocity_z, agl=agl
        )
        choreography_str = self.generate_choreography_file_from_route(
            uav_id, velocity_xy=velocity_xy, velocity_z=velocity_z, agl=agl
        )
        mission_str = self.generate_mission_file_from_route(uav_id)

        # create the zipfile and write content to it
        buffer = BytesIO()
        zip_archive = ZipFile(buffer, "w", ZIP_DEFLATED)
        zip_archive.writestr("waypoint.cfg", waypoint_str)
        zip_archive.writestr("waypoint_ground.cfg", waypoint_ground_str)
        zip_archive.writestr("choreography.cfg", choreography_str)
        zip_archive.writestr("mission.cfg", mission_str)
        zip_archive.writestr("_meta/version", META_VERSION_STR)
        zip_archive.writestr("_meta/name", META_NAME_STR)
        zip_archive.close()

        return buffer

    def generate_mission_file_from_route(self, uav_id: str):
        """Generate a mission file from a given route between stations."""
        return MISSION_STR

    def generate_waypoint_file_from_route(
        self, uav_id: str, velocity_xy: float = 4, velocity_z: float = 1, agl: float = 5
    ):
        """Generate a waypoint file from a given route between stations."""
        waypoint_str_parts = [WAYPOINT_INIT_STR]
        if uav_id:
            for name in self._trips[uav_id].route:
                pos = self._stations[name].position
                waypoint_str_parts.append(
                    WAYPOINT_STR.format(
                        station=name,
                        lat=pos.lat,
                        lon=pos.lon,
                        agl=agl,
                        velocity_xy=velocity_xy,
                        velocity_z=velocity_z,
                    )
                )

        return "".join(waypoint_str_parts)

    async def handle_trip_addition(self, message, sender, hub):
        """Handles the addition of a new trip to the list of scheduled trips."""
        uav_id = message.body.get("uavId")
        if not isinstance(uav_id, str):
            return hub.reject(message, "Missing UAV ID or it is not a string")

        start_time_ms = message.body.get("startTime")
        try:
            start_time_ms = int(start_time_ms)
        except Exception:
            pass
        if not isinstance(start_time_ms, int):
            return hub.reject(message, "Missing start time or it is not an integer")

        start_time_sec = start_time_ms / 1000
        if start_time_sec < time():
            return hub.reject(message, "Start time is in the past")

        route = message.body.get("route")
        if not isinstance(route, list) or not route:
            return hub.reject(message, "Route is not specified or is empty")

        if any(not isinstance(station, str) for station in route):
            return hub.reject(message, "Station names in route must be strings")

        self._trips[uav_id] = Trip(
            uav_id=uav_id, start_time=start_time_sec, route=route
        )

        await self._command_queue_tx.send(uav_id)

        self.log.info(
            f"Trip successfully received.", extra={"semantics": "success", "id": uav_id}
        )

        return hub.acknowledge(message)

    def handle_trip_cancellation(self, message, sender, hub):
        """Cancels the current trip on a given drone."""
        uav_id = message.body.get("uavId")
        if not isinstance(uav_id, str):
            return hub.reject(message, "Missing UAV ID or it is not a string")

        trip = self._trips.pop(uav_id, None)
        if trip is None:
            return hub.reject(message, "UAV has no scheduled trip")

        self.log.info(f"Trip cancelled.", extra={"semantics": "failure", "id": uav_id})

        return hub.acknowledge(message)

    async def manage_trips(self, queue):
        """Background task that waits for UAV IDs in a queue and then uploads
        the trip corresponding to the given UAV to the UAV itself.
        """
        async with queue:
            async for uav_id in queue:
                await self.upload_trip_to_uav(uav_id)

    async def run(self):
        handlers = {
            "X-TRIP-ADD": self.handle_trip_addition,
            "X-TRIP-CANCEL": self.handle_trip_cancellation,
        }

        docks = [station.create_dock() for station in self._stations.values()]

        with self.app.message_hub.use_message_handlers(handlers):
            with self.app.object_registry.use(*docks):
                self._command_queue_tx, self._command_queue_rx = open_memory_channel(32)
                async with self._command_queue_tx:
                    await self.manage_trips(self._command_queue_rx)

    async def upload_trip_to_uav(self, uav_id: str) -> None:
        """Uploads the current trip belonging to the given UAV if needed."""
        extra = {"id": uav_id}
        trip = self._trips.get(uav_id)
        if trip is None:
            self.log.warn(
                f"upload_trip_to_uav() called with no scheduled trip", extra=extra
            )
            return

        if trip.status != TripStatus.NEW:
            self.log.warn(
                f"Trip status is {trip.status!r}, this might be a bug?", extra=extra
            )
            return

        self.log.info(f"Uplading trip...", extra=extra)
        trip.status = TripStatus.UPLOADING
        try:
            await sleep(5)
        except Exception:
            self.log.exception(
                f"Unexpected error while uploading trip to UAV.", extra=extra
            )
            trip.status = TripStatus.ERROR
        else:
            extra["semantics"] = "success"
            self.log.info(f"Trip uploaded successfully.", extra=extra)
            trip.status = TripStatus.UPLOADED


construct = ERPSystemConnectionDemoExtension
dependencies = ("dock",)
