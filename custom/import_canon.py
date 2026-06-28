"""Import from the Canon SD card into the E:/ and D:/ archives."""

from pathlib import Path

from custom.recipe import ImportRecipe, run_recipe

RECIPE = ImportRecipe(
    source=Path(r"F:\\"),
    photo_targets=[Path(r"E:\Photos\Canon"), Path(r"D:\Photos\Canon")],
    video_targets=[Path(r"E:\Videos"), Path(r"D:\Videos")],
    photo_extensions=[".cr3", ".jpg"],
    video_extensions=[".mp4"],
)


if __name__ == "__main__":
    run_recipe(RECIPE)
