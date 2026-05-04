from config import MIN_BAND_WIDTH
from models import NodeLoad

class BandCalculator:

    def compute(self, nodes: list[NodeLoad]):
        if not nodes:
            return 0.0, 0.0, 0.0

        cpu_values = [n.cpu_pct for n in nodes]
        threshold  = sum(cpu_values) / len(cpu_values)

        cpu_min = min(cpu_values)
        cpu_max = max(cpu_values)
        mean_val = (cpu_min + cpu_max) / 2.0

        diff = abs(threshold - mean_val)
        half_width = max(diff, MIN_BAND_WIDTH)

        lower = threshold - half_width
        upper = threshold + half_width

        for node in nodes:
            if node.cpu_pct > upper:
                node.band = "heavy"
            elif node.cpu_pct < lower:
                node.band = "light"
            else:
                node.band = "moderate"

        return threshold, lower, upper
