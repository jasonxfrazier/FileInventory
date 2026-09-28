from src.fileinventory import Options, FileInventory
from pathlib import Path


if __name__ == "__main__":
    options = Options(
        ignore_hidden=False,
        ignore_system=False,
        recursive=True,
        cutoff_size=500,
        top_n=5,
        target_dir=Path('D:\\Code'),
        db_path=Path(__file__).resolve().parent / "db" / "inventory.db",
    )
    inv = FileInventory(options)
    inv.view()
