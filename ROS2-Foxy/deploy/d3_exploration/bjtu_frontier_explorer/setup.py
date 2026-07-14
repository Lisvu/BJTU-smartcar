from glob import glob
from setuptools import find_packages, setup

package_name = "bjtu_frontier_explorer"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=("test",)),
    data_files=[
        ("share/ament_index/resource_index/packages", ["resource/" + package_name]),
        ("share/" + package_name, ["package.xml"]),
        ("share/" + package_name + "/config", glob("config/*.yaml")),
        ("share/" + package_name + "/launch", glob("launch/*.launch.py")),
    ],
    install_requires=["setuptools", "numpy"],
    zip_safe=True,
    maintainer="BJTU Smart Car Team",
    maintainer_email="huan@example.com",
    description="Greedy frontier exploration and STOP-sign response.",
    license="MIT",
    entry_points={
        "console_scripts": [
            "explorer_node = bjtu_frontier_explorer.explorer_node:main",
        ],
    },
)
