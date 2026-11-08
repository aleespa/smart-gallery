from smart_gallery.format import format_count, format_duration, format_size


def test_size_three_significant_figures():
    assert format_size(0) == "0 B"
    assert format_size(1023) == "1023 B"
    assert format_size(1024) == "1.00 KB"
    assert format_size(int(7.43 * 2**20)) == "7.43 MB"
    assert format_size(int(45.6 * 2**30)) == "45.6 GB"
    assert format_size(int(781.2 * 2**30)) == "781 GB"


def test_size_rounding_never_shows_four_digits():
    assert format_size(int(999.5 * 2**20)) == "1000 MB"
    assert format_size(1024**2 - 1) == "1.00 MB"


def test_count_singular_and_plural():
    assert format_count(1, "file") == "1 file"
    assert format_count(84355, "file") == "84,355 files"
    assert format_count(2, "person", "people") == "2 people"


def test_duration():
    assert format_duration(12) == "12s"
    assert format_duration(243) == "4m 3s"
    assert format_duration(57299) == "15h 54m 59s"
