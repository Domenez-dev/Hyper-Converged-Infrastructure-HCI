import logging


class ColorFormatter(logging.Formatter):
    GREY     = "\033[38;5;245m"
    GREEN    = "\033[32m"
    YELLOW   = "\033[33m"
    RED      = "\033[31m"
    BOLD_RED = "\033[1;31m"
    CYAN     = "\033[36m"
    RESET    = "\033[0m"

    # Value highlight colors
    TEAL     = "\033[38;5;80m"    # threshold / band bounds
    ORANGE   = "\033[38;5;214m"   # heavy node / WTV
    LIME     = "\033[38;5;154m"   # light node / destination
    PURPLE   = "\033[38;5;183m"   # selected VM
    BLUE     = "\033[38;5;117m"   # candidate VM diff

    BOLD     = "\033[1m"

    LEVEL_COLORS = {
        logging.DEBUG:    CYAN,
        logging.INFO:     GREEN,
        logging.WARNING:  YELLOW,
        logging.ERROR:    RED,
        logging.CRITICAL: BOLD_RED,
    }

    @staticmethod
    def _pct_color(pct: float) -> str:
        if pct >= 80:
            return "\033[31m"
        if pct >= 50:
            return "\033[33m"
        return "\033[32m"

    @classmethod
    def cpu(cls, val: float) -> str:
        return f"{cls._pct_color(val)}{val:.1f}%{cls.RESET}"

    @classmethod
    def ram(cls, val: float) -> str:
        return f"{cls._pct_color(val)}{val:.1f}%{cls.RESET}"

    @classmethod
    def candidate(cls, val: str) -> str:
        return f"{cls.CYAN}{val}{cls.RESET}"

    @classmethod
    def selected(cls, val: str) -> str:
        return f"{cls.LIME}{val}{cls.RESET}"

    @classmethod
    def node(cls, name: str, band: str) -> str:
        color = {
            "heavy":    cls.ORANGE,
            "light":    cls.LIME,
            "moderate": cls.GREY,
        }.get(band, cls.RESET)
        return f"{color}{cls.BOLD}{name}{cls.RESET}"

    @classmethod
    def vm_name(cls, name: str) -> str:
        return f"{cls.PURPLE}{name}{cls.RESET}"

    @classmethod
    def threshold(cls, val: float) -> str:
        return f"{cls.TEAL}{cls.BOLD}{val:.1f}%{cls.RESET}"

    @classmethod
    def wtv(cls, val: float) -> str:
        return f"{cls.ORANGE}{val:.1f}%{cls.RESET}"

    @classmethod
    def diff(cls, val: float) -> str:
        return f"{cls.BLUE}{val:.1f}%{cls.RESET}"

    def format(self, record):
        level_color = self.LEVEL_COLORS.get(record.levelno, self.RESET)
        time_str    = self.formatTime(record, "%Y-%m-%d %H:%M:%S")
        return (
            f"{self.GREY}{time_str}{self.RESET} "
            f"[{level_color}{record.levelname}{self.RESET}] "
            f"{record.getMessage()}"
        )


def get_logger():
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter())

    log = logging.getLogger("drs")
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    return log
