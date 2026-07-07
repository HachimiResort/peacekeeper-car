import select
import sys
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist
from rclpy.node import Node


HELP = """
W/S forward/backward
A/D left/right strafe
Q/E spin left/right
SPACE/X stop
+/- speed
Ctrl-C exit and stop
"""


class TeleopCmdVel(Node):
    def __init__(self):
        super().__init__("peacekeeper_teleop_cmd_vel")
        self.declare_parameter("topic", "/cmd_vel")
        self.declare_parameter("rate_hz", 5.0)
        self.declare_parameter("linear_speed", 0.2)
        self.declare_parameter("angular_speed", 0.6)

        self.topic = str(self.get_parameter("topic").value)
        self.rate_hz = float(self.get_parameter("rate_hz").value)
        self.linear_speed = float(self.get_parameter("linear_speed").value)
        self.angular_speed = float(self.get_parameter("angular_speed").value)
        self.publisher = self.create_publisher(Twist, self.topic, 10)
        self.active_key = " "
        self.last_msg = Twist()

    def set_key(self, key):
        self.active_key = key
        self.last_msg = self.twist_for_key(key)

    def publish_once(self):
        self.publisher.publish(self.last_msg)

    def stop(self):
        self.last_msg = Twist()
        self.publisher.publish(self.last_msg)
        self.active_key = " "

    def twist_for_key(self, key):
        msg = Twist()
        if key == "w":
            msg.linear.x = self.linear_speed
        elif key == "s":
            msg.linear.x = -self.linear_speed
        elif key == "a":
            msg.linear.y = self.linear_speed
        elif key == "d":
            msg.linear.y = -self.linear_speed
        elif key == "q":
            msg.angular.z = self.angular_speed
        elif key == "e":
            msg.angular.z = -self.angular_speed
        return msg

    def speed_up(self):
        self.linear_speed = min(0.6, self.linear_speed + 0.05)
        self.angular_speed = min(1.5, self.angular_speed + 0.1)
        self.set_key(self.active_key)

    def speed_down(self):
        self.linear_speed = max(0.05, self.linear_speed - 0.05)
        self.angular_speed = max(0.2, self.angular_speed - 0.1)
        self.set_key(self.active_key)


def read_key(timeout):
    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    return sys.stdin.read(1).lower() if ready else None


def main(args=None):
    rclpy.init(args=args)
    node = TeleopCmdVel()
    old = termios.tcgetattr(sys.stdin)
    period = 1.0 / max(node.rate_hz, 1.0)
    last_publish = 0.0

    print(HELP)
    print(f"Publishing {node.topic} at {node.rate_hz:.1f} Hz")
    try:
        tty.setcbreak(sys.stdin.fileno())
        node.stop()
        while rclpy.ok():
            key = read_key(0.02)
            if key:
                if key in ("w", "s", "a", "d", "q", "e"):
                    node.set_key(key)
                elif key in (" ", "x", "0"):
                    node.stop()
                elif key == "+":
                    node.speed_up()
                elif key == "-":
                    node.speed_down()
                print(
                    f"\rkey={node.active_key!r} linear={node.linear_speed:.2f} angular={node.angular_speed:.2f}   ",
                    end="",
                    flush=True,
                )

            now = time.monotonic()
            if now - last_publish >= period:
                node.publish_once()
                last_publish = now
            rclpy.spin_once(node, timeout_sec=0.0)
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        print("\nStopping...")
        node.stop()
        time.sleep(0.2)
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
