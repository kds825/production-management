"""통합 테스트: 같은 묶음 그룹이 solved_order 에서 연속으로 나오는지."""

from datetime import date, datetime
from app.domain.sheath_cluster import build_sheath_clusters, cluster_sort_key


class _FakeBatch:
    def __init__(self, process_name, color, due, sq=120):
        self.process_name, self.sheath_color, self.due_date = process_name, color, due
        self.sq_mm2 = sq


def test_two_clusters_one_earlier_due_sorts_first():
    groups_meta = {
        "A100_갈_W17_120SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 17))],
            "earliest_due": date(2026, 4, 17),
            "cpsat_dur": 100,
            "pred_ready": datetime(2026, 4, 6, 8, 0),
        },
        "A100_회_W10_120SQ": {
            "batches": [_FakeBatch("저압시스", "회", date(2026, 4, 10))],
            "earliest_due": date(2026, 4, 10),
            "cpsat_dur": 100,
            "pred_ready": datetime(2026, 4, 6, 8, 0),
        },
    }
    clusters = build_sheath_clusters(groups_meta)
    ordered = sorted(clusters, key=lambda c: cluster_sort_key(c, groups_meta))
    flat = [gk for c in ordered for gk in c.group_keys]
    assert flat == ["A100_회_W10_120SQ", "A100_갈_W17_120SQ"]
