import json
import sys
import time

import rclpy
from rclpy.node import Node


class TopicWatchdog(Node):
    def __init__(self):
        super().__init__("peacekeeper_topic_watchdog")
        self.declare_parameter("cmd_vel_topic", "/cmd_vel")
        self.declare_parameter("scan_topic", "/scan")
        self.declare_parameter("map_topic", "/map")
        self.declare_parameter("period_s", 1.0)
        period = float(self.get_parameter("period_s").value)
        self.timer = self.create_timer(period, self.report)

    def report(self):
        topics = {
            "cmd_vel": self.get_parameter("cmd_vel_topic").value,
            "scan": self.get_parameter("scan_topic").value,
            "map": self.get_parameter("map_topic").value,
        }
        names_and_types = dict(self.get_topic_names_and_types())
        payload = {
            "time": time.time(),
            "topics": {
                key: {
                    "name": topic,
                    "present": topic in names_and_types,
                    "types": names_and_types.get(topic, []),
                }
                for key, topic in topics.items()
            },
        }
        print(json.dumps(payload, ensure_ascii=False), flush=True)


def main():
    rclpy.init(args=sys.argv)
    node = TopicWatchdog()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()
