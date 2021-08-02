"""Extension that connects to one or more data sources for RTK connections
and forwards the corrections to the UAVs managed by the server.
"""

from .extension import construct, dependencies, description, optional_dependencies

__all__ = ("construct", "dependencies", "description", "optional_dependencies")
