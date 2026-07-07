from setuptools import setup

package_name = "peacekeeper_bringup"

setup(
    name=package_name,
    version="0.1.0",
    packages=[package_name],
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (f"share/{package_name}/launch", [
            "launch/chassis.launch.py",
            "launch/lidar.launch.py",
            "launch/slam.launch.py",
            "launch/mapping_stack.launch.py",
        ]),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="Peacekeeper Team",
    maintainer_email="peacekeeper@example.invalid",
    description="Peacekeeper v1 launch wrappers.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "rosmaster_chassis = peacekeeper_bringup.rosmaster_chassis:main",
            "teleop_cmd_vel = peacekeeper_bringup.teleop_cmd_vel:main",
            "topic_watchdog = peacekeeper_bringup.topic_watchdog:main",
        ],
    },
)
