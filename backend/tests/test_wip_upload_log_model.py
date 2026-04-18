from app.infrastructure.models.wip_upload_log import WipUploadLog


def test_wip_upload_log_insert(db):
    log = WipUploadLog(
        canonical_hash="a" * 64,
        raw_file_hash="b" * 64,
        run_label="test_run",
        rows_inserted=5,
        rows_updated=2,
        file_name="test.xlsx",
    )
    db.add(log)
    db.flush()
    assert log.upload_id is not None


def test_canonical_hash_unique_constraint(db):
    import pytest
    from sqlalchemy.exc import IntegrityError

    h = "c" * 64
    db.add(WipUploadLog(canonical_hash=h))
    db.flush()

    db.add(WipUploadLog(canonical_hash=h))  # 중복
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
