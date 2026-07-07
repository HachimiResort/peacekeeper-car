from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch_ros.substitutions import FindPackageShare
from launch.substitutions import PathJoinSubstitution


def _include_launch(name):
    launch_path = PathJoinSubstitution([
        FindPackageShare("peacekeeper_bringup"),
        "launch",
        name,
    ])
    return IncludeLaunchDescription(PythonLaunchDescriptionSource(launch_path))


def generate_launch_description():
    return LaunchDescription([
        _include_launch("chassis.launch.py"),
        _include_launch("lidar.launch.py"),
        _include_launch("slam.launch.py"),
    ])
