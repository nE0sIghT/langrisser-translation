"""Langrisser translation toolkit.

Modules are grouped by what they operate on rather than by which game or
console needs them, because almost none of them are specific to one: the SCEN
and SYSTEM tools serve every release built on the l45 engine, and the four
descriptor modules (`game`, `release`, `platform`, `engine`) serve all of
them. The `saturn_` prefix marks the tools that read a Saturn-only container,
not tools for one game.

Each module is also a command: `python3 -m langrisser.saturn_build`.
"""
