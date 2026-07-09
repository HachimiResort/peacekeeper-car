#!/usr/bin/env bash
set -euo pipefail

ROS_DISTRO_SETUP="${ROS_DISTRO_SETUP:-/opt/ros/foxy/setup.bash}"
ROS_WORKSPACE_SETUP="${ROS_WORKSPACE_SETUP:-/root/yahboomcar_ros2_ws/yahboomcar_ws/install/setup.bash}"
ROBOT_TYPE="${ROBOT_TYPE:-x3}"
BRINGUP_PACKAGE="${BRINGUP_PACKAGE:-yahboomcar_bringup}"
BRINGUP_LAUNCH="${BRINGUP_LAUNCH:-yahboomcar_bringup_X3_launch.py}"
BRINGUP_EXECUTABLE="${BRINGUP_EXECUTABLE:-Mcnamu_driver_X3}"
CMD_VEL_TOPIC="${CMD_VEL_TOPIC:-/cmd_vel}"
ODOM_TOPIC="${ODOM_TOPIC:-}"
TEST_PATTERN="${TEST_PATTERN:-turn360}"
FORWARD_SPEED="${FORWARD_SPEED:-0.20}"
BACKWARD_SPEED="${BACKWARD_SPEED:--0.20}"
TURN_SPEED="${TURN_SPEED:--0.60}"
FORWARD_SECONDS="${FORWARD_SECONDS:-3.0}"
TURN_SECONDS="${TURN_SECONDS:-10.5}"
SAMPLE_RATE_HZ="${SAMPLE_RATE_HZ:-10.0}"
SERIAL_DEVICE="${SERIAL_DEVICE:-/dev/myserial}"
BRINGUP_LOG="${BRINGUP_LOG:-/tmp/peacekeeper-official-bringup-odom.log}"

if pgrep -f "python3 .*agent.py" >/dev/null 2>&1 || pgrep -f "python .*agent.py" >/dev/null 2>&1; then
  echo "fleet-agent appears to be running. Stop it first so official bringup is the only /dev/myserial owner." >&2
  exit 2
fi
if pgrep -f "yahboomcar_bringup|Mcnamu_driver_X3|base_node_X3|ros2 launch .*yahboomcar" >/dev/null 2>&1; then
  echo "Existing Yahboom bringup processes are still running. Stop them before this test." >&2
  pgrep -af "yahboomcar_bringup|Mcnamu_driver_X3|base_node_X3|ros2 launch .*yahboomcar" >&2 || true
  exit 2
fi

if command -v fuser >/dev/null 2>&1 && fuser -s "$SERIAL_DEVICE"; then
  echo "$SERIAL_DEVICE is already opened by another process. Official bringup test would be invalid." >&2
  fuser -v "$SERIAL_DEVICE" >&2 || true
  exit 2
fi

if test -f "$ROS_DISTRO_SETUP"; then
  set +u
  # shellcheck disable=SC1090
  source "$ROS_DISTRO_SETUP"
  set -u
fi
if test -f "$ROS_WORKSPACE_SETUP"; then
  set +u
  # shellcheck disable=SC1090
  source "$ROS_WORKSPACE_SETUP"
  set -u
fi

if ! ros2 pkg prefix "$BRINGUP_PACKAGE" >/dev/null 2>&1; then
  echo "ROS package '$BRINGUP_PACKAGE' was not found after sourcing the ROS environment." >&2
  exit 3
fi

package_prefix="$(ros2 pkg prefix "$BRINGUP_PACKAGE")"
launch_file="$package_prefix/share/$BRINGUP_PACKAGE/launch/$BRINGUP_LAUNCH"

