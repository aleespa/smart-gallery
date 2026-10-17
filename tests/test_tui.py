"""Drive the Textual UI headlessly: pick commands, fill the generated forms, and
check the live command preview and what actually runs."""

import asyncio

from textual.widgets import (
    Checkbox,
    Collapsible,
    Input,
    RichLog,
    Select,
    Static,
    Switch,
)

from smart_gallery.db import GalleryRepository
from smart_gallery.progress import COPYING, READING
from smart_gallery.tui import HelpScreen, SmartGalleryApp
from smart_gallery.tui_progress import ProgressPanel, StepRow


def run_app(scenario, size=(150, 80)):
    async def main():
        app = SmartGalleryApp()
        async with app.run_test(size=size) as pilot:
            await scenario(app, pilot)

    asyncio.run(main())


async def open_command(pilot, name):
    await pilot.click(f"#command-{name}")
    await pilot.pause()


async def fill(pilot, key, value):
    pilot.app.query_one(f"#field-{key}", Input).value = value
    await pilot.pause()


async def set_widget(pilot, selector, widget_type, value):
    pilot.app.query_one(selector, widget_type).value = value
    await pilot.pause()


def text_of(app, selector):
    return str(app.query_one(selector, Static).content)


def test_form_fills_preview_with_the_exact_command():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        assert app.query_one("#field-drive", Input)
        await fill(pilot, "drive", "E:/")
        await fill(pilot, "dest", "D:/My Selects")
        assert text_of(app, "#preview") == (
            '$ smart-gallery export --from E:/ --to "D:/My Selects"'
        )

    run_app(scenario)


def test_switches_choices_and_modes_change_the_preview():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await fill(pilot, "drive", "E:/")
        await fill(pilot, "dest", "D:/x")
        await set_widget(pilot, "#field-dry_run", Switch, True)
        await set_widget(pilot, "#field-on_exist", Select, "skip")
        assert text_of(app, "#preview").endswith("--on-exist skip --dry-run")

        await open_command(pilot, "cluster-faces")
        await fill(pilot, "drive", "E:/")
        await set_widget(pilot, "#field-mode", Select, "--incremental")
        assert text_of(app, "#preview") == (
            "$ smart-gallery cluster-faces E:/ --incremental"
        )

    run_app(scenario)


def test_folder_structure_follows_the_order_boxes_are_ticked():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await fill(pilot, "drive", "E:/")
        await fill(pilot, "dest", "D:/x")
        for choice, value in (
            ("Year", False),
            ("Month", False),
            ("Model", True),
            ("Year", True),
        ):
            await set_widget(pilot, f"#field-structure-{choice}", Checkbox, value)
        assert text_of(app, "#preview").endswith("--structure Model Year")

        def label(choice):
            return str(app.query_one(f"#field-structure-{choice}", Checkbox).label)

        assert label("Model") == "Model ¹"
        assert label("Year") == "Year ²"
        assert label("Month") == "Month"

    run_app(scenario)


def test_invalid_value_is_flagged_next_to_its_field():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await fill(pilot, "min_iso", "abc")
        error = app.query_one("#error-min_iso", Static)
        assert error.display
        assert "whole number" in str(error.content)
        await fill(pilot, "min_iso", "200")
        assert not error.display

    run_app(scenario)


def test_folded_section_title_counts_options_that_are_set():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        filters = app.query_one("#section-Filters", Collapsible)
        assert filters.collapsed
        await fill(pilot, "min_iso", "200")
        await set_widget(pilot, "#field-file_types-image", Checkbox, True)
        assert "2 set" in str(filters.title)

    run_app(scenario)


def test_drive_carries_over_and_values_survive_switching_commands():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await fill(pilot, "drive", "E:/")
        await fill(pilot, "dest", "D:/x")
        await open_command(pilot, "scan-faces")
        assert app.query_one("#field-drive", Input).value == "E:/"
        await open_command(pilot, "export")
        assert app.query_one("#field-dest", Input).value == "D:/x"

    run_app(scenario)


