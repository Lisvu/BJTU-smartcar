# D3 Greedy Frontier Explorer

This deployment adds autonomous room search on top of the already validated D1 and D2 pipelines. The robot builds a map online, repeatedly visits useful boundaries between known and unknown space, and switches to the STOP-sign response when D2 publishes a target pose.

The implementation is an `ament_python` package under `bjtu_frontier_explorer/`. Its map algorithms and finite-state machine do not depend on ROS, so they can be tested with synthetic NumPy maps without a robot, Nav2, or DDS runtime.

## Runtime Contract

D1 supplies online mapping, localization transforms, and collision-aware navigation:

- `/map` (`nav_msgs/msg/OccupancyGrid`) from `slam_toolbox`
- `map -> odom -> base_footprint` TF
- `/navigate_to_pose` (`nav2_msgs/action/NavigateToPose`)
- `/spin` (`nav2_msgs/action/Spin`)
- `/scan`, odometry, costmaps, planner, and controller behind Nav2

D2 supplies:

- `/bjtu/stop_pose_map` (`geometry_msgs/msg/PoseStamped`), frame `map`

The D2 pose uses YOLO horizontal bearing and median registered Astra depth. Lidar does not measure the thin STOP paper; D2 retains lidar only for obstacle checking and diagnostics.

All processes use ROS 2 Foxy, `ROS_DOMAIN_ID=11`, and `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`.

## Algorithm

1. **Occupancy conversion:** `occupancy.py` validates `/map`, stores it as `grid[y, x]`, and handles cell/world transforms including translation and yaw in the map origin.
2. **Yamauchi frontier detection:** a frontier is a known-free cell adjacent to at least one unknown cell. Four- or eight-connected unknown adjacency and a minimum number of free neighbors are configurable.
3. **Clustering:** connected frontier cells become clusters. Small components are removed; each retained component records its cells, centroid, area, and bounding box.
4. **Safe target projection:** a cluster representative is moved to the nearest known-free cell that has `occupied_clearance_m` clearance from occupied cells.
5. **Greedy scoring:** the default utility is `area / (distance + distance_bias)`. Optional unknown-cell information gain and heading penalty terms can be enabled without changing the selector.
6. **Nav2 execution:** the highest-scoring target is sent through `NavigateToPose`. Nav2 owns path planning, live lidar avoidance, and replanning. An aborted frontier is blacklisted and the next frontier is selected; the default failure count is unlimited so one bad frontier does not end exploration.
7. **STOP response:** a `/bjtu/stop_pose_map` message cancels exploration and creates an approach point `approach_standoff_m` before the sign. After Nav2 reaches it, the FSM requests one `Spin` action and finishes.

The explicit state sequence is:

```text
EXPLORE -> APPROACH -> ARRIVE -> SPIN -> DONE
```

`nav_interface.py` assigns a generation to every goal. Results from canceled or superseded goals are ignored, preventing a late callback from an old frontier from changing the current mission.

## Package Layout

| Module | Responsibility |
| --- | --- |
| `occupancy.py` | Map storage, origin-aware transforms, cell queries |
| `frontier_detection.py` | Yamauchi frontier predicate and extraction |
| `clustering.py` | BFS connected components and cluster geometry |
| `scoring.py` | Area/distance utility, information gain, heading term |
| `costmap_utils.py` | Occupied-cell clearance and safe target projection |
| `fsm.py` | Pure exploration/approach/arrival/spin state machine |
| `nav_interface.py` | NavigateToPose and Spin ActionClient lifecycle |
| `explorer_node.py` | ROS subscriptions, TF lookup, module composition |

## Parameters

All defaults are in `bjtu_frontier_explorer/config/frontier.yaml`.

