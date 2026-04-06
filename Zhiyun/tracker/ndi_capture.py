"""
NDI video capture using libndi via ctypes.

Finds NDI sources on the network and captures frames.
Multiple clients can receive the same NDI stream simultaneously.
"""
import ctypes
import ctypes.util
import time
import numpy as np

# Load NDI library
_ndi_path = ctypes.util.find_library('ndi')
if not _ndi_path:
    raise ImportError("libndi not found. Install NDI SDK: brew install libndi")
_ndi = ctypes.CDLL(_ndi_path)


# --- NDI Structs ---
class NDIlib_find_create_t(ctypes.Structure):
    _fields_ = [
        ("show_local_sources", ctypes.c_bool),
        ("p_groups", ctypes.c_char_p),
        ("p_extra_ips", ctypes.c_char_p),
    ]

class NDIlib_source_t(ctypes.Structure):
    _fields_ = [
        ("p_ndi_name", ctypes.c_char_p),
        ("p_url_address", ctypes.c_char_p),
    ]

class NDIlib_recv_create_v3_t(ctypes.Structure):
    _fields_ = [
        ("source_to_connect_to", NDIlib_source_t),
        ("color_format", ctypes.c_int),
        ("bandwidth", ctypes.c_int),
        ("allow_video_fields", ctypes.c_bool),
        ("p_ndi_recv_name", ctypes.c_char_p),
    ]

class NDIlib_video_frame_v2_t(ctypes.Structure):
    _fields_ = [
        ("xres", ctypes.c_int),
        ("yres", ctypes.c_int),
        ("FourCC", ctypes.c_int),
        ("frame_rate_N", ctypes.c_int),
        ("frame_rate_D", ctypes.c_int),
        ("picture_aspect_ratio", ctypes.c_float),
        ("frame_format_type", ctypes.c_int),
        ("timecode", ctypes.c_int64),
        ("p_data", ctypes.c_void_p),
        ("line_stride_in_bytes", ctypes.c_int),
        ("p_metadata", ctypes.c_char_p),
        ("timestamp", ctypes.c_int64),
    ]

# NDI constants
NDILIB_RECV_COLOR_FORMAT_BGRX_BGRA = 0
NDILIB_RECV_COLOR_FORMAT_UYVY_BGRA = 1
NDILIB_RECV_COLOR_FORMAT_RGBX_RGBA = 2
NDILIB_RECV_COLOR_FORMAT_UYVY_RGBA = 3
NDILIB_RECV_BANDWIDTH_HIGHEST = 0
NDILIB_RECV_BANDWIDTH_LOWEST = 1
NDILIB_FRAME_TYPE_NONE = 0
NDILIB_FRAME_TYPE_VIDEO = 1
NDILIB_FRAME_TYPE_AUDIO = 2

# --- NDI Function signatures ---
_ndi.NDIlib_initialize.restype = ctypes.c_bool
_ndi.NDIlib_find_create_v2.restype = ctypes.c_void_p
_ndi.NDIlib_find_create_v2.argtypes = [ctypes.POINTER(NDIlib_find_create_t)]
_ndi.NDIlib_find_wait_for_sources.restype = ctypes.c_bool
_ndi.NDIlib_find_wait_for_sources.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
_ndi.NDIlib_find_get_current_sources.restype = ctypes.POINTER(NDIlib_source_t)
_ndi.NDIlib_find_get_current_sources.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
_ndi.NDIlib_recv_create_v3.restype = ctypes.c_void_p
_ndi.NDIlib_recv_create_v3.argtypes = [ctypes.POINTER(NDIlib_recv_create_v3_t)]
_ndi.NDIlib_recv_capture_v2.restype = ctypes.c_int
_ndi.NDIlib_recv_capture_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(NDIlib_video_frame_v2_t), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32]
_ndi.NDIlib_recv_free_video_v2.argtypes = [ctypes.c_void_p, ctypes.POINTER(NDIlib_video_frame_v2_t)]
_ndi.NDIlib_recv_destroy.argtypes = [ctypes.c_void_p]
_ndi.NDIlib_find_destroy.argtypes = [ctypes.c_void_p]

