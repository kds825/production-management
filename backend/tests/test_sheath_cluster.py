from datetime import date, datetime

from app.services.sheath_cluster import (
    SheathCluster,
    build_sheath_clusters,
    compute_cluster_meta,
)


class _FakeBatch:
    def __init__(self, process_name, sheath_color, due_date, sq_mm2=120):
        self.process_name = process_name
        self.sheath_color = sheath_color
        self.due_date = due_date
        self.sq_mm2 = sq_mm2


def test_sheath_cluster_dataclass_fields():
    c = SheathCluster(
        cluster_id="A100_갈_2026W16H2",
        equipment_category="A100",
        color="갈",
        due_week_int=405233,
        group_keys=["A100_갈_2026W16H2_120SQ"],
    )
    assert c.cluster_id == "A100_갈_2026W16H2"
    assert c.equipment_category == "A100"
    assert c.color == "갈"
    assert c.due_week_int == 405233
    assert c.group_keys == ["A100_갈_2026W16H2_120SQ"]


def test_build_clusters_groups_by_category_week_color():
    groups_meta = {
        "A100_갈_2026W16H2_120SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 16))]
        },
        "A100_갈_2026W16H2_400SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 17))]
        },
        "A100_회_2026W16H2_120SQ": {
            "batches": [_FakeBatch("저압시스", "회", date(2026, 4, 16))]
        },
        "A120_흑_2026W15H2_240SQ": {
            "batches": [_FakeBatch("저압시스", "흑", date(2026, 4, 9))]
        },
    }
    clusters = build_sheath_clusters(groups_meta)
    by_id = {c.cluster_id: c for c in clusters}
    assert "A100_갈_2026W16H2" in by_id
    assert set(by_id["A100_갈_2026W16H2"].group_keys) == {
        "A100_갈_2026W16H2_120SQ",
        "A100_갈_2026W16H2_400SQ",
    }
    assert "A100_회_2026W16H2" in by_id
    assert "A120_흑_2026W15H2" in by_id


def test_build_clusters_excludes_non_sheath_groups():
    groups_meta = {
        "연선_120SQ": {"batches": [_FakeBatch("연선", None, date(2026, 4, 10))]},
    }
    clusters = build_sheath_clusters(groups_meta)
    assert clusters == []


def test_cluster_meta_aggregates_earliest_and_latest_due():
    groups_meta = {
        "A100_갈_2026W16H2_120SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 16))],
            "earliest_due": date(2026, 4, 16),
            "cpsat_dur": 240,
            "pred_ready": datetime(2026, 4, 6, 19, 0),
        },
        "A100_갈_2026W16H2_400SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 17))],
            "earliest_due": date(2026, 4, 17),
            "cpsat_dur": 180,
            "pred_ready": datetime(2026, 4, 7, 14, 0),
        },
    }
    cluster = SheathCluster(
        cluster_id="A100_갈_W405233",
        equipment_category="A100",
        color="갈",
        due_week_int=405233,
        group_keys=["A100_갈_2026W16H2_120SQ", "A100_갈_2026W16H2_400SQ"],
    )
    meta = compute_cluster_meta(cluster, groups_meta)
    assert meta["earliest"] == datetime(2026, 4, 6, 19, 0)
    assert meta["latest_due"] == date(2026, 4, 16)
    assert meta["total_duration_min"] == 420


def test_cluster_sort_key_orders_by_latest_due_then_color():
    groups_meta = {
        "A100_갈_W2_120SQ": {
            "batches": [_FakeBatch("저압시스", "갈", date(2026, 4, 17))],
            "earliest_due": date(2026, 4, 17),
            "cpsat_dur": 100,
            "pred_ready": datetime(2026, 4, 6, 8, 0),
        },
        "A100_회_W1_120SQ": {
            "batches": [_FakeBatch("저압시스", "회", date(2026, 4, 10))],
            "earliest_due": date(2026, 4, 10),
            "cpsat_dur": 100,
            "pred_ready": datetime(2026, 4, 6, 8, 0),
        },
    }
    c_gal = SheathCluster(
        cluster_id="A100_갈_W2",
        equipment_category="A100",
        color="갈",
        due_week_int=405234,
        group_keys=["A100_갈_W2_120SQ"],
    )
    c_hoi = SheathCluster(
        cluster_id="A100_회_W1",
        equipment_category="A100",
        color="회",
        due_week_int=405231,
        group_keys=["A100_회_W1_120SQ"],
    )
    ordered = sorted([c_gal, c_hoi], key=lambda c: cluster_sort_key(c, groups_meta))
    assert ordered[0].cluster_id == "A100_회_W1"
    assert ordered[1].cluster_id == "A100_갈_W2"