| Parameter | Default | Meaning |
| --- | ---: | --- |
| `dry_run` | `true` | Do not construct Nav2 action clients or send goals |
| `map_topic` | `/map` | Online SLAM occupancy grid |
| `stop_pose_topic` | `/bjtu/stop_pose_map` | D2 STOP pose in `map` |
| `map_frame` | `map` | Planning frame |
| `base_frame` | `base_footprint` | D1/Nav2 robot frame |
| `navigate_action` | `/navigate_to_pose` | Nav2 navigation action |
| `spin_action` | `/spin` | Nav2 rotation action |
| `recompute_period_s` | `2.0` | Frontier selection period |
| `frontier_connectivity` | `8` | Frontier clustering/free-neighbor connectivity |
| `unknown_connectivity` | `8` | Unknown adjacency used by frontier detection |
| `min_free_neighbors` | `1` | Noise filter for isolated free cells |
| `min_frontier_cells` | `3` | Minimum connected frontier size |
| `distance_bias_m` | `0.5` | Stabilizer in area/distance score |
| `area_weight` | `1.0` | Cluster area utility weight |
| `information_gain_weight` | `0.0` | Optional unknown-cell gain weight |
| `information_gain_radius_m` | `0.75` | Radius used to estimate information gain |
| `heading_penalty_weight` | `0.0` | Optional turn-cost penalty |
| `occupied_clearance_m` | `0.18` | Required target clearance from occupied cells |
| `safe_target_search_radius_m` | `2.0` | Maximum target projection search radius |
| `frontier_blacklist_radius_m` | `0.5` | Suppression radius after a failed goal |
| `frontier_exhaustion_cycles` | `3` | Empty scans required before mission completion |
| `approach_standoff_m` | `0.7` | Navigation stopping distance before STOP pose |
| `arrive_radius_m` | `0.25` | Pure-FSM arrival threshold |
| `stop_pose_timeout_s` | `1.0` | Reserved freshness threshold for continuous detections |
| `spin_target_yaw` | `6.283185...` | One full revolution in radians |
| `max_frontier_failures` | `0` | Frontier failure limit; zero means unlimited |
| `max_approach_failures` | `2` | Retries allowed for STOP approach |
| `max_spin_failures` | `1` | Retries allowed for celebration spin |

## Build And Test

From a Foxy workspace containing this package:

```bash
colcon build --packages-select bjtu_frontier_explorer
source install/setup.bash
colcon test --packages-select bjtu_frontier_explorer
colcon test-result --verbose
```

Pure logic tests can also run without ROS:

```bash
PYTHONPATH=./bjtu_frontier_explorer python3 -m pytest -q bjtu_frontier_explorer/test
```

The tests cover rotated map origins, coordinate round trips, exact frontier masks, degenerate maps, four/eight connectivity, component geometry, small-cluster filtering, score terms, safe target projection, retries, exhaustion, and every FSM phase.

## Dry-Run Procedure

Start D1 mapping and Nav2 first, then launch the explorer with its safe default:

```bash
export ROS_DISTRO=foxy
export ROS_DOMAIN_ID=11
export RMW_IMPLEMENTATION=rmw_cyclonedds_cpp
source /opt/ros/foxy/setup.bash
source install/setup.bash
ros2 launch bjtu_frontier_explorer d3_explore.launch.py dry_run:=true
```

Dry-run still subscribes to `/map` and `/bjtu/stop_pose_map` and prints selected frontiers and FSM decisions. It does **not** construct NavigateToPose or Spin clients and cannot send a movement goal.

Useful checks:

```bash
ros2 topic info /map
ros2 topic info /bjtu/stop_pose_map
ros2 run tf2_ros tf2_echo map base_footprint
ros2 action list -t | grep -E 'navigate_to_pose|spin'
```

## Live Procedure

Live mode is a separate, explicit step after D1, D2, TF, costmaps, and dry-run output have been checked:

```bash
ros2 launch bjtu_frontier_explorer d3_explore.launch.py dry_run:=false
```

Use an open test area, low Nav2 velocity limits, a charged untethered vehicle, a spotter beside the power switch, and an initially reachable frontier. `Ctrl-C` cancels the current action goal. D1 remains responsible for lidar obstacle avoidance while moving to frontiers and the STOP standoff point.

## Compatibility Note

The original `scripts/frontier_fsm_node.py` is retained as deployment history. The package preserves its topic names and greedy area/distance behavior while separating algorithms from ROS plumbing and replacing ad-hoc action bookkeeping with a generation-aware interface. Existing D1 and D2 files are not modified by this package.
