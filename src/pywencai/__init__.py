import logging

from . import convert as _convert_module
from . import headers as _headers_module
from . import wencai as _wencai_module
from .wencai import get


def _module_logger_specs():
    return (
        (_headers_module, logging.INFO),
        (_convert_module, logging.INFO),
        (_wencai_module, logging.INFO),
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
        module.logger = _headers_module.runtime_logger(target_logger)
    return target_logger


def configure_runtime_logging(enabled):
    """Globally enable or disable pywencai runtime logs for the current process."""
    _wencai_module.set_runtime_logging_enabled(enabled)
    return _wencai_module.is_runtime_logging_enabled()


def is_runtime_logging_enabled():
    """Return whether pywencai runtime logs are enabled for the current process."""
    return _wencai_module.is_runtime_logging_enabled()


def reset_logger():
    """Restore default module-level loggers."""
    for module, level in _module_logger_specs():
        module.logger = _headers_module.runtime_logger(logging.getLogger(module.__name__))
        module.logger.setLevel(level)
    _wencai_module.set_runtime_logging_enabled(True)


__all__ = [
    "get",
    "configure_logger",
    "configure_runtime_logging",
    "is_runtime_logging_enabled",
    "reset_logger",
]
