from bjtu_frontier_explorer.fsm import (
    ActionType,
    EventType,
    ExplorerFSM,
    ExplorerState,
    FsmConfig,
    FsmEvent,
)


def test_explore_selects_frontier_and_retries_another_after_failure():
    fsm = ExplorerFSM()
    action = fsm.handle(FsmEvent(EventType.FRONTIER_AVAILABLE, (1.0, 2.0)))
    assert action.kind == ActionType.NAVIGATE_FRONTIER
    assert fsm.state == ExplorerState.EXPLORE
    action = fsm.handle(FsmEvent(EventType.NAV_FAILED))
    assert action.kind == ActionType.SELECT_ANOTHER_FRONTIER
    assert fsm.state == ExplorerState.EXPLORE


def test_default_frontier_retries_are_unlimited():
    fsm = ExplorerFSM(FsmConfig(max_frontier_failures=0))
    for _ in range(100):
        assert fsm.handle(FsmEvent(EventType.NAV_FAILED)).kind == ActionType.SELECT_ANOTHER_FRONTIER
    assert fsm.state == ExplorerState.EXPLORE


def test_configured_frontier_failure_limit_stops():
    fsm = ExplorerFSM(FsmConfig(max_frontier_failures=2))
    assert fsm.handle(FsmEvent(EventType.NAV_FAILED)).kind == ActionType.SELECT_ANOTHER_FRONTIER
    assert fsm.handle(FsmEvent(EventType.NAV_FAILED)).kind == ActionType.STOP
    assert fsm.state == ExplorerState.DONE


def test_frontier_exhaustion_finishes():
    fsm = ExplorerFSM()
    action = fsm.handle(FsmEvent(EventType.FRONTIERS_EXHAUSTED))
    assert action.kind == ActionType.STOP
    assert fsm.state == ExplorerState.DONE


def test_stop_detection_approach_arrival_spin_done_sequence():
    fsm = ExplorerFSM()
    action = fsm.handle(FsmEvent(EventType.STOP_DETECTED, (4.0, 3.0)))
    assert action.kind == ActionType.NAVIGATE_APPROACH
    assert fsm.state == ExplorerState.APPROACH
    action = fsm.handle(FsmEvent(EventType.NAV_SUCCEEDED))
    assert action.kind == ActionType.START_SPIN
    assert fsm.state == ExplorerState.ARRIVE
    fsm.handle(FsmEvent(EventType.SPIN_STARTED))
    assert fsm.state == ExplorerState.SPIN
    action = fsm.handle(FsmEvent(EventType.SPIN_SUCCEEDED))
    assert action.kind == ActionType.STOP
    assert fsm.state == ExplorerState.DONE


def test_distance_can_confirm_arrival():
    fsm = ExplorerFSM(FsmConfig(arrive_radius_m=0.3))
    fsm.handle(FsmEvent(EventType.STOP_DETECTED, (1.0, 1.0)))
    assert fsm.handle(FsmEvent(EventType.DISTANCE_UPDATED, distance_m=0.31)).kind == ActionType.NONE
    assert fsm.handle(FsmEvent(EventType.DISTANCE_UPDATED, distance_m=0.30)).kind == ActionType.START_SPIN
    assert fsm.state == ExplorerState.ARRIVE


def test_approach_retries_then_stops():
    fsm = ExplorerFSM(FsmConfig(max_approach_failures=1))
    fsm.handle(FsmEvent(EventType.STOP_DETECTED, (2.0, 0.0)))
    retry = fsm.handle(FsmEvent(EventType.NAV_FAILED))
    assert retry.kind == ActionType.NAVIGATE_APPROACH
    assert retry.target == (2.0, 0.0)
    assert fsm.handle(FsmEvent(EventType.NAV_FAILED)).kind == ActionType.STOP


def test_spin_retry_and_terminal_failure():
    fsm = ExplorerFSM(FsmConfig(max_spin_failures=1))
    fsm.handle(FsmEvent(EventType.STOP_DETECTED, (1.0, 0.0)))
    fsm.handle(FsmEvent(EventType.NAV_SUCCEEDED))
    fsm.handle(FsmEvent(EventType.SPIN_STARTED))
    assert fsm.handle(FsmEvent(EventType.SPIN_FAILED)).kind == ActionType.START_SPIN
    assert fsm.handle(FsmEvent(EventType.SPIN_FAILED)).kind == ActionType.STOP


def test_reset_restores_explore_state_and_counters():
    fsm = ExplorerFSM()
    fsm.handle(FsmEvent(EventType.NAV_FAILED))
    fsm.handle(FsmEvent(EventType.FRONTIERS_EXHAUSTED))
    fsm.handle(FsmEvent(EventType.RESET))
    assert fsm.state == ExplorerState.EXPLORE
    assert fsm.frontier_failures == 0
