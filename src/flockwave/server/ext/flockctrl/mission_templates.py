"""String templates to be used for parametrized mission file generation for
the flockctrl system.
"""

__all__ = ("gps_coordinate_to_string", "CHOREOGRAPHY_FILE_TEMPLATE",
    "MISSION_FILE_TEMPLATE", "WAYPOINT_FILE_TEMPLATE")

CHOREOGRAPHY_FILE_TEMPLATE = """# this is a choreography file generated by flockwave-server

[sequence]

wait_on_ground=waypoint
go_on_route=waypoint

[wait_on_ground]
waypoint.file=waypoints_ground.cfg
waypoint.whats_next=loiter

[go_on_route]
waypoint.file=waypoints.cfg
waypoint.minimum_altitude=3
waypoint.altitude_setpoint={altitude_setpoint:.2f}
waypoint.velocity_xy={velocity_xy:.2f}
waypoint.velocity_z={velocity_z:.2f}
waypoint.velocity_threshold=6
waypoint.manual_override_rp=shift
waypoint.manual_override_gas=shift
waypoint.whats_next=land
"""

MISSION_FILE_TEMPLATE = """# this is a mission file generated by flockwave-server

[settings]
choreography_file=choreography.cfg

# TODO: add all settings that are needed for shows but are not default in flockctrl.cfg
"""

WAYPOINT_FILE_TEMPLATE = """# this is a waypoint file generated by flockwave-server

[init]
angle={angle}
ground_altitude={ground_altitude}
origin={origin}

[waypoints]
{waypoints}
"""

def gps_coordinate_to_string(lat, lon):
    """Return a string to be used in waypoint files when absolute coordinates
    are needed.

    Parameters:
        lat(float) - latitude in [deg]
        lon(float) - longitude in [deg]

    Return:
        gps coordinate string in flockctrl format
    """
    NS = 'N' if lat >= 0 else 'S'
    EW = 'E' if lon >= 0 else 'W'

    return "{}{:.8f} {}{:.8f}".format(NS, lat, EW, lon)