cleanup() {
  set +e
  if command -v ros2 >/dev/null 2>&1; then
    timeout 2 ros2 topic pub --once "$CMD_VEL_TOPIC" geometry_msgs/msg/Twist \
      "{linear: {x: 0.0, y: 0.0, z: 0.0}, angular: {x: 0.0, y: 0.0, z: 0.0}}" >/dev/null 2>&1 || true
  fi
  if test -n "${BRINGUP_PID:-}"; then
    kill -TERM "-$BRINGUP_PID" >/dev/null 2>&1 || kill "$BRINGUP_PID" >/dev/null 2>&1 || true
    sleep 1
    kill -KILL "-$BRINGUP_PID" >/dev/null 2>&1 || true
    wait "$BRINGUP_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

if test -f "$launch_file"; then
  echo "Starting official bringup: ros2 launch $BRINGUP_PACKAGE $BRINGUP_LAUNCH"
  : > "$BRINGUP_LOG"
  ROBOT_TYPE="$ROBOT_TYPE" setsid ros2 launch "$BRINGUP_PACKAGE" "$BRINGUP_LAUNCH" >"$BRINGUP_LOG" 2>&1 &
else
  echo "Launch file not found: $launch_file"
  echo "Starting official driver fallback: ros2 run $BRINGUP_PACKAGE $BRINGUP_EXECUTABLE"
  : > "$BRINGUP_LOG"
  setsid ros2 run "$BRINGUP_PACKAGE" "$BRINGUP_EXECUTABLE" >"$BRINGUP_LOG" 2>&1 &
fi
BRINGUP_PID="$!"
echo "Official bringup log: $BRINGUP_LOG"

echo "Waiting for official bringup topics..."
for _ in $(seq 1 30); do
  if grep -q "SerialException\\|multiple access on port\\|device disconnected" "$BRINGUP_LOG" 2>/dev/null; then
    echo "Official bringup serial receive thread failed before the test started:" >&2
    grep -n "SerialException\\|multiple access on port\\|device disconnected" "$BRINGUP_LOG" >&2 || true
    echo "Tail of official bringup log:" >&2
    tail -n 40 "$BRINGUP_LOG" >&2 || true
    exit 5
  fi
  if ros2 topic list 2>/dev/null | grep -qx "$CMD_VEL_TOPIC"; then
    break
  fi
  sleep 0.5
done

if test -z "$ODOM_TOPIC"; then
  topics="$(ros2 topic list 2>/dev/null || true)"
  if printf '%s\n' "$topics" | grep -qx "/odom"; then
    ODOM_TOPIC="/odom"
  elif printf '%s\n' "$topics" | grep -qx "/odom_raw"; then
    ODOM_TOPIC="/odom_raw"
  else
    ODOM_TOPIC="/odom"
  fi
fi

echo "Using CMD_VEL_TOPIC=$CMD_VEL_TOPIC ODOM_TOPIC=$ODOM_TOPIC TEST_PATTERN=$TEST_PATTERN"
echo "Current topic list:"
ros2 topic list | sort
echo "Topic info:"
ros2 topic info "$CMD_VEL_TOPIC" || true
ros2 topic info "$ODOM_TOPIC" || true
cmd_vel_info="$(ros2 topic info "$CMD_VEL_TOPIC" 2>/dev/null || true)"
odom_info="$(ros2 topic info "$ODOM_TOPIC" 2>/dev/null || true)"
cmd_vel_subscribers="$(printf '%s\n' "$cmd_vel_info" | awk -F': ' '/Subscription count/ {print $2}')"
odom_publishers="$(printf '%s\n' "$odom_info" | awk -F': ' '/Publisher count/ {print $2}')"
if test "${cmd_vel_subscribers:-0}" != "1" || test "${odom_publishers:-0}" != "1"; then
  echo "WARNING: expected one official /cmd_vel subscriber and one odom publisher, got subscribers=${cmd_vel_subscribers:-unknown}, odom_publishers=${odom_publishers:-unknown}." >&2
  echo "This usually means previous bringup nodes are still alive; the result may be invalid." >&2
fi

CMD_VEL_TOPIC="$CMD_VEL_TOPIC" \
ODOM_TOPIC="$ODOM_TOPIC" \
TEST_PATTERN="$TEST_PATTERN" \
FORWARD_SPEED="$FORWARD_SPEED" \
BACKWARD_SPEED="$BACKWARD_SPEED" \
TURN_SPEED="$TURN_SPEED" \
FORWARD_SECONDS="$FORWARD_SECONDS" \
TURN_SECONDS="$TURN_SECONDS" \
SAMPLE_RATE_HZ="$SAMPLE_RATE_HZ" \
BRINGUP_LOG="$BRINGUP_LOG" \
python3 - <<'PY'
import math
import os
import select
import sys
import time

import rclpy
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


def yaw_from_quaternion(q):
    siny_cosp = 2.0 * (q.w * q.z + q.x * q.y)
    cosy_cosp = 1.0 - 2.0 * (q.y * q.y + q.z * q.z)
    return math.atan2(siny_cosp, cosy_cosp)


def normalize_angle(value):
    while value > math.pi:
        value -= 2.0 * math.pi
    while value < -math.pi:
        value += 2.0 * math.pi
    return value


def wait_for_operator(prompt):
    tty_path = "/dev/tty"
    try:
        with open(tty_path, "r", encoding="utf-8") as tty:
            tty.readline()
            return
    except Exception:
        pass
    input(prompt)


def operator_pressed_enter():
    tty_path = "/dev/tty"
    try:
        if not hasattr(operator_pressed_enter, "_tty"):
            operator_pressed_enter._tty = open(tty_path, "r", encoding="utf-8")
        tty = operator_pressed_enter._tty
        readable, _, _ = select.select([tty], [], [], 0.0)
        if readable:
            tty.readline()
            return True
    except Exception:
        return False
    return False


def pose_tuple(msg):
    pose = msg.pose.pose
    return (
        float(pose.position.x),
        float(pose.position.y),
        yaw_from_quaternion(pose.orientation),
    )


def make_sequence(pattern):
    forward_speed = float(os.environ.get("FORWARD_SPEED", "0.20"))
    turn_speed = float(os.environ.get("TURN_SPEED", "-0.60"))
    forward_seconds = float(os.environ.get("FORWARD_SECONDS", "3.0"))
    turn_seconds = float(os.environ.get("TURN_SECONDS", "10.5"))
    if pattern == "forward":
        return [("forward", forward_seconds, forward_speed, 0.0)]
    if pattern == "square":
        sequence = []
        for index in range(4):
            sequence.append(("forward_%d" % (index + 1), forward_seconds, forward_speed, 0.0))
            sequence.append(("turn_%d" % (index + 1), abs(math.pi / 2.0 / turn_speed), 0.0, turn_speed))
        return sequence
    return [("turn360", turn_seconds, 0.0, turn_speed)]


class OdomProbe:
    def __init__(self):
        self.cmd_vel_topic = os.environ.get("CMD_VEL_TOPIC", "/cmd_vel")
        self.odom_topic = os.environ.get("ODOM_TOPIC", "/odom")
        self.pattern = os.environ.get("TEST_PATTERN", "turn360")
        self.rate_hz = float(os.environ.get("SAMPLE_RATE_HZ", "10.0"))
        self.samples = []
        self.last_odom = None
        self.node = rclpy.create_node("official_bringup_odom_probe")
        self.publisher = self.node.create_publisher(Twist, self.cmd_vel_topic, 10)
        self.subscription = self.node.create_subscription(Odometry, self.odom_topic, self.on_odom, 50)

    def on_odom(self, msg):
        pose = pose_tuple(msg)
        self.last_odom = pose
        self.samples.append((time.time(), pose))

    def publish_cmd(self, linear_x, angular_z):
        msg = Twist()
        msg.linear.x = float(linear_x)
        msg.angular.z = float(angular_z)
        self.publisher.publish(msg)

    def wait_for_odom(self, timeout_s=8.0):
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.1)
            if self.last_odom is not None:
                return True
        return False

    def run_segment(self, label, duration_s, linear_x, angular_z):
        print("segment %s duration=%.2fs linear_x=%.3f angular_z=%.3f" % (label, duration_s, linear_x, angular_z))
        deadline = time.time() + duration_s
        period = 1.0 / max(self.rate_hz, 1.0)
        while time.time() < deadline:
            self.publish_cmd(linear_x, angular_z)
            rclpy.spin_once(self.node, timeout_sec=period)
        self.publish_cmd(0.0, 0.0)
        for _ in range(5):
            rclpy.spin_once(self.node, timeout_sec=0.1)

    def run_until_enter(self, label, linear_x, angular_z):
        print("observed segment %s linear_x=%.3f angular_z=%.3f" % (label, linear_x, angular_z))
        print("Press Enter when the physical car has completed the observed motion.")

        period = 1.0 / max(self.rate_hz, 1.0)
        while True:
            self.publish_cmd(linear_x, angular_z)
            rclpy.spin_once(self.node, timeout_sec=period)
            if operator_pressed_enter():
                break
        self.publish_cmd(0.0, 0.0)
        for _ in range(10):
            rclpy.spin_once(self.node, timeout_sec=0.1)

    def wait_until_enter(self, label):
        print(label)
        print("Press Enter to continue...")
        wait_for_operator("Press Enter to continue...")
        for _ in range(10):
            rclpy.spin_once(self.node, timeout_sec=0.1)

    def run(self):
        print("waiting for odom on %s ..." % self.odom_topic)
        if not self.wait_for_odom():
            print("ERROR: no odom samples received")
            return 4
        print("cmd_vel_subscribers=%d" % self.publisher.get_subscription_count())
        deadline = time.time() + 5.0
        while self.publisher.get_subscription_count() == 0 and time.time() < deadline:
            rclpy.spin_once(self.node, timeout_sec=0.1)
        print("cmd_vel_subscribers_after_wait=%d" % self.publisher.get_subscription_count())
        if self.publisher.get_subscription_count() == 0:
            print("ERROR: no subscriber on %s; official driver may not be listening" % self.cmd_vel_topic)
            return 6
        if self.pattern == "manual_observed":
            self.wait_until_enter("Place the car at the physical start mark.")
            start = self.last_odom
            start_sample_count = len(self.samples)
            print("start x=%.3f y=%.3f yaw=%.1fdeg" % (start[0], start[1], math.degrees(start[2])))
            self.wait_until_enter("Move the car with any external/manual method, then return it to the same physical mark and heading.")
        elif self.pattern == "observed_turn":
            self.wait_until_enter("Point the car at a visible physical heading mark.")
            start = self.last_odom
            start_sample_count = len(self.samples)
            print("start x=%.3f y=%.3f yaw=%.1fdeg" % (start[0], start[1], math.degrees(start[2])))
            self.run_until_enter("observed_turn", 0.0, float(os.environ.get("TURN_SPEED", "-0.60")))
        elif self.pattern == "observed_out_back":
            self.wait_until_enter("Place the car at mark A, pointed at mark B.")
            start = self.last_odom
            start_sample_count = len(self.samples)
            print("start x=%.3f y=%.3f yaw=%.1fdeg" % (start[0], start[1], math.degrees(start[2])))
            self.run_until_enter("A_to_B", float(os.environ.get("FORWARD_SPEED", "0.20")), 0.0)
            midpoint = self.last_odom
            print("point B odom x=%.3f y=%.3f yaw=%.1fdeg" % (midpoint[0], midpoint[1], math.degrees(midpoint[2])))
            self.run_until_enter("B_to_A_reverse", float(os.environ.get("BACKWARD_SPEED", "-0.20")), 0.0)
        else:
            start = self.last_odom
            start_sample_count = len(self.samples)
            print("start x=%.3f y=%.3f yaw=%.1fdeg" % (start[0], start[1], math.degrees(start[2])))
            for segment in make_sequence(self.pattern):
                self.run_segment(*segment)
        for _ in range(10):
            self.publish_cmd(0.0, 0.0)
            rclpy.spin_once(self.node, timeout_sec=0.1)
        end = self.last_odom
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        dyaw = normalize_angle(end[2] - start[2])
        distance_error = math.hypot(dx, dy)
        print("end   x=%.3f y=%.3f yaw=%.1fdeg" % (end[0], end[1], math.degrees(end[2])))
        print("delta dx=%.3f dy=%.3f dist=%.3fm dyaw=%.1fdeg" % (dx, dy, distance_error, math.degrees(dyaw)))
        print("odom_samples=%d new_samples=%d" % (len(self.samples), len(self.samples) - start_sample_count))
        log_path = os.environ.get("BRINGUP_LOG", "")
        if log_path:
            try:
                with open(log_path, "r", encoding="utf-8", errors="ignore") as handle:
                    log_text = handle.read()
                if "SerialException" in log_text or "multiple access on port" in log_text:
                    print("WARNING: official bringup log contains a serial receive failure; this run is not valid")
            except Exception:
                pass
        return 0


def main():
    rclpy.init(args=None)
    probe = OdomProbe()
    try:
        return probe.run()
    finally:
        probe.publish_cmd(0.0, 0.0)
        probe.node.destroy_node()
        rclpy.shutdown()


raise SystemExit(main())
PY
