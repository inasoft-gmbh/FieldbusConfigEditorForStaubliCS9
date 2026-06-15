"""Guided, menu-driven console front-end. Thin client over fbconfig core.
UI text in English. No business logic lives here.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # src/ on path

from fbconfig import project
from cli.folderpick import pick_folder
from cli.views import overview_text, signals_text
from cli.util import ask_int, Cancelled
from cli.edit import edit_menu


class AppState:
    def __init__(self):
        self.robot_dir: str | None = None
        self.paths = None
        self.model = None

    @property
    def loaded(self) -> bool:
        return self.model is not None


def action_select_folder(state: AppState, folder: str | None = None):
    folder = folder or pick_folder()
    if not folder:
        print("  No folder selected.")
        return
    projects = project.discover(folder)
    state.robot_dir = folder
    if not projects:
        print(f"  No fieldbus project found under {folder}\\usr\\fieldbus\\.")
        print("  (Creating a new configuration here is not implemented yet.)")
        state.paths, state.model = None, None
        return
    if len(projects) > 1:
        print(f"\n  Found {len(projects)} fieldbus projects:")
        for i, p in enumerate(projects):
            print(f"    [{i}] {p.spj.parent.name} / {p.base_name}")
        sel = ask_int("  Select project #", 0, len(projects) - 1, default=0)
        paths = projects[sel]
    else:
        paths = projects[0]
    state.paths = paths
    state.model = project.load(paths)
    d = state.model.device
    print(f"\n  Loaded: {d.base_name} ({d.protocol}) from {Path(folder).name}")


def main_menu(state: AppState):
    print("\n" + "#" * 60)
    print("#  FIELDBUS CONFIG EDITOR")
    print("#" * 60)
    if not state.robot_dir:
        print("  Folder : (none selected)")
    else:
        print(f"  Folder : {state.robot_dir}")
        if state.loaded:
            d = state.model.device
            print(f"  Loaded : {d.base_name}  [{d.protocol}]  node {d.node_id}")
        else:
            print("  Loaded : (no fieldbus project found)")
    print("-" * 60)
    items = [("s", "Select robot folder" if not state.robot_dir
              else "Select another robot folder")]
    if state.loaded:
        items += [("o", "Show overview"),
                  ("c", "Show configured signals (address / type / name)"),
                  ("e", "Edit configuration")]
    items.append(("q", "Quit"))
    for key, label in items:
        print(f"   [{key}]  {label}")
    print("-" * 60)
    return {k for k, _ in items}


def run(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    state = AppState()
    if argv:  # optional: preload a folder (no dialog) for convenience/testing
        action_select_folder(state, argv[0])
    while True:
        valid = main_menu(state)
        choice = input("  > ").strip().lower()
        if choice not in valid:
            print("  Unknown option.")
            continue
        if choice == "q":
            print("  Bye.")
            return 0
        try:
            if choice == "s":
                action_select_folder(state)
            elif choice == "o":
                print(overview_text(state.model, state.paths))
            elif choice == "c":
                print(signals_text(state.model))
            elif choice == "e":
                edit_menu(state)
        except Cancelled:
            print("  Cancelled.")


def main(argv=None):
    return run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
