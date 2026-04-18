from app.services.sheath_cluster import SheathCluster


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
