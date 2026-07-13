"""Estimate target distance from depth images or lidar for vision detections."""
from __future__ import annotations

import math
import struct
import threading
import time
import uuid
from dataclasses import dataclass
from importlib import import_module
from statistics import median
from typing import Any, Optional, Sequence

from .ros_control import _source_ros_environment

rclpy = None
Image = None
LaserScan = None
SingleThreadedExecutor = None
Buffer = None
TransformListener = None
_ros_import_error: Optional[str] = None
_ros_environment_loaded = False


def _ensure_ros_perception(setup_paths=()) -> bool:
    global rclpy, Image, LaserScan, SingleThreadedExecutor, Buffer, TransformListener, _ros_import_error, _ros_environment_loaded
    if (
        rclpy is not None
        and Image is not None
        and LaserScan is not None
        and SingleThreadedExecutor is not None
    ):
        return True
    if _ros_import_error is not None and _ros_environment_loaded:
        return False
    try:
        if not _ros_environment_loaded:
            _source_ros_environment(setup_paths)
            _ros_environment_loaded = True
        rclpy = import_module("rclpy")
        sensor_msgs = import_module("sensor_msgs.msg")
        executors = import_module("rclpy.executors")
        tf2_ros = import_module("tf2_ros")
        Image = sensor_msgs.Image
        LaserScan = sensor_msgs.LaserScan
        SingleThreadedExecutor = executors.SingleThreadedExecutor
        Buffer = tf2_ros.Buffer
        TransformListener = tf2_ros.TransformListener
        _ros_import_error = None
        return True
    except Exception as exc:  # pragma: no cover - depends on ROS runtime.
        _ros_import_error = str(exc)
        rclpy = None
        Image = None
        LaserScan = None
        SingleThreadedExecutor = None
        return False


@dataclass(frozen=True)
class DepthFrame:
    width: int
    height: int
    encoding: str
    step: int
    is_bigendian: bool
    data: bytes
    received_at: float
    source_timestamp: Optional[float] = None


@dataclass(frozen=True)
class ScanFrame:
    angle_min: float
    angle_increment: float
    range_min: float
    range_max: float
    ranges: tuple[float, ...]
    received_at: float
    source_timestamp: Optional[float] = None


