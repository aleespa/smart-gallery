import pytest

from smart_gallery.cli import main
from smart_gallery.db import GalleryRepository
from smart_gallery.services.rename import MAX_NAME, NAME_KEY, rename_gallery


def test_rename_stores_the_trimmed_name(repo):
    assert rename_gallery(repo, "  Family   photos ") == "Family photos"
    assert repo.get_meta(NAME_KEY) == "Family photos"


@pytest.mark.parametrize("name", ["", "   ", "x" * (MAX_NAME + 1)])
def test_rename_rejects_blank_or_long_names(repo, name):
    repo.set_meta(NAME_KEY, "Archive")
    with pytest.raises(ValueError):
        rename_gallery(repo, name)
    assert repo.get_meta(NAME_KEY) == "Archive"


def test_cli_rename(drive, repo):
    main(["rename", str(drive), "Archive 2026"])
    with GalleryRepository.open(drive, read_only=True) as again:
        assert again.get_meta(NAME_KEY) == "Archive 2026"


def test_cli_rename_blank_exits_1(drive, repo):
    with pytest.raises(SystemExit) as exc:
        main(["rename", str(drive), "  "])
    assert exc.value.code == 1
