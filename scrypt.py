#!/usr/bin/env python3
"""SCRYPT - a small Nix application launcher and NixOS maintenance tool."""

import json
import os
import re
import shlex
import socket
import subprocess
import sys
from pathlib import Path

FLAKE_DIR = Path(os.environ.get("SCRYPT_FLAKE_DIR", "/etc/nixos")).expanduser().resolve()
PACKAGE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._+-]*")

BANNER = """
  ███████╗ ██████╗██████╗ ██╗   ██╗██████╗ ████████╗
  ██╔════╝██╔════╝██╔══██╗╚██╗ ██╔╝██╔══██╗╚══██╔══╝
  ███████╗██║     ██████╔╝ ╚████╔╝ ██████╔╝   ██║
  ╚════██║██║     ██╔══██╗  ╚██╔╝  ██╔══      ██║
  ███████║╚██████╗██║  ██║   ██║   ██║        ██║
  ╚══════╝ ╚═════╝╚═╝  ╚═╝   ╚═╝   ╚═╝        ╚═╝

       SCRYPT - Nix Utility
"""


def ask(prompt):
    return input(prompt).strip()


def capture(cmd, **kwargs):
    """Run a command, capturing its text output without raising on failure."""
    return subprocess.run(cmd, capture_output=True, text=True, **kwargs)


# ==============================
#        APPLICATION LAUNCHER
# ==============================

def resolve_package(package):
    """Return the package's display name, or None (after printing why) if invalid."""
    if not PACKAGE_RE.fullmatch(package):
        print("Error: Invalid package name.")
        return None

    result = capture(["nix", "eval", "--raw", f"nixpkgs#{package}.name"])
    if result.returncode != 0:
        print(f"Error: Could not resolve package '{package}'.")
        if result.stderr:
            print(result.stderr.strip())
        return None

    return result.stdout.strip()


def default_command(package):
    """Use the package's declared main program if it has one, else its name."""
    result = capture(["nix", "eval", "--raw", f"nixpkgs#{package}.meta.mainProgram"])
    return result.stdout.strip() if result.returncode == 0 and result.stdout.strip() else package


def command_exists(package, executable):
    result = subprocess.run(
        ["nix", "shell", f"nixpkgs#{package}", "--command",
         "sh", "-c", 'command -v "$1"', "sh", executable],
        capture_output=True,
    )
    return result.returncode == 0


def prompt_package():
    """Ask for a valid package. Returns None if the user exits."""
    while True:
        package = ask("\nNix package (or 'exit'): ")

        if package.lower() == "exit":
            return None
        if not package:
            print("Error: Must enter a package.")
            continue

        name = resolve_package(package)
        if name:
            print(f"Package found: {name}")
            return package


def prompt_command(package):
    """Ask for a runnable command inside the package's shell. Returns a list or None."""
    default = default_command(package)

    while True:
        command = ask(f"Command [{default}] (or 'exit'): ") or default

        if command.lower() == "exit":
            return None

        try:
            parts = shlex.split(command)
        except ValueError as e:
            print(f"Invalid command syntax: {e}")
            continue

        if not parts:
            print("Error: Must enter a command.")
            continue

        if not command_exists(package, parts[0]):
            print(f"Error: Command '{parts[0]}' not found in '{package}'. Please try again.")
            continue

        return parts


def launch_package():
    package = prompt_package()
    if package is None:
        return

    command = prompt_command(package)
    if command is None:
        return

    print(f"\nExecuting {shlex.join(command)}...\n")
    result = subprocess.run(["nix", "shell", f"nixpkgs#{package}", "--command", *command])

    if result.returncode != 0:
        print(f"Application exited with code {result.returncode}")


# ==============================
#        GARBAGE COLLECTION
# ==============================

def garbage_collect():
    print("\nStarting Nix garbage collection...\n")
    subprocess.run(["nix-collect-garbage"], check=True)
    print("\nGarbage collection completed.")


# ==============================
#          SYSTEM UPDATE
# ==============================

def list_configurations():
    """Return the flake's nixosConfigurations names, or None (after printing why)."""
    if not (FLAKE_DIR / "flake.nix").is_file():
        print(f"Error: No flake.nix found in {FLAKE_DIR}")
        return None

    result = capture(
        ["nix", "eval", "--json", ".#nixosConfigurations", "--apply", "builtins.attrNames"],
        cwd=FLAKE_DIR,
    )
    if result.returncode != 0:
        print("Error: Could not read NixOS flake configurations.")
        if result.stderr:
            print(result.stderr.strip())
        return None

    configurations = sorted(json.loads(result.stdout))
    if not configurations:
        print("Error: No NixOS configurations found.")
        return None

    return configurations


def choose_configuration():
    """Pick the configuration matching this host, or ask. Returns None if cancelled."""
    hostname = socket.gethostname()
    print(f"\nDetected hostname: {hostname}")

    configurations = list_configurations()
    if configurations is None:
        return None

    # Match the full hostname, then the short one (in case it is a FQDN).
    for candidate in (hostname, hostname.split(".")[0]):
        if candidate in configurations:
            print(f"Detected configuration: {candidate}")
            return candidate

    print(f"\nNo configuration matching '{hostname}' found.")
    print("\nAvailable configurations:")
    for i, name in enumerate(configurations, 1):
        print(f"{i}. {name}")

    while True:
        choice = ask("\nSelect configuration (or 'exit'): ")

        if choice.lower() == "exit":
            return None
        if choice.isdigit() and 1 <= int(choice) <= len(configurations):
            return configurations[int(choice) - 1]

        print(f"Please enter a number between 1 and {len(configurations)}.")


def update_system():
    print("\nStarting NixOS update...")

    configuration = choose_configuration()
    if configuration is None:
        print("Update cancelled.")
        return

    # /etc/nixos is usually root-owned, so the lock file may need sudo to update.
    update_cmd = ["sudo", "nix", "flake", "update"]

    rebuild_cmd = ["sudo", "nixos-rebuild", "switch", "--flake", f"{FLAKE_DIR}#{configuration}"]

    print("\nUpdating flake inputs...\n")
    subprocess.run(update_cmd, cwd=FLAKE_DIR, check=True)

    print("\nRebuilding NixOS...\n")
    subprocess.run(rebuild_cmd, check=True)

    print("\nNixOS successfully updated!")


# ==============================
#            MAIN MENU
# ==============================

ACTIONS = {
    "1": ("Launch a package in a nix-shell.", launch_package),
    "2": ("Garbage collection.", garbage_collect),
    "3": ("Update NixOS.", update_system),
}


def run_action(action):
    """Run a menu action; report failures without leaving the program."""
    try:
        action()
    except FileNotFoundError as e:
        print(f"Error: '{e.filename}' was not found. Is Nix installed and in your PATH?")
    except subprocess.CalledProcessError as e:
        print(f"Error: '{shlex.join(map(str, e.cmd))}' failed with exit code {e.returncode}")
    except KeyboardInterrupt:
        print("\nInterrupted.")


def main():
    print(BANNER)

    while True:
        print("\nWhat action would you like to complete?")
        for key, (label, _) in ACTIONS.items():
            print(f"{key}. {label}")
        print("4. Exit.")

        choice = ask("\nEnter action... ")

        if choice == "4":
            print("Thanks for using SCRYPT!")
            return
        if choice in ACTIONS:
            run_action(ACTIONS[choice][1])
        else:
            print("Invalid selection. Please try again.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSCRYPT exited.")
        sys.exit(0)
