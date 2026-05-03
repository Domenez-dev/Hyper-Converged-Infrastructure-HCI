interface VLANInfo {
  vlan: number;
  name: string;
  subnet: string;
  color: string;
}

const vlans: VLANInfo[] = [
  { vlan: 10, name: "Management", subnet: "192.168.10.0/24", color: "#3b82f6" },
  { vlan: 20, name: "Corosync", subnet: "192.168.20.0/24", color: "#1e3a8a" },
  { vlan: 30, name: "Ceph Cluster", subnet: "10.10.30.0/24", color: "#f59e0b" },
  { vlan: 40, name: "Ceph Public", subnet: "10.10.40.0/24", color: "#fb923c" },
  { vlan: 50, name: "Live Migration", subnet: "10.10.50.0/24", color: "#8b5cf6" },
  { vlan: 60, name: "Réseau Dev", subnet: "172.16.60.0/24", color: "#10b981" },
  { vlan: 70, name: "Réseau Prod", subnet: "172.16.70.0/24", color: "#84cc16" },
  { vlan: 80, name: "DMZ External", subnet: "172.16.80.0/24", color: "#ef4444" },
  { vlan: 90, name: "Backup PBS", subnet: "10.10.90.0/24", color: "#6b7280" },
];

export function VLANLegend() {
  return (
    <div className="grid grid-cols-9 w-full mt-6">
      {vlans.map((vlan) => (
        <div
          key={vlan.vlan}
          className="py-3 px-2 text-center"
          style={{ backgroundColor: vlan.color }}
        >
          <div className="text-white font-bold text-sm">VLAN {vlan.vlan}</div>
          <div className="text-white text-xs mt-0.5">{vlan.name}</div>
          <div className="text-white text-xs opacity-90">{vlan.subnet}</div>
        </div>
      ))}
    </div>
  );
}
