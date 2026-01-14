import logging
from .config import LOG_LEVEL

__version__ = '0.2.1'


# Configure logging
log = logging.getLogger("pipu-cli")


class LevelSpecificFormatter(logging.Formatter):
    """
    A logging formatter that prefexes every level but INFO
    """
    NORMAL_FORMAT = "%(message)s"
    LEVEL_SPECIFIC_FORMAT = "%(levelname)s: %(message)s"

    def __init__(self):
        super().__init__(fmt=self.NORMAL_FORMAT, datefmt=None, style='%')

    def format(self, record: logging.LogRecord) -> str:
        """
        Format a log record based on the log level.

        :param record: Record to format
        :returns: Formatted record string
        """
        if record.levelno == logging.INFO:
            self._style._fmt = self.NORMAL_FORMAT
        else:
            self._style._fmt = self.LEVEL_SPECIFIC_FORMAT

        # Call the original formatter class to do the grunt work
        result = logging.Formatter.format(self, record)

        return result


############################
# Configure the logger
############################

ch = logging.StreamHandler()
ch.setLevel(LOG_LEVEL)

ch.setFormatter(LevelSpecificFormatter())
log.addHandler(ch)

log.setLevel(LOG_LEVEL)