class TargetRangeEstimator:
    """Best-effort target range lookup for one captured vision frame.

    The estimator prefers the depth camera when a recent depth frame is
    available. Otherwise it falls back to lidar by projecting the detection
    center into the scan plane with a fixed horizontal field-of-view model.
    """

    DEPTH_STALE_AFTER_S = 2.0
    SCAN_STALE_AFTER_S = 1.5
    DEFAULT_LIDAR_ANGLE_CANDIDATES_DEG = (0.0, -90.0, 90.0, -120.0, 120.0, -150.0, 150.0, -180.0, 180.0)

    def __init__(
        self,
        scan_topic: str,
        setup_paths=None,
        depth_topic: str = "/camera/depth/image_raw",
        camera_horizontal_fov_deg: float = 60.0,
        lidar_angle_offset_deg: float = 0.0,
        lidar_angle_candidates_deg: Optional[Sequence[float]] = None,
        map_frame: str = "map",
        base_frame: str = "base_link",
        laser_frame: str = "laser",
        camera_frame: str = "camera_link",
    ):
        self.scan_topic = scan_topic
        self.depth_topic = depth_topic
        self.setup_paths = tuple(setup_paths or ())
        self.camera_horizontal_fov_deg = float(camera_horizontal_fov_deg)
        self.lidar_angle_offset_deg = float(lidar_angle_offset_deg)
        self.lidar_angle_candidates_deg = tuple(
            float(value)
            for value in (lidar_angle_candidates_deg or self.DEFAULT_LIDAR_ANGLE_CANDIDATES_DEG)
        )
        self.map_frame = map_frame
        self.base_frame = base_frame
        self.laser_frame = laser_frame
        self.camera_frame = camera_frame
        self.node = None
        self.executor = None
        self.depth_subscription = None
        self.scan_subscription = None
        self.tf_buffer = None
        self.tf_listener = None
        self._spin_thread = None
        self._lock = threading.RLock()
        self._depth: Optional[DepthFrame] = None
        self._scan: Optional[ScanFrame] = None
        self.last_error: Optional[str] = None

    @property
    def available(self) -> bool:
        return _ensure_ros_perception(self.setup_paths)

    def start(self) -> None:
        if not self.available:
            raise RuntimeError(f"ROS2 perception modules are not available: {_ros_import_error}")
        with self._lock:
            if self.node is not None:
                return
            if not rclpy.ok():
                rclpy.init(args=None)
            self.node = rclpy.create_node("peacekeeper_target_range_estimator")
            self.executor = SingleThreadedExecutor()
            self.executor.add_node(self.node)
            self.tf_buffer = Buffer()
            self.tf_listener = TransformListener(self.tf_buffer, self.node, spin_thread=False)
            self.depth_subscription = self.node.create_subscription(
                Image,
                self.depth_topic,
                self._on_depth_image,
                10,
            )
            self.scan_subscription = self.node.create_subscription(
                LaserScan,
                self.scan_topic,
                self._on_scan,
                10,
            )
            self._spin_thread = threading.Thread(target=self._spin, daemon=True)
            self._spin_thread.start()

    def shutdown(self) -> None:
        with self._lock:
            node = self.node
            executor = self.executor
            self.node = None
            self.executor = None
            self.depth_subscription = None
            self.scan_subscription = None
            self.tf_listener = None
            self.tf_buffer = None
            self._depth = None
            self._scan = None
        if executor is not None:
            try:
                executor.shutdown()
            except Exception:
                pass
        if node is not None:
            try:
                node.destroy_node()
            except Exception:
                pass

    def status(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            depth = self._depth
            scan = self._scan
            depth_age = None if depth is None else max(0.0, now - depth.received_at)
            scan_age = None if scan is None else max(0.0, now - scan.received_at)
            return {
                "available": self.available,
                "ros_import_error": _ros_import_error,
                "ready": self.node is not None,
                "depth_topic": self.depth_topic,
                "depth_available": depth is not None and depth_age is not None and depth_age <= self.DEPTH_STALE_AFTER_S,
                "depth_age_s": depth_age,
                "scan_topic": self.scan_topic,
                "scan_available": scan is not None and scan_age is not None and scan_age <= self.SCAN_STALE_AFTER_S,
                "scan_age_s": scan_age,
                "last_error": self.last_error,
                "spin_thread_alive": self._spin_thread is not None and self._spin_thread.is_alive(),
            }

    def enrich(self, payload: dict[str, Any]) -> dict[str, Any]:
        detections = payload.get("detections")
        width = int(payload.get("image_width") or 0)
        height = int(payload.get("image_height") or 0)
        if not isinstance(detections, list) or width <= 0 or height <= 0:
            return dict(payload)

        enriched = dict(payload)
        enriched["observation_id"] = str(enriched.get("observation_id") or uuid.uuid4())
        next_detections = []
        measured_count = 0
        used_sources: set[str] = set()
        for raw_detection in detections:
            detection = dict(raw_detection) if isinstance(raw_detection, dict) else {}
            bbox = detection.get("bbox") or {}
            center_x = float(bbox.get("x") or 0.0) + float(bbox.get("width") or 0.0) / 2.0
            center_y = float(bbox.get("y") or 0.0) + float(bbox.get("height") or 0.0) / 2.0
            bearing_rad = self._bearing_from_pixel(center_x, width)
            measurement = self._measure_range(center_x, center_y, width, height, bbox, bearing_rad)
            range_m = measurement.get("range_m")
            source = measurement.get("source")
            detection["bearing_deg"] = round(math.degrees(bearing_rad), 1)
            detection["range_m"] = round(range_m, 3) if range_m is not None else None
            detection["range_source"] = source
            detection["range_quality"] = measurement.get("quality")
            detection["measurement_age_ms"] = measurement.get("age_ms")
            detection["measurement_timestamp"] = measurement.get("timestamp")
            detection["measurement_angle_deg"] = measurement.get("angle_deg")
            robot_pose, target_pose = self._map_poses(measurement, bearing_rad)
            detection["robot_pose_map"] = robot_pose
            detection["target_pose_map"] = target_pose
            detection["localization_valid"] = target_pose is not None
            if range_m is not None:
                measured_count += 1
            if source:
                used_sources.add(source)
            next_detections.append(detection)

        enriched["detections"] = next_detections
        enriched["range_measurements"] = {
            "measured_count": measured_count,
            "total_count": len(next_detections),
            "sources": sorted(used_sources),
        }
        return enriched

    def _measure_range(
        self,
        center_x: float,
        center_y: float,
        image_width: int,
        image_height: int,
        bbox: dict[str, Any],
        bearing_rad: float,
    ) -> dict[str, Any]:
        depth = self._measure_depth(center_x, center_y, image_width, image_height)
        if depth is not None:
            return depth
        lidar = self._measure_lidar(center_x, image_width, bbox, bearing_rad)
        if lidar is not None:
            return lidar
        return {"range_m": None, "source": None, "quality": None, "age_ms": None, "angle_deg": None, "timestamp": None}

    def _measure_depth(
        self,
        center_x: float,
        center_y: float,
        image_width: int,
        image_height: int,
    ) -> Optional[dict[str, Any]]:
        with self._lock:
            depth = self._depth
        if depth is None or time.time() - depth.received_at > self.DEPTH_STALE_AFTER_S:
            return None
        if depth.width <= 0 or depth.height <= 0:
            return None
        scale_x = depth.width / max(image_width, 1)
        scale_y = depth.height / max(image_height, 1)
        px = int(round(center_x * scale_x))
        py = int(round(center_y * scale_y))
        offsets = [(0, 0), (-3, -3), (-3, 3), (3, -3), (3, 3)]
        values = [
            value
            for value in (
                self._read_depth_value(depth, px + dx, py + dy)
                for dx, dy in offsets
            )
            if value is not None and 0.04 < value < 80.0
        ]
        if not values:
            return None
        return {
            "range_m": float(median(values)),
            "source": "depth_camera",
            "quality": "calibrated",
            "age_ms": round(max(0.0, time.time() - depth.received_at) * 1000.0, 1),
            "angle_deg": None,
            "timestamp": depth.source_timestamp or depth.received_at,
            "frame": self.camera_frame,
        }

    def _measure_lidar(
        self,
        center_x: float,
        image_width: int,
        bbox: dict[str, Any],
        bearing_rad: float,
    ) -> Optional[dict[str, Any]]:
        del center_x
        with self._lock:
            scan = self._scan
        if scan is None or time.time() - scan.received_at > self.SCAN_STALE_AFTER_S:
            return None
        if not scan.ranges or abs(scan.angle_increment) < 1e-9:
            return None

        bbox_width = max(0.0, float(bbox.get("width") or 0.0))
        angular_width_deg = max(2.0, min(10.0, (bbox_width / max(image_width, 1)) * self.camera_horizontal_fov_deg + 1.0))
        candidates: list[tuple[float, float, int]] = []
        preferred: Optional[tuple[float, float, int]] = None
        for offset_deg in self._iter_lidar_offsets():
            sampled = self._sample_lidar_window(
                scan,
                bearing_rad + math.radians(offset_deg),
                angular_width_deg,
            )
            if sampled is None:
                continue
            candidate = (offset_deg, sampled[0], sampled[1])
            candidates.append(candidate)
            if math.isclose(offset_deg, self.lidar_angle_offset_deg, abs_tol=1e-6):
                preferred = candidate
        if not candidates:
            return None
        # Respect an explicit non-zero offset when it works; otherwise auto-pick
        # the nearest valid obstacle among the known mounting candidates.
        if preferred is not None and not math.isclose(self.lidar_angle_offset_deg, 0.0, abs_tol=1e-6):
            best = preferred
        else:
            best = min(candidates, key=lambda item: (item[1], -item[2], abs(item[0] - self.lidar_angle_offset_deg)))
        selected_angle = bearing_rad + math.radians(best[0])
        calibrated = math.isclose(best[0], self.lidar_angle_offset_deg, abs_tol=1e-6)
        return {
            "range_m": best[1],
            "source": "lidar",
            "quality": "calibrated" if calibrated else "fallback",
            "age_ms": round(max(0.0, time.time() - scan.received_at) * 1000.0, 1),
            "angle_deg": round(math.degrees(selected_angle), 2),
            "timestamp": scan.source_timestamp or scan.received_at,
            "frame": self.laser_frame,
        }

    def _on_depth_image(self, msg) -> None:
        try:
            frame = DepthFrame(
                width=int(msg.width),
                height=int(msg.height),
                encoding=str(msg.encoding),
                step=int(msg.step),
                is_bigendian=bool(msg.is_bigendian),
                data=bytes(msg.data),
                received_at=time.time(),
                source_timestamp=self._message_timestamp(msg),
            )
            with self._lock:
                self._depth = frame
                self.last_error = None
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)

    def _on_scan(self, msg) -> None:
        try:
            frame = ScanFrame(
                angle_min=float(msg.angle_min),
                angle_increment=float(msg.angle_increment),
                range_min=float(msg.range_min),
                range_max=float(msg.range_max),
                ranges=tuple(float(value) for value in msg.ranges),
                received_at=time.time(),
                source_timestamp=self._message_timestamp(msg),
            )
            with self._lock:
                self._scan = frame
                self.last_error = None
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)

    def _spin(self) -> None:
        try:
            executor = self.executor
            if executor is not None:
                executor.spin()
        except Exception as exc:
            with self._lock:
                self.last_error = str(exc)

    def _read_depth_value(self, frame: DepthFrame, x: int, y: int) -> Optional[float]:
        if x < 0 or y < 0 or x >= frame.width or y >= frame.height:
            return None
        if frame.encoding in ("16UC1", "mono16", "16SC1"):
            offset = y * frame.step + x * 2
            raw = frame.data[offset:offset + 2]
            if len(raw) != 2:
                return None
            value = int.from_bytes(raw, byteorder="big" if frame.is_bigendian else "little", signed=False)
            return value / 1000.0
        if frame.encoding == "32FC1":
            offset = y * frame.step + x * 4
            raw = frame.data[offset:offset + 4]
            if len(raw) != 4:
                return None
            return float(struct.unpack(">f" if frame.is_bigendian else "<f", raw)[0])
        return None

    def _iter_lidar_offsets(self) -> tuple[float, ...]:
        values = [self.lidar_angle_offset_deg, *self.lidar_angle_candidates_deg]
        ordered: list[float] = []
        for value in values:
            if any(math.isclose(value, existing, abs_tol=1e-6) for existing in ordered):
                continue
            ordered.append(float(value))
        return tuple(ordered)

    def _sample_lidar_window(
        self,
        scan: ScanFrame,
        target_angle: float,
        angular_width_deg: float,
    ) -> Optional[tuple[float, int]]:
        half_window_rad = math.radians(angular_width_deg / 2.0)
        normalized_target = self._normalize_scan_angle(scan, target_angle)
        min_angle = normalized_target - half_window_rad
        max_angle = normalized_target + half_window_rad
        start_index = int(math.floor((min_angle - scan.angle_min) / scan.angle_increment))
        end_index = int(math.ceil((max_angle - scan.angle_min) / scan.angle_increment))
        values = []
        for index in range(start_index, end_index + 1):
            if index < 0 or index >= len(scan.ranges):
                continue
            value = float(scan.ranges[index])
            if not math.isfinite(value):
                continue
            if value < max(scan.range_min, 0.04) or value > scan.range_max:
                continue
            values.append(value)
        if not values:
            return None
        return float(median(values)), len(values)

    @staticmethod
    def _normalize_scan_angle(scan: ScanFrame, target_angle: float) -> float:
        max_angle = scan.angle_min + scan.angle_increment * max(len(scan.ranges) - 1, 0)
        full_turn = math.tau
        normalized = float(target_angle)
        while normalized < scan.angle_min:
            normalized += full_turn
        while normalized > max_angle:
            normalized -= full_turn
        return normalized

    def _bearing_from_pixel(self, center_x: float, image_width: int) -> float:
        normalized = (center_x / max(image_width, 1)) - 0.5
        # In the image plane, smaller x means "left of center". In ROS LaserScan
        # convention, left side is a positive bearing (CCW), so the image sign
        # must be flipped before projecting into scan angles.
        return -normalized * math.radians(self.camera_horizontal_fov_deg)

    @staticmethod
    def _message_timestamp(msg) -> Optional[float]:
        stamp = getattr(getattr(msg, "header", None), "stamp", None)
        if stamp is None:
            return None
        value = float(getattr(stamp, "sec", 0)) + float(getattr(stamp, "nanosec", 0)) / 1_000_000_000.0
        return value if value > 0 else None

    def _map_poses(self, measurement: dict[str, Any], camera_bearing_rad: float) -> tuple[Optional[dict], Optional[dict]]:
        robot_transform = self._lookup_transform(self.base_frame, measurement.get("timestamp"))
        robot_pose = self._pose_from_transform(robot_transform) if robot_transform is not None else None
        distance = measurement.get("range_m")
        if distance is None:
            return robot_pose, None
        source_frame = measurement.get("frame") or self.base_frame
        source_transform = self._lookup_transform(source_frame, measurement.get("timestamp"))
        if source_transform is None:
            return robot_pose, None
        angle = camera_bearing_rad
        if source_frame == self.laser_frame and measurement.get("angle_deg") is not None:
            angle = math.radians(float(measurement["angle_deg"]))
        yaw = self._yaw(source_transform.transform.rotation)
        local_x = float(distance) * math.cos(angle)
        local_y = float(distance) * math.sin(angle)
        tx = float(source_transform.transform.translation.x)
        ty = float(source_transform.transform.translation.y)
        return robot_pose, {
            "x": round(tx + local_x * math.cos(yaw) - local_y * math.sin(yaw), 3),
            "y": round(ty + local_x * math.sin(yaw) + local_y * math.cos(yaw), 3),
        }

    def _lookup_transform(self, source_frame: str, timestamp: Optional[float]):
        buffer = self.tf_buffer
        if buffer is None or rclpy is None:
            return None
        try:
            if timestamp:
                stamp = rclpy.time.Time(seconds=float(timestamp))
            else:
                stamp = rclpy.time.Time()
            return buffer.lookup_transform(self.map_frame, source_frame, stamp)
        except Exception as exc:
            self.last_error = str(exc)
            return None

    @classmethod
    def _pose_from_transform(cls, transform) -> dict[str, float]:
        return {
            "x": round(float(transform.transform.translation.x), 3),
            "y": round(float(transform.transform.translation.y), 3),
            "yaw": round(cls._yaw(transform.transform.rotation), 4),
        }

    @staticmethod
    def _yaw(rotation) -> float:
        x = float(rotation.x)
        y = float(rotation.y)
        z = float(rotation.z)
        w = float(rotation.w)
        return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
