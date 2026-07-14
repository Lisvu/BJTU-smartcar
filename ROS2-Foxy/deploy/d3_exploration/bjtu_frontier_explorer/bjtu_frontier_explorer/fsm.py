"""Hardware-independent state machine for exploration, approach, and celebration."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Optional, Tuple


class ExplorerState(Enum):
    EXPLORE = auto()
    APPROACH = auto()
    ARRIVE = auto()
    SPIN = auto()
    DONE = auto()


class EventType(Enum):
    FRONTIER_AVAILABLE = auto()
    FRONTIERS_EXHAUSTED = auto()
    STOP_DETECTED = auto()
    NAV_SUCCEEDED = auto()
    NAV_FAILED = auto()
    DISTANCE_UPDATED = auto()
    SPIN_STARTED = auto()
    SPIN_SUCCEEDED = auto()
    SPIN_FAILED = auto()
    RESET = auto()


class ActionType(Enum):
    NONE = auto()
    NAVIGATE_FRONTIER = auto()
    NAVIGATE_APPROACH = auto()
    SELECT_ANOTHER_FRONTIER = auto()
    START_SPIN = auto()
    STOP = auto()


@dataclass(frozen=True)
class FsmEvent:
    kind: EventType
    target: Optional[Tuple[float, float]] = None
    distance_m: Optional[float] = None


@dataclass(frozen=True)
class FsmAction:
    kind: ActionType
    target: Optional[Tuple[float, float]] = None
    reason: str = ""


@dataclass(frozen=True)
class FsmConfig:
    arrive_radius_m: float = 0.25
    max_frontier_failures: int = 0
    max_approach_failures: int = 2
    max_spin_failures: int = 1

    def __post_init__(self) -> None:
        if self.arrive_radius_m <= 0.0:
            raise ValueError("arrive_radius_m must be positive")
        if min(self.max_frontier_failures, self.max_approach_failures, self.max_spin_failures) < 0:
            raise ValueError("failure limits cannot be negative")


class ExplorerFSM:
    """Deterministic transition engine; ``max_frontier_failures=0`` means unlimited."""

    def __init__(self, config: FsmConfig = FsmConfig()):
        self.config = config
        self.state = ExplorerState.EXPLORE
        self.frontier_failures = 0
        self.approach_failures = 0
        self.spin_failures = 0
        self.stop_target: Optional[Tuple[float, float]] = None

    def handle(self, event: FsmEvent) -> FsmAction:
        if event.kind == EventType.RESET:
            self.__init__(self.config)
            return FsmAction(ActionType.NONE, reason="reset")
        if self.state == ExplorerState.DONE:
            return FsmAction(ActionType.STOP, reason="mission already complete")

        if event.kind == EventType.STOP_DETECTED and event.target is not None:
            self.stop_target = event.target
            self.state = ExplorerState.APPROACH
            self.approach_failures = 0
            return FsmAction(ActionType.NAVIGATE_APPROACH, event.target, "STOP pose received")

        if self.state == ExplorerState.EXPLORE:
            return self._handle_explore(event)
        if self.state == ExplorerState.APPROACH:
            return self._handle_approach(event)
        if self.state == ExplorerState.ARRIVE:
            if event.kind == EventType.SPIN_STARTED:
                self.state = ExplorerState.SPIN
                return FsmAction(ActionType.NONE, reason="spin accepted")
            return FsmAction(ActionType.NONE)
        if self.state == ExplorerState.SPIN:
            return self._handle_spin(event)
        return FsmAction(ActionType.NONE)

    def _handle_explore(self, event: FsmEvent) -> FsmAction:
        if event.kind == EventType.FRONTIER_AVAILABLE and event.target is not None:
            return FsmAction(ActionType.NAVIGATE_FRONTIER, event.target, "best frontier selected")
        if event.kind == EventType.FRONTIERS_EXHAUSTED:
            self.state = ExplorerState.DONE
            return FsmAction(ActionType.STOP, reason="no reachable frontiers remain")
        if event.kind == EventType.NAV_FAILED:
            self.frontier_failures += 1
            if (
                self.config.max_frontier_failures
                and self.frontier_failures >= self.config.max_frontier_failures
            ):
                self.state = ExplorerState.DONE
                return FsmAction(ActionType.STOP, reason="frontier failure limit reached")
            return FsmAction(ActionType.SELECT_ANOTHER_FRONTIER, reason="frontier navigation failed")
        if event.kind == EventType.NAV_SUCCEEDED:
            self.frontier_failures = 0
            return FsmAction(ActionType.SELECT_ANOTHER_FRONTIER, reason="frontier reached")
        return FsmAction(ActionType.NONE)

    def _handle_approach(self, event: FsmEvent) -> FsmAction:
        if event.kind == EventType.DISTANCE_UPDATED and event.distance_m is not None:
            if event.distance_m <= self.config.arrive_radius_m:
                self.state = ExplorerState.ARRIVE
                return FsmAction(ActionType.START_SPIN, reason="inside arrival radius")
        if event.kind == EventType.NAV_SUCCEEDED:
            self.state = ExplorerState.ARRIVE
            return FsmAction(ActionType.START_SPIN, reason="approach goal reached")
        if event.kind == EventType.NAV_FAILED:
            self.approach_failures += 1
            if self.approach_failures > self.config.max_approach_failures:
                self.state = ExplorerState.DONE
                return FsmAction(ActionType.STOP, reason="approach failure limit reached")
            return FsmAction(ActionType.NAVIGATE_APPROACH, self.stop_target, "retry STOP approach")
        return FsmAction(ActionType.NONE)

    def _handle_spin(self, event: FsmEvent) -> FsmAction:
        if event.kind == EventType.SPIN_SUCCEEDED:
            self.state = ExplorerState.DONE
            return FsmAction(ActionType.STOP, reason="celebration spin complete")
        if event.kind == EventType.SPIN_FAILED:
            self.spin_failures += 1
            if self.spin_failures > self.config.max_spin_failures:
                self.state = ExplorerState.DONE
                return FsmAction(ActionType.STOP, reason="spin failure limit reached")
            return FsmAction(ActionType.START_SPIN, reason="retry celebration spin")
        return FsmAction(ActionType.NONE)
