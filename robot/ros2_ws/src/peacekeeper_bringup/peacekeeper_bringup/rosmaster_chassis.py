import math
import threading
import time

import rclpy
from geometry_msgs.msg import TransformStamped, Twist
from nav_msgs.msg import Odometry
from rclpy.node import Node
from tf2_ros import TransformBroadcaster


class RosmasterChassis(Node):
    def __init__(self):
        super().__init__("peacekeeper_rosmaster_chassis")

        self.declare_parameter("port", "/dev/myserial")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("odom_topic", "/odom")
        self.declare_parameter("odom_frame", "odom")
        self.declare_parameter("base_frame", "base_link")
        self.declare_parameter("speed", 25)
        self.declare_parameter("deadband", 0.01)
        self.declare_parameter("command_timeout_s", 0.5)
        self.declare_parameter("max_linear_x", 0.45)
        self.declare_parameter("max_linear_y", 0.45)
        self.declare_parameter("max_angular_z", 1.4)
        self.declare_parameter("publish_odom", True)
        self.declare_parameter("publish_tf", True)

        self.port = str(self.get_parameter("port").value)
        self.cmd_vel_topic = str(self.get_parameter("cmd_vel_topic").value)
        self.odom_topic = str(self.get_parameter("odom_topic").value)
        self.odom_frame = str(self.get_parameter("odom_frame").value)
        self.base_frame = str(self.get_parameter("base_frame").value)
        self.speed = max(10, min(80, int(self.get_parameter("speed").value)))
        self.deadband = float(self.get_parameter("deadband").value)
        self.command_timeout_s = float(self.get_parameter("command_timeout_s").value)
        self.max_linear_x = float(self.get_parameter("max_linear_x").value)
        self.max_linear_y = float(self.get_parameter("max_linear_y").value)
        self.max_angular_z = float(self.get_parameter("max_angular_z").value)
        self.publish_odom_enabled = self._as_bool(self.get_parameter("publish_odom").value)
        self.publish_tf_enabled = self._as_bool(self.get_parameter("publish_tf").value)

        from Rosmaster_Lib import Rosmaster

        self.bot = Rosmaster(com=self.port, debug=False)
        self.lock = threading.RLock()
        self.current_cmd = Twist()
        self.deadline = None
        self.last_state = 0
        self.x = 0.0
        self.y = 0.0
        self.yaw = 0.0
        self.last_tick = time.monotonic()

        self.odom_pub = self.create_publisher(Odometry, self.odom_topic, 10)
        self.tf_broadcaster = TransformBroadcaster(self)
        self.sub = self.create_subscription(Twist, self.cmd_vel_topic, self.on_cmd_vel, 10)
        self.timer = self.create_timer(0.05, self.on_timer)

        self.stop_motors()
        self.get_logger().info(
            f"Rosmaster chassis ready: port={self.port}, cmd_vel={self.cmd_vel_topic}, "
            f"odom={self.odom_topic}, speed={self.speed}"
        )

    def on_cmd_vel(self, msg):
        clipped = Twist()
        clipped.linear.x = self._clip(msg.linear.x, self.max_linear_x)
        clipped.linear.y = self._clip(msg.linear.y, self.max_linear_y)
        clipped.angular.z = self._clip(msg.angular.z, self.max_angular_z)
        state = self._state_from_twist(clipped.linear.x, clipped.linear.y, clipped.angular.z)

        with self.lock:
            self.current_cmd = clipped
            self.deadline = time.monotonic() + self.command_timeout_s
            self.last_state = state
            if state == 0:
                self.stop_motors()
            else:
                self.bot.set_car_run(state, self.speed)

    def on_timer(self):
        now = time.monotonic()
        with self.lock:
            dt = max(0.0, min(now - self.last_tick, 0.2))
            self.last_tick = now
            if self.deadline is not None and now > self.deadline:
                self.current_cmd = Twist()
                self.deadline = None
                self.last_state = 0
                self.stop_motors()

            self._integrate_odom(dt)
            self._publish_odom_and_tf()

    def stop_motors(self):
        for func in (
            lambda: self.bot.set_car_run(0, 0),
            lambda: self.bot.set_car_motion(0, 0, 0),
            lambda: self.bot.set_motor(0, 0, 0, 0),
            lambda: self.bot.set_beep(0),
        ):
            try:
                func()
            except Exception:
                pass

    def destroy_node(self):
        try:
            self.stop_motors()
        finally:
            super().destroy_node()

    def _integrate_odom(self, dt):
        vx = float(self.current_cmd.linear.x)
        vy = float(self.current_cmd.linear.y)
        wz = float(self.current_cmd.angular.z)
        cos_yaw = math.cos(self.yaw)
        sin_yaw = math.sin(self.yaw)
        self.x += (vx * cos_yaw - vy * sin_yaw) * dt
        self.y += (vx * sin_yaw + vy * cos_yaw) * dt
        self.yaw = self._normalize_angle(self.yaw + wz * dt)

    def _publish_odom_and_tf(self):
        stamp = self.get_clock().now().to_msg()
        qz = math.sin(self.yaw * 0.5)
        qw = math.cos(self.yaw * 0.5)

        if self.publish_tf_enabled:
            transform = TransformStamped()
            transform.header.stamp = stamp
            transform.header.frame_id = self.odom_frame
            transform.child_frame_id = self.base_frame
            transform.transform.translation.x = self.x
            transform.transform.translation.y = self.y
            transform.transform.translation.z = 0.0
            transform.transform.rotation.z = qz
            transform.transform.rotation.w = qw
            self.tf_broadcaster.sendTransform(transform)

        if self.publish_odom_enabled:
            odom = Odometry()
            odom.header.stamp = stamp
            odom.header.frame_id = self.odom_frame
            odom.child_frame_id = self.base_frame
            odom.pose.pose.position.x = self.x
            odom.pose.pose.position.y = self.y
            odom.pose.pose.orientation.z = qz
            odom.pose.pose.orientation.w = qw
            odom.twist.twist.linear.x = float(self.current_cmd.linear.x)
            odom.twist.twist.linear.y = float(self.current_cmd.linear.y)
            odom.twist.twist.angular.z = float(self.current_cmd.angular.z)
            self.odom_pub.publish(odom)

    def _state_from_twist(self, linear_x, linear_y, angular_z):
        values = {
            "x": abs(linear_x),
            "y": abs(linear_y),
            "z": abs(angular_z),
        }
        if max(values.values()) < self.deadband:
            return 0
        dominant = max(values, key=values.get)
        if dominant == "z":
            return 5 if angular_z > 0 else 6
        if dominant == "y":
            return 3 if linear_y > 0 else 4
        return 1 if linear_x > 0 else 2

    @staticmethod
    def _clip(value, limit):
        return max(-limit, min(limit, float(value)))

    @staticmethod
    def _normalize_angle(value):
        while value > math.pi:
            value -= 2.0 * math.pi
        while value < -math.pi:
            value += 2.0 * math.pi
        return value

    @staticmethod
    def _as_bool(value):
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in ("1", "true", "yes", "on")


def main(args=None):
    rclpy.init(args=args)
    node = RosmasterChassis()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
