# D1 Online SLAM and Nav2 Deployment

This directory is a self-contained backup of the D1 vehicle deployment. It starts the Yahboom X3 chassis driver and RPLidar, runs `slam_toolbox` in online asynchronous mapping mode, and starts Nav2 against the live map without a static map server or AMCL. The Nav2 global planner permits unknown space, while both costmaps consume the live laser scan.

The vehicle test passed online mapping, path planning, straight-line motion, and automatic stopping at the goal. The complete ROS 2 graph uses CycloneDDS with `ROS_DOMAIN_ID=11`. Before startup, `free_devices.sh` stops known vendor perception and chassis holders and verifies device ownership at the Jetson host level. Startup also rejects a zero or missing chassis voltage and any serial disconnect or multiple-access error.

Run the deployment from the Mac project clone:

```bash
./ROS2-Foxy/deploy/d1_nav/scripts/start_d1_slam_nav.sh
```

The start script does not send a navigation goal. Stop the deployment with:

```bash
./ROS2-Foxy/deploy/d1_nav/scripts/stop_d1_slam_nav.sh
```

The current vendor goal tolerance is approximately 0.25 m. D3 must tighten this before precise stopping in front of a sign. The live laser obstacle layers are configured but have not yet been validated with a physical obstacle test. The car currently has no usable battery and must remain tethered to power; D2 stationary perception and fusion can be completed while tethered, while D3 mobile exploration should wait for the battery.

The deployed files originated from the local `ROS2-Foxy` history at commits `ea9aed0`, `ce821e6`, and `9156db2`.
