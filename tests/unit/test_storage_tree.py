import pandas as pd

from smart_gallery import colors
from smart_gallery.storage import tree as storage


def _frame(rows):
    return pd.DataFrame(
        {
            "relpath": [r[0] for r in rows],
            "ext": [r[1] for r in rows],
            "size_bytes": [r[2] for r in rows],
        }
    )


def _main_frame():
    return _frame(
        [
            ("Canon/2025/a.cr3", ".cr3", 300),
            ("Canon/2025/a.jpg", ".jpg", 100),
            ("Canon/2024/b.cr3", ".cr3", 50),
            ("Drone/c.mp4", ".mp4", 500),
            ("top.txt", ".txt", 7),
        ]
    )


def test_type_label():
    assert storage.type_label(".cr3") == "CR3"
    assert storage.type_label(".JPG") == "JPG"
    assert storage.type_label(".jpeg") == "JPEG"
    assert storage.type_label("") == storage.NO_EXT
    assert storage.type_label(None) == storage.NO_EXT


def test_format_bytes():
    assert storage.format_bytes(0) == "0 B"
    assert storage.format_bytes(1023) == "1023 B"
    assert storage.format_bytes(1024) == "1.00 KB"
    assert storage.format_bytes(1536 * 1024) == "1.50 MB"
    assert storage.format_bytes(int(45.6 * 1024**3)) == "45.6 GB"
    assert storage.format_bytes(912 * 1024**3) == "912 GB"
    assert storage.format_bytes(int(1.82 * 1024**4)) == "1.82 TB"


def test_folder_tree_totals_and_order():
    tree = storage.build_storage(_main_frame(), "E:")
    root = tree.by_folder
    assert (root.name, root.relpath, root.bytes, root.files) == ("E:", "", 957, 5)
    # folders first, then types, each biggest first
    assert [c.name for c in root.children] == ["Drone", "Canon", "TXT"]
    canon = root.children[1]
    assert (canon.bytes, canon.files, canon.relpath) == (450, 3, "Canon")
    # Canon has no own files -> no type rows
    assert [c.name for c in canon.children] == ["2025", "2024"]
    y25 = canon.children[0]
    assert [(c.kind, c.name, c.bytes, c.relpath) for c in y25.children] == [
        ("type", "CR3", 300, "Canon/2025"),
        ("type", "JPG", 100, "Canon/2025"),
    ]
    # root-level file's type row opens the root
    assert root.children[2].relpath == ""


def test_type_tree():
    tree = storage.build_storage(_main_frame(), "E:")
    types = tree.by_type.children
    assert [(t.name, t.bytes, t.files, t.relpath) for t in types] == [
        ("MP4", 500, 1, None),
        ("CR3", 350, 2, None),
        ("JPG", 100, 1, None),
        ("TXT", 7, 1, None),
    ]
    cr3 = types[1]
    assert [c.name for c in cr3.children] == ["Canon"]
    assert [(c.name, c.bytes, c.relpath) for c in cr3.children[0].children] == [
        ("2025", 300, "Canon/2025"),
        ("2024", 50, "Canon/2024"),
    ]
    # no type rows below a type
    assert all(c.kind == "folder" for c in cr3.children)


def test_type_colors_ranked_by_bytes():
    tree = storage.build_storage(_main_frame(), "E:")
    ranks = colors.categorical()
    assert tree.type_colors == {
        "MP4": ranks[0],
        "CR3": ranks[1],
        "JPG": ranks[2],
        "TXT": ranks[3],
    }
    assert tree.type_colors["MP4"] == colors.ACCENT


def test_types_past_category_count_are_neutral():
    n = colors.CATEGORY_COUNT + 1
    many = pd.DataFrame(
        {
            "relpath": [f"a/f.e{i}" for i in range(n)],
            "ext": [f".e{i}" for i in range(n)],
            "size_bytes": [100 - i for i in range(n)],
        }
    )
    tree = storage.build_storage(many, "E:")
    assert tree.type_colors["E0"] == colors.ACCENT
    assert tree.type_colors[f"E{n - 1}"] == colors.NEUTRAL


def test_extensionless_files():
    df = _frame([("a/README", "", 5)])
    tree = storage.build_storage(df, "E:")
    assert tree.by_folder.children[0].children[0].name == storage.NO_EXT


def test_folders_merge_case_insensitively():
    df = _frame([("Canon/a.jpg", ".jpg", 10), ("canon/b.jpg", ".jpg", 20)])
    root = storage.build_storage(df, "E:").by_folder
    assert [(c.name, c.bytes, c.files) for c in root.children] == [("Canon", 30, 2)]


def test_extensions_merge_case_insensitively():
    df = _frame([("a/x.JPG", ".JPG", 10), ("a/y.jpg", ".jpg", 20)])
    folder = storage.build_storage(df, "E:").by_folder.children[0]
    assert [(c.name, c.bytes, c.files) for c in folder.children] == [("JPG", 30, 2)]


def test_missing_size_counts_as_zero():
    df = _frame([("a/x.jpg", ".jpg", 10), ("a/y.jpg", ".jpg", None)])
    tree = storage.build_storage(df, "E:")
    assert (tree.total_bytes, tree.total_files) == (10, 2)
    assert isinstance(tree.total_bytes, int)


def test_empty_frame_is_none():
    empty = pd.DataFrame(columns=["relpath", "ext", "size_bytes"])
    assert storage.build_storage(empty, "E:") is None
