"""Small Nav2 action facade with generation-based stale callback rejection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import NavigateToPose, Spin
from rclpy.action import ActionClient


@dataclass(frozen=True)
class NavResult:
    kind: str
    generation: int
    accepted: bool
    status: int

    @property
    def succeeded(self) -> bool:
        return self.accepted and self.status == GoalStatus.STATUS_SUCCEEDED


class NavInterface:
    """Own NavigateToPose and Spin clients while exposing one active generation."""

    def __init__(self, node, navigate_action: str = "/navigate_to_pose", spin_action: str = "/spin"):
        self._node = node
        self._navigate = ActionClient(node, NavigateToPose, navigate_action)
        self._spin = ActionClient(node, Spin, spin_action)
        self._generation = 0
        self._pending = False
        self._active_handle = None
        self._callback: Optional[Callable[[NavResult], None]] = None

    @property
    def generation(self) -> int:
        return self._generation

    @property
    def pending(self) -> bool:
        return self._pending or self._active_handle is not None

    def navigate(
        self,
        pose: PoseStamped,
        callback: Callable[[NavResult], None],
        timeout_s: float = 2.0,
    ) -> bool:
        if not self._navigate.wait_for_server(timeout_sec=timeout_s):
            return False
        goal = NavigateToPose.Goal()
        goal.pose = pose
        return self._send("navigate", self._navigate, goal, callback)

    def spin(self, target_yaw: float, callback: Callable[[NavResult], None], timeout_s: float = 2.0) -> bool:
        if not self._spin.wait_for_server(timeout_sec=timeout_s):
            return False
        goal = Spin.Goal()
        goal.target_yaw.sec = int(abs(target_yaw))
        goal.target_yaw.nanosec = int((abs(target_yaw) - int(abs(target_yaw))) * 1e9)
        return self._send("spin", self._spin, goal, callback)

    def _send(self, kind: str, client, goal, callback: Callable[[NavResult], None]) -> bool:
        self.cancel()
        self._generation += 1
        generation = self._generation
        self._pending = True
        self._callback = callback
        future = client.send_goal_async(goal)
        future.add_done_callback(lambda done: self._goal_response(done, kind, generation))
        return True

    def _goal_response(self, future, kind: str, generation: int) -> None:
        handle = future.result()
        if generation != self._generation:
            if handle.accepted:
                handle.cancel_goal_async()
            return
        self._pending = False
        if not handle.accepted:
            self._emit(NavResult(kind, generation, False, GoalStatus.STATUS_ABORTED))
            return
        self._active_handle = handle
        handle.get_result_async().add_done_callback(
            lambda done: self._goal_result(done, kind, generation)
        )

    def _goal_result(self, future, kind: str, generation: int) -> None:
        if generation != self._generation:
            return
        self._active_handle = None
        self._emit(NavResult(kind, generation, True, future.result().status))

    def _emit(self, result: NavResult) -> None:
        callback, self._callback = self._callback, None
        if callback is not None:
            callback(result)

    def cancel(self) -> None:
        self._generation += 1
        self._pending = False
        self._callback = None
        if self._active_handle is not None:
            self._active_handle.cancel_goal_async()
            self._active_handle = None