# Initialize NDI
if not _ndi.NDIlib_initialize():
    raise RuntimeError("Failed to initialize NDI")


def find_sources(timeout_ms=5000):
    """Find all NDI sources on the network. Returns list of (name, url) tuples."""
    find_create = NDIlib_find_create_t(True, None, None)
    finder = _ndi.NDIlib_find_create_v2(ctypes.byref(find_create))
    if not finder:
        return []

    _ndi.NDIlib_find_wait_for_sources(finder, timeout_ms)

    num = ctypes.c_uint32(0)
    sources = _ndi.NDIlib_find_get_current_sources(finder, ctypes.byref(num))

    result = []
    for i in range(num.value):
        name = sources[i].p_ndi_name.decode('utf-8') if sources[i].p_ndi_name else ''
        url = sources[i].p_url_address.decode('utf-8') if sources[i].p_url_address else ''
        result.append((name, url))

    _ndi.NDIlib_find_destroy(finder)
    return result


class NDICapture:
    """Capture video frames from an NDI source."""

    def __init__(self, source_name=None, low_bandwidth=True):
        """
        source_name: NDI source name (e.g. "IPHONE (NDI HX Camera)")
                     If None, uses first available source.
        low_bandwidth: Use low bandwidth mode (lower quality but less CPU)
        """
        self.source_name = source_name
        self.low_bandwidth = low_bandwidth
        self._recv = None
        self._connected = False

    @property
    def connected(self):
        return self._connected

    def connect(self, timeout_ms=5000):
        """Find and connect to NDI source. Returns True on success."""
        sources = find_sources(timeout_ms)
        if not sources:
            print("[NDI] No sources found")
            return False

        # Find matching source
        target = None
        for name, url in sources:
            print(f"[NDI] Found: {name}")
            if self.source_name is None or self.source_name.lower() in name.lower():
                target = (name, url)
                break

        if not target:
            print(f"[NDI] Source '{self.source_name}' not found")
            return False

        name, url = target
        print(f"[NDI] Connecting to: {name}")

        source = NDIlib_source_t(name.encode('utf-8'), url.encode('utf-8'))
        bw = NDILIB_RECV_BANDWIDTH_LOWEST if self.low_bandwidth else NDILIB_RECV_BANDWIDTH_HIGHEST
        recv_create = NDIlib_recv_create_v3_t(
            source, NDILIB_RECV_COLOR_FORMAT_BGRX_BGRA, bw, True, b"gimbal-tracker"
        )
        self._recv = _ndi.NDIlib_recv_create_v3(ctypes.byref(recv_create))
        self._connected = self._recv is not None and self._recv != 0
        return self._connected

    def capture_frame(self, timeout_ms=100):
        """Capture a single video frame. Returns numpy BGR array or None."""
        if not self._connected:
            return None

        video_frame = NDIlib_video_frame_v2_t()
        frame_type = _ndi.NDIlib_recv_capture_v2(
            self._recv, ctypes.byref(video_frame), None, None, timeout_ms
        )

        if frame_type != NDILIB_FRAME_TYPE_VIDEO:
            return None

        w, h = video_frame.xres, video_frame.yres
        stride = video_frame.line_stride_in_bytes

        # Copy frame data to numpy array (BGRX format → BGR)
        buf = (ctypes.c_uint8 * (h * stride)).from_address(video_frame.p_data)
        frame = np.frombuffer(buf, dtype=np.uint8).reshape((h, stride // 4, 4))
        frame = frame[:, :w, :3].copy()  # BGRX → BGR, remove padding

        _ndi.NDIlib_recv_free_video_v2(self._recv, ctypes.byref(video_frame))
        return frame

    def disconnect(self):
        if self._recv:
            _ndi.NDIlib_recv_destroy(self._recv)
            self._recv = None
            self._connected = False
