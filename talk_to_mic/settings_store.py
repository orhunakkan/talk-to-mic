"""Saves settings changed in the UI back to config.toml, keeping the user's comments and layout."""

from __future__ import annotations

import copy
import logging
import os
from dataclasses import fields
from pathlib import Path

import tomlkit

from .config import CONFIG_PATH, DEFAULT_CONFIG_TEXT, Config, parse_config

log = logging.getLogger(__name__)


def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None:
    """Writes every setting in cfg to path. Unknown keys and comments already in the file are kept."""
    try:
        doc = tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        doc = tomlkit.parse(DEFAULT_CONFIG_TEXT)
    except tomlkit.exceptions.ParseError as exc:
        log.warning("%s could not be parsed (%s); rewriting it from the defaults", path, exc)
        doc = tomlkit.parse(DEFAULT_CONFIG_TEXT)
    defaults = tomlkit.parse(DEFAULT_CONFIG_TEXT)
    for section_field in fields(cfg):
        name = section_field.name
        section = getattr(cfg, name)
        if name not in doc:
            # Bring the section in with its explanatory comments from the default file.
            if not tomlkit.dumps(doc).endswith("\n\n"):
                doc.add(tomlkit.nl())
            doc.add(name, copy.deepcopy(defaults[name]) if name in defaults else tomlkit.table())
        table = doc[name]
        for f in fields(section):
            value = getattr(section, f.name)
            if f.name not in table or table[f.name] != value:
                table[f.name] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(tomlkit.dumps(doc), encoding="utf-8")
    os.replace(tmp, path)
    # Never leave a file behind that the app itself can't read back.
    parse_config(path.read_text(encoding="utf-8"))


def copy_into(target: Config, source: Config) -> None:
    """Updates target's sections in place, so objects holding a section see the new values."""
    for section_field in fields(target):
        dst, src = getattr(target, section_field.name), getattr(source, section_field.name)
        for f in fields(dst):
            setattr(dst, f.name, copy.deepcopy(getattr(src, f.name)))
