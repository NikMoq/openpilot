"""Optional Waze data acquisition, independent of controls and Android runtimes."""
from .backend import AcquisitionResult, WazeBackend

__all__ = ["AcquisitionResult", "WazeBackend"]
