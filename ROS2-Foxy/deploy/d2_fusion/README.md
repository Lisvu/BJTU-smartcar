# D2 Static STOP Sign Fusion

This directory contains the stationary D2 perception deployment. The Jetson host runs the fine-tuned traffic-sign YOLO model and streams detections over a local TCP socket. The ROS 2 node in `bjtu_car` converts the highest-confidence STOP detection from pixel position to bearing, takes range from the registered Astra depth image inside the YOLO bounding box, and publishes the resulting `PoseStamped` in both `base_link` and `map` frames.

The lidar is not used as the STOP paper range source. A thin paper target produces missing or background laser returns, so the lidar value is printed only as `lidar_check` for obstacle validation. STOP pose range uses the median valid `16UC1` Astra depth inside the central portion of the detection box. The registered depth and UVC color images are both 640 by 480 pixels and share the color optical frame.

Run the complete stationary pipeline from the Mac clone:

```bash
./ROS2-Foxy/deploy/d2_fusion/scripts/run_d2_static_fusion.sh
```

The script releases the camera, lidar, and chassis serial devices, starts the chassis feedback driver, lidar, online `slam_toolbox`, registered Astra depth, YOLO, and the STOP pose node. Nav2 is not started. The vendor joystick publisher is stopped, and startup refuses to continue unless `/cmd_vel` has zero publishers. Pressing `Ctrl-C` stops the detector and the `bjtu_car` container.

The deployed model is expected at `/home/jetson/yolov5-7.0/traffic_sign_yolov5s.pt`, with class metadata at `/home/jetson/yolov5-7.0/traffic_signs.yaml`. The weight file is intentionally not duplicated in this source backup.

The stationary vehicle test measured approximately 3 to 5 cm range error at a taped distance of 1 m. A centered A4 STOP sign produced a bearing near 0 degrees, confirming the bearing sign and camera geometry. The lidar frequently returned no value or a distant wall at the same bearing, confirming that it must not measure the paper target.

A patterned garment at the right edge of the image produced a high-confidence STOP false positive. When its registered depth ROI was invalid, the node printed `depth=nan` and did not publish an incorrect pose. The long-term correction is to add that scene and similar garments as negative training samples, rather than hiding the model error with geometry-specific filters.

No D2 component publishes velocity. The complete test ran with `/cmd_vel` publisher count equal to zero, and the vehicle did not move.
