from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def generate_launch_description():
    sllidar_launch = PathJoinSubstitution([
        FindPackageShare("sllidar_ros2"),
        "launch",
        "sllidar_launch.py",
    ])
    return LaunchDescription([
        IncludeLaunchDescription(PythonLaunchDescriptionSource(sllidar_launch)),
    ])
