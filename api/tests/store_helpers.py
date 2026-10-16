"""Read persisted SurrealDB state when checking engine safety guarantees."""


def stored_record(store, table, key):
    row = store.db.select(store.rid(table, key))
    if isinstance(row, list):
        assert len(row) == 1
        row = row[0]
    assert isinstance(row, dict), f"Missing {table} record: {key}"
    return row


def audit_rows(store, run_id=None):
    if run_id is None:
        return store.db.query("SELECT * FROM audit ORDER BY at ASC")
    return store.db.query(
        "SELECT * FROM audit WHERE run = $run ORDER BY at ASC",
        {"run": store.rid("run", run_id)},
    )
