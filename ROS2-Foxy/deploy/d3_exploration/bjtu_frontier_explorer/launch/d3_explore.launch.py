"""Launch the D3 explorer with dry-run enabled unless explicitly overridden."""

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
import os


def generate_launch_description():
    package_share = get_package_share_directory("bjtu_frontier_explorer")
    config = os.path.join(package_share, "config", "frontier.yaml")
    dry_run = LaunchConfiguration("dry_run")
    return LaunchDescription([
        DeclareLaunchArgument(
            "dry_run",
            default_value="true",
            description="When true, do not create Nav2 action clients or send motion goals.",
        ),
        Node(
            package="bjtu_frontier_explorer",
            executable="explorer_node",
            name="bjtu_frontier_explorer",
            output="screen",
            parameters=[config, {"dry_run": dry_run}],
        ),
    ])
