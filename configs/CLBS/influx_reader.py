from influxdb_client import InfluxDBClient
from config import INFLUX_URL, INFLUX_TOKEN, INFLUX_ORG, INFLUX_BUCKET

class InfluxReader:
    def __init__(self):
        self.client    = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
        self.query_api = self.client.query_api()

    def _query(self, flux: str) -> dict[str, float]:
        result = {}
        for table in self.query_api.query(flux):
            for record in table.records:
                host  = record.values.get("host", "")
                value = record.get_value()
                if host and value is not None:
                    result[host] = round(float(value), 2)
        return result

    def get_vm_cpu(self, node_name: str) -> dict[str, float]:
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -15m)
          |> filter(fn: (r) => r._measurement == "system")
          |> filter(fn: (r) => r._field == "cpu")
          |> filter(fn: (r) => r["object"] == "qemu")
          |> filter(fn: (r) => r["_value"] > 0)
          |> filter(fn: (r) => r["nodename"] == "{node_name}")
          |> group(columns: ["host"])
          |> mean()
        '''
        raw = self._query(flux)
        return {host: round(val * 100.0, 2) for host, val in raw.items()}

    def get_ct_cpu(self, node_name: str) -> dict[str, float]:
        flux = f'''
        from(bucket: "{INFLUX_BUCKET}")
          |> range(start: -15m)
          |> filter(fn: (r) => r._measurement == "system")
          |> filter(fn: (r) => r._field == "cpu")
          |> filter(fn: (r) => r["object"] == "lxc")
          |> filter(fn: (r) => r["_value"] > 0)
          |> filter(fn: (r) => r["nodename"] == "{node_name}")
          |> group(columns: ["host"])
          |> mean()
        '''
        raw = self._query(flux)
        return {host: round(val * 100.0, 2) for host, val in raw.items()}
