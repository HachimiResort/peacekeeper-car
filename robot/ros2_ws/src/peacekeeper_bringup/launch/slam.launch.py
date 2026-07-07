from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        Node(
            package="yahboomcar_nav",
            executable="slam_gmapping_X3",
            name="peacekeeper_slam",
            output="screen",
        ),
    ])
