import logging

class ColorFormatter(logging.Formatter):
    # (keep your class exactly as-is)
    ...

def get_logger():
    handler = logging.StreamHandler()
    handler.setFormatter(ColorFormatter())

    log = logging.getLogger("drs")
    log.setLevel(logging.INFO)
    log.addHandler(handler)

    return log
