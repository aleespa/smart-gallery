from smart_gallery.gui.history.store import HistoryStore, JobRecord


def _record(title, argv, status="ok"):
    return JobRecord(
        title=title,
        argvs=[argv],
        started_at="2026-10-17T10:00:00+02:00",
        ended_at="2026-10-17T10:00:05+02:00",
        status=status,
        exit_code=0,
        summary="done",
        log_path=None,
        output_tail=["a", "b"],
    )


def test_history_roundtrip_and_skips_corrupt_lines(tmp_path):
    path = tmp_path / "history.jsonl"
    store = HistoryStore(path)
    assert store.load() == []

    first = _record("Import Mamá", ["import", "E:\\Photos\\Other cameras", "Mamá"])
    second = _record("Sync", ["sync", "E:/"], status="failed")
    store.append(first)
    with path.open("a", encoding="utf-8") as fh:
        fh.write("not json at all\n")
        fh.write('{"title": "missing the rest"}\n')
        fh.write("\n")
    store.append(second)

    loaded = store.load()
    assert loaded == [second, first]  # newest first
    assert loaded[1].argvs == [["import", "E:\\Photos\\Other cameras", "Mamá"]]
    assert "Mamá" in path.read_text(encoding="utf-8")  # readable, not \u escapes
