# WoT 0.9.22 Map Observer (fork)

An offline observer / map viewer for **World of Tanks 0.9.22** (WOTClassicReborn).
It lets you enter any map alone, choose a tank, and drive around / inspect the map without a real battle.

> Fork of the original project **WOTClassicReborn/WoT_0.9.22_Map_observer**. Thanks to the original authors.

## Features
- Tank selection via JSON config (`vehicle.json`).
- Physics configuration via JSON (`physics.json`): engine power, speeds, rotation, brakes, and terrain resistance.
- Hot reload for map/observer settings without restarting the game.
- Correct reload indicators for both regular guns and autoloaders.
- Fixed turret rotation.

## Hotkeys
- **Ctrl+M** — load physics settings (`physics.json`).
- **Ctrl+G** — return to login.

## Installation
1. Copy `izeberg.observer_1.0.4.2.wotmod` and `poliroid.modslistapi_1.1.0a.wotmod` to:
   `WoT\mods\0.9.22.0\`
2. Copy `vehicle.json` and `physics.json` to:
   `WoT\mods\configs\mod_observer\`

## Disclaimer
This is an unofficial modification. It is not affiliated with Wargaming. *World of Tanks* is a trademark of Wargaming.
The mod is intended for offline map viewing on the 0.9.22 client (classic server).
Use it at your own risk; the rules of each specific server are defined by that server.
This repository contains only the mod's own code and does not include proprietary game files.
