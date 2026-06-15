"""Console input helpers (guided prompts with validation).

Every prompt is cancellable by typing 'c' (or 'cancel'), which raises Cancelled.
The edit menu catches it and returns to the menu, so the user can abort any step.
"""
from __future__ import annotations


class Cancelled(Exception):
    """Raised when the user types 'c' at a prompt to abort the current action."""


def _check_cancel(raw: str):
    if raw.lower() in ("c", "cancel"):
        raise Cancelled()


def ask_str(prompt, default=None):
    d = f" [{default}]" if default is not None else ""
    raw = input(f"{prompt}{d} (c=cancel): ").strip()
    _check_cancel(raw)
    return raw if raw else (default if default is not None else "")


def ask_int(prompt, lo, hi, default=None):
    while True:
        d = f" (default {default})" if default is not None else ""
        raw = input(f"{prompt} [{lo}-{hi}]{d} (c=cancel): ").strip()
        _check_cancel(raw)
        if not raw and default is not None:
            return default
        try:
            v = int(raw)
            if lo <= v <= hi:
                return v
            print(f"  Out of range ({lo}-{hi}).")
        except ValueError:
            print("  Please enter a number (or 'c' to cancel).")


def ask_yesno(prompt, default=True):
    d = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{d}] (c=cancel): ").strip().lower()
    _check_cancel(raw)
    if not raw:
        return default
    return raw.startswith("y")


def ask_menu(title, items):
    """items: list of labels. Returns chosen index (0-based). Raises Cancelled on 'c'."""
    print(f"\n  {title}")
    for i, label in enumerate(items):
        print(f"    [{i + 1}]  {label}")
    while True:
        raw = input("  Choose # (c=cancel): ").strip()
        _check_cancel(raw)
        try:
            v = int(raw)
            if 1 <= v <= len(items):
                return v - 1
        except ValueError:
            pass
        print("  Invalid choice.")
