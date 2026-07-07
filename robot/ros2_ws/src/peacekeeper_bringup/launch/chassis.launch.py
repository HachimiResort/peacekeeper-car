from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("port", default_value="/dev/myserial"),
        DeclareLaunchArgument("cmd_vel_topic", default_value="/cmd_vel"),
        DeclareLaunchArgument("odom_topic", default_value="/odom"),
        DeclareLaunchArgument("odom_frame", default_value="odom"),
        DeclareLaunchArgument("base_frame", default_value="base_link"),
        DeclareLaunchArgument("speed", default_value="25"),
        DeclareLaunchArgument("command_timeout_s", default_value="0.5"),
        Node(
            package="peacekeeper_bringup",
            executable="rosmaster_chassis",
            name="peacekeeper_chassis_driver",
            output="screen",
            parameters=[{
                "port": LaunchConfiguration("port"),
                "cmd_vel_topic": LaunchConfiguration("cmd_vel_topic"),
                "odom_topic": LaunchConfiguration("odom_topic"),
                "odom_frame": LaunchConfiguration("odom_frame"),
                "base_frame": LaunchConfiguration("base_frame"),
                "speed": LaunchConfiguration("speed"),
                "command_timeout_s": LaunchConfiguration("command_timeout_s"),
            }],
        ),
    ])
