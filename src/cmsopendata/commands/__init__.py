import importlib
import pkgutil

import click


def register_commands(cli: click.Group) -> None:
    package = __name__

    for _, module_name, _ in pkgutil.iter_modules(__path__):
        module = importlib.import_module(f"{package}.{module_name}")
        if hasattr(module, "command"):
            cli.add_command(module.command)
