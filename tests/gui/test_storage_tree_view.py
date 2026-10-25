import pandas as pd
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from smart_gallery.gui.storage import tree_view
from smart_gallery.gui.storage.tree_view import (
    COLUMNS,
    NOT_FOUND,
    SHARE_ROLE,
    StorageTreeView,
)
from smart_gallery.storage.tree import build_storage


@pytest.fixture
def tree():
    df = pd.DataFrame(
        {
            "relpath": ["Canon/2025/a.cr3", "Canon/2025/a.jpg", "Drone/c.mp4"],
            "ext": [".cr3", ".jpg", ".mp4"],
            "size_bytes": [300, 100, 500],
        }
    )
    return build_storage(df, "Drive")


@pytest.fixture
def opened(monkeypatch):
    calls = []
    monkeypatch.setattr(tree_view, "open_folder", calls.append)
    return calls


@pytest.fixture
def view(qtbot, tmp_path):
    (tmp_path / "Canon" / "2025").mkdir(parents=True)
    v = StorageTreeView()
    qtbot.addWidget(v)
    return v


def _child(model, parent, name, column=0):
    for r in range(model.rowCount(parent)):
        if model.index(r, 0, parent).data() == name:
            return model.index(r, column, parent)
    raise AssertionError(name)


def _canon_2025(view):
    m = view.tree.model()
    root = m.index(0, 0)
    return _child(m, _child(m, root, "Canon"), "2025")


def _cr3(view):
    return _child(view.tree.model(), _canon_2025(view), "CR3")


def test_folder_grouping_rows(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    m = view.tree.model()
    assert [m.headerData(i, Qt.Orientation.Horizontal) for i in range(4)] == list(
        COLUMNS
    )
    root = m.index(0, 0)
    assert view.tree.isExpanded(root)
    assert [m.index(r, 0, root).data() for r in range(m.rowCount(root))] == [
        "Drone",
        "Canon",
    ]
    assert m.index(0, 1, root).data() == "500 B"
    assert m.index(0, 3, root).data() == "1"


def test_share_column_is_fraction_of_parent(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    m = view.tree.model()
    root = m.index(0, 0)
    drone = m.index(0, 2, root)
    assert drone.data() == "56%"
    assert drone.data(SHARE_ROLE) == pytest.approx(500 / 900)
    assert m.index(0, 2).data(SHARE_ROLE) == 1.0
    cr3 = _cr3(view)
    assert cr3.data(Qt.ItemDataRole.DecorationRole) == QColor(tree.type_colors["CR3"])


def test_grouping_switch_rebuilds(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.waitSignal(view.grouping_changed) as sig:
        view.buttons["type"].click()
    assert sig.args == ["type"]
    assert view.grouping() == "type"
    root = view.tree.model().index(0, 0)
    assert view.tree.model().index(0, 0, root).data() == "MP4"


def test_set_same_grouping_is_silent(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.assertNotEmitted(view.grouping_changed):
        view.set_grouping("folder")


def test_double_click_folder_opens_it(qtbot, view, tree, tmp_path, opened):
    view.set_tree(tree, tmp_path)
    view.open_node(_canon_2025(view))
    assert opened == [tmp_path / "Canon/2025"]


def test_type_row_opens_its_folder(qtbot, view, tree, tmp_path, opened):
    view.set_tree(tree, tmp_path)
    view.open_node(_cr3(view))
    assert opened == [tmp_path / "Canon/2025"]


def _mp4(view):
    m = view.tree.model()
    return _child(m, m.index(0, 0), "MP4")


def test_type_top_level_menu_is_disabled(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    view.set_grouping("type")
    menu = view.context_menu(_mp4(view))
    assert menu.actions()[0].text() == "Open in Explorer"
    assert menu.actions()[0].isEnabled() is False


def test_missing_folder_shows_message(qtbot, view, tree, tmp_path, opened):
    view.set_tree(tree, tmp_path)
    m = view.tree.model()
    with qtbot.waitSignal(view.message) as sig:
        view.open_node(_child(m, m.index(0, 0), "Drone"))
    assert opened == []
    assert sig.args == [NOT_FOUND.format(path=tmp_path / "Drone")]


def test_click_selects_folder(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.waitSignal(view.folder_selected) as sig:
        view.tree.setCurrentIndex(_canon_2025(view))
    assert sig.args == ["Canon/2025"]


def test_type_row_selects_its_folder(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    with qtbot.waitSignal(view.folder_selected) as sig:
        view.tree.setCurrentIndex(_cr3(view))
    assert sig.args == ["Canon/2025"]


def test_top_level_type_selects_none(qtbot, view, tree, tmp_path):
    view.set_tree(tree, tmp_path)
    view.set_grouping("type")
    with qtbot.waitSignal(view.folder_selected) as sig:
        view.tree.setCurrentIndex(_mp4(view))
    assert sig.args == [None]


def test_files_column_has_thousands_separators(qtbot, view, tmp_path):
    n = 1234
    df = pd.DataFrame(
        {
            "relpath": [f"Big/{i}.jpg" for i in range(n)],
            "ext": [".jpg"] * n,
            "size_bytes": [1] * n,
        }
    )
    view.set_tree(build_storage(df, "Drive"), tmp_path)
    m = view.tree.model()
    assert m.index(0, 3).data() == "1,234"
    assert _child(m, m.index(0, 0), "Big", 3).data() == "1,234"


def test_tree_indents_like_the_library(qtbot, view):
    from smart_gallery.gui import theme

    assert view.tree.indentation() == theme.TREE_INDENT
