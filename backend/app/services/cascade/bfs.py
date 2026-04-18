from .snap import Snap, SnapTask


def same_equipment_overlapping(t: SnapTask, snap: Snap) -> list[SnapTask]:
    raise NotImplementedError


def same_eq_prev_end(t: SnapTask, snap: Snap):
    raise NotImplementedError


def successor_tasks(t: SnapTask, snap: Snap) -> list[SnapTask]:
    raise NotImplementedError