def test_run_is_blocked_while_required_fields_are_empty():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await pilot.click("#run")
        await pilot.pause()
        assert "Source drive" in text_of(app, "#status")
        assert app.query_one("#error-drive", Static).display
        assert app.query_one("#stop").disabled

    run_app(scenario)


def test_f1_shows_the_full_cli_help():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await pilot.press("f1")
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        assert "--portable-db" in text_of(app.screen, "#help-text")

    run_app(scenario)


def test_run_streams_the_command_output(drive, monkeypatch):
    # A plain terminal: the child would otherwise write the locale code page.
    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    monkeypatch.delenv("PYTHONUTF8", raising=False)
    monkeypatch.chdir(drive.parent)  # the run's log file lands under ./logs
    GalleryRepository.create(drive).close()

    async def scenario(app, pilot):
        await open_command(pilot, "sync")
        await fill(pilot, "drive", str(drive))
        await set_widget(pilot, "#field-dry_run", Switch, True)
        await pilot.click("#run")
        for _ in range(600):
            await pilot.pause(0.05)
            if "Running" not in text_of(app, "#status"):
                break
        assert text_of(app, "#status") == "Completed successfully."
        # Long lines wrap at a space, so rejoin them and collapse whitespace.
        lines = app.query_one("#output", RichLog).lines
        text = " ".join(" ".join(strip.text for strip in lines).split())
        assert "Sync (dry-run) — +0 ~0 -0 =0" in text
        assert "\x1b[" not in text  # colors are rendered, not printed as codes

    run_app(scenario)


def test_run_unfolds_sections_with_fields_to_fix():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        await fill(pilot, "drive", "E:/")
        await fill(pilot, "dest", "D:/x")
        await fill(pilot, "min_iso", "abc")
        filters = app.query_one("#section-Filters", Collapsible)
        assert filters.collapsed
        await pilot.click("#run")
        await pilot.pause()
        assert not filters.collapsed
        assert "Min ISO" in text_of(app, "#status")

    run_app(scenario)


def test_each_command_form_opens_scrolled_to_the_top():
    async def scenario(app, pilot):
        await open_command(pilot, "export")
        app.query_one("#section-Filters", Collapsible).collapsed = False
        await pilot.pause()
        form = app.query_one("#form")
        form.scroll_end(animate=False)
        await pilot.pause()
        assert form.scroll_y > 0
        await open_command(pilot, "cluster-faces")
        assert form.scroll_y == 0

    run_app(scenario, size=(150, 40))


async def run_and_wait(app, pilot):
    await pilot.click("#run")
    for _ in range(600):
        await pilot.pause(0.05)
        if "Running" not in text_of(app, "#status"):
            break


def test_progress_panel_replaces_per_file_lines(tmp_path, drive, monkeypatch):
    monkeypatch.chdir(tmp_path)  # the run's log file lands under ./logs
    card = tmp_path / "card"
    card.mkdir()
    for name in ("a.txt", "b.txt"):
        (card / name).write_bytes(b"x")
    GalleryRepository.create(drive).close()

    async def scenario(app, pilot):
        panel = app.query_one(ProgressPanel)
        assert not panel.display
        await open_command(pilot, "import")
        await fill(pilot, "sources", str(card))
        await fill(pilot, "drive", str(drive))
        await run_and_wait(app, pilot)
        assert text_of(app, "#status") == "Completed successfully."

        rows = list(app.query(StepRow))
        assert [r.step for r in rows] == [READING, COPYING]
        assert [r.state for r in rows] == ["done", "done"]
        legend = str(rows[1].query_one(".step-legend", Static).content)
        assert "2 copied" in legend and "0 failed" in legend
        assert panel.display

        lines = app.query_one("#output", RichLog).lines
        text = " ".join(" ".join(strip.text for strip in lines).split())
        assert "@sg-progress" not in text
        assert "Copied:" not in text
        assert "Details:" in text

    run_app(scenario)
