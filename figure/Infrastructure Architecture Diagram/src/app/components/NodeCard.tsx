import { PillBadge } from "./PillBadge";
import { VLANBadge } from "./VLANBadge";

interface NodeCardProps {
  name: string;
  ip?: string;
  hasCephMgr?: "actif" | "standby";
}

const vlanColors = {
  10: "#3b82f6", // blue
  20: "#1e3a8a", // dark navy
  30: "#f59e0b", // amber
  40: "#fb923c", // orange
  50: "#8b5cf6", // purple
  60: "#10b981", // green
  70: "#84cc16", // light green
  80: "#ef4444", // red
  90: "#6b7280", // gray
};

export function NodeCard({ name, ip, hasCephMgr }: NodeCardProps) {
  return (
    <div className="bg-white rounded-lg border border-gray-300 p-4 flex-1 shadow-sm">
      <h3 className="text-lg font-bold text-[#1e3a8a] mb-1">{name}</h3>
      {ip && <div className="text-xs text-gray-500 mb-3">{ip}</div>}
      
      <div className="flex flex-col gap-2 mb-4">
        <PillBadge label="Ceph MON" color="#3b82f6" />
        {hasCephMgr === "actif" && <PillBadge label="Ceph MGR (actif)" color="#06b6d4" />}
        {hasCephMgr === "standby" && <PillBadge label="Ceph MGR (standby)" color="#8b5cf6" />}
        <PillBadge label="Ceph OSD" color="#f59e0b" />
        <PillBadge label="Open vSwitch" color="#10b981" />
      </div>
      
      <div className="flex flex-wrap gap-1.5">
        <VLANBadge vlan={10} color={vlanColors[10]} />
        <VLANBadge vlan={20} color={vlanColors[20]} />
        <VLANBadge vlan={30} color={vlanColors[30]} />
        <VLANBadge vlan={40} color={vlanColors[40]} />
        <VLANBadge vlan={50} color={vlanColors[50]} />
        <VLANBadge vlan={60} color={vlanColors[60]} />
        <VLANBadge vlan={70} color={vlanColors[70]} />
        <VLANBadge vlan={80} color={vlanColors[80]} />
        <VLANBadge vlan={90} color={vlanColors[90]} />
      </div>
    </div>
  );
}
