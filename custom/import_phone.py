"""Import from a phone (Pixel) dump folder into the E:/ and D:/ archives."""

from pathlib import Path

from custom.recipe import ImportRecipe, run_recipe

RECIPE = ImportRecipe(
    source=Path(r"C:\Users\Alejandro\Pictures\Pixel10-07.06.2026"),
    photo_targets=[Path(r"E:\Photos\Other cameras"), Path(r"D:\Photos\Other cameras")],
    video_targets=[Path(r"E:\Videos"), Path(r"D:\Videos")],
    photo_extensions=[".cr3", ".jpg"],
    video_extensions=[".mp4"],
)


if __name__ == "__main__":
    run_recipe(RECIPE)
