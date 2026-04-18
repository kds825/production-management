from datetime import date

from app.services.sheath_cluster import SheathCluster, build_sheath_clusters


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
