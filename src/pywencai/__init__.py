import logging

from . import convert as _convert_module
from . import headers as _headers_module
from . import wencai as _wencai_module
from .wencai import get


def _module_logger_specs():
    return (
        (_headers_module, logging.INFO),
        (_convert_module, logging.DEBUG),
        (_wencai_module, logging.DEBUG),
    )


def configure_logger(logger_or_name):
    """Route pywencai logs to a host application's logger."""
    if isinstance(logger_or_name, str):
        target_logger = logging.getLogger(logger_or_name)
    elif isinstance(logger_or_name, logging.Logger):
        target_logger = logger_or_name
    else:
        raise TypeError("logger_or_name must be a logger name or logging.Logger")

    for module, _level in _module_logger_specs():
        module.logger = target_logger
    return target_logger


def reset_logger():
    """Restore default module-level loggers."""
    for module, level in _module_logger_specs():
        module.logger = logging.getLogger(module.__name__)
        module.logger.setLevel(level)


__all__ = ["get", "configure_logger", "reset_logger"]
