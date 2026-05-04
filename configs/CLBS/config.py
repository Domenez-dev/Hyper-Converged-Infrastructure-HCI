import os
import dotenv

dotenv.load_dotenv()

INFLUX_URL    = os.getenv("INFLUX_URL")
INFLUX_TOKEN  = os.getenv("INFLUX_TOKEN")
INFLUX_ORG    = os.getenv("INFLUX_ORG")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET")

PVE_HOST      = os.getenv("PVE_HOST")
PVE_TOKEN_ID  = os.getenv("PVE_TOKEN_ID")
PVE_TOKEN_SEC = os.getenv("PVE_TOKEN_SEC")

RAM_HIGH = 85.0
CHECK_INTERVAL = 60
MIGRATION_COOLDOWN = 300
MIN_BAND_WIDTH = 10.0

VM_PRIORITY_BONUS = 200.0
VM_DOUBLE_MIGRATION_WINDOW = 660.0
VM_DOUBLE_MIGRATION_COOLDOWN = 1800.0

STORM_WINDOW = 1860.0
STORM_THRESHOLD = 6
STORM_PAUSE = 1800.0

EXCLUDED_VMS = {"monitoring", "OPNsense", "CLBS"}
EXCLUDED_VM_IDS = {100, 102, 106}
EXCLUDED_CTS = {"monitoring"}
EXCLUDED_CT_IDS = {102}


def validate():
    required = {
        "INFLUX_URL": INFLUX_URL,
        "INFLUX_TOKEN": INFLUX_TOKEN,
        "INFLUX_ORG": INFLUX_ORG,
        "INFLUX_BUCKET": INFLUX_BUCKET,
        "PVE_HOST": PVE_HOST,
        "PVE_TOKEN_ID": PVE_TOKEN_ID,
        "PVE_TOKEN_SEC": PVE_TOKEN_SEC,
    }

    missing = [k for k, v in required.items() if not v]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")

    if "!" not in PVE_TOKEN_ID:
        raise RuntimeError("Invalid PVE_TOKEN_ID format")
