from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import PathJoinSubstitution
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    launch_path = PathJoinSubstitution([
        FindPackageShare("yahboomcar_nav"),
        "launch",
        "map_gmapping_a1_launch.py",
    ])
    return LaunchDescription([
        IncludeLaunchDescription(PythonLaunchDescriptionSource(launch_path)),
    ])
