import { Database, ArrowRight } from "lucide-react";
import { NodeCard } from "./NodeCard";
import { VMCard } from "./VMCard";
import { VLANLegend } from "./VLANLegend";

export function InfrastructureDiagram() {
  return (
    <div className="bg-white p-8">
      {/* Layer 1 - Physical Server Container */}
      <div className="bg-blue-100 rounded-2xl p-6 pb-8">
        {/* FIX 6 – pill reflects 3 physical servers */}
        <div className="mb-4">
          <span className="inline-block bg-[#1e3a8a] text-white px-4 py-2 rounded-full text-sm">
            3 × Serveurs Physiques · Proxmox VE Bare Metal
          </span>
        </div>

        {/* Layer 2 - Cluster Container */}
        <div className="bg-white rounded-xl border-2 border-dashed border-blue-500 p-6 w-full">
          {/* Cluster Label */}
          <h2 className="text-center font-bold text-lg text-gray-900 mb-4">
            Cluster Proxmox VE
          </h2>

          {/* Three Node Cards – FIX 1: vlanColors in NodeCard already match legend */}
          <div className="flex gap-4 mb-6">
            <NodeCard name="PVE-01" ip="192.168.10.100" />
            <NodeCard name="PVE-02" ip="192.168.10.213" hasCephMgr="actif" />
            <NodeCard name="PVE-03" ip="192.168.10.84" hasCephMgr="standby" />
          </div>

          {/* Ceph Pool Bar */}
          <div className="bg-amber-50 border-2 border-dashed border-amber-500 rounded-lg p-4 flex items-center justify-between">
            <div className="font-bold text-gray-900">Ceph Pool</div>

            <div className="flex items-center gap-3">
              <div className="flex flex-col items-center">
                <Database className="w-8 h-8 text-amber-600" />
                <span className="text-xs text-gray-700">OSD-01</span>
              </div>
              <ArrowRight className="w-5 h-5 text-gray-400" />
              <div className="flex flex-col items-center">
                <Database className="w-8 h-8 text-amber-600" />
                <span className="text-xs text-gray-700">OSD-02</span>
              </div>
              <ArrowRight className="w-5 h-5 text-gray-400" />
              <div className="flex flex-col items-center">
                <Database className="w-8 h-8 text-amber-600" />
                <span className="text-xs text-gray-700">OSD-03</span>
              </div>
            </div>

            <div className="text-sm text-gray-700">
              ~300GB utilisables · Réplication x3
            </div>
          </div>

          {/* VM Section */}
          <div className="mt-6 flex gap-6 w-full flex-nowrap">
            {/* Left subsection – Sona-Web */}
            <div className="flex-[2] min-w-0">
              <h3 className="font-bold text-gray-900 mb-3">
                VMs Applicatives · Sona-Web
              </h3>
              <div className="grid grid-cols-4 gap-3 min-w-0">
                <VMCard
                  name="HAProxy"
                  subtitle="Load Balancer"
                  borderColor="#ef4444"
                  badges={["DMZ 172.16.80.0/24"]}
                />
                <VMCard
                  name="Apache VM 1"
                  borderColor="#10b981"
                  badges={["Ceph RBD", "Prod 172.16.70.0/24"]}
                />
                <VMCard
                  name="Apache VM 2"
                  borderColor="#10b981"
                  badges={["Ceph RBD", "Prod 172.16.70.0/24"]}
                />
                {/* FIX 7 – HA activé green badge inserted after Ceph RBD */}
                <VMCard
                  name="PostgreSQL"
                  borderColor="#8b5cf6"
                  badges={[
                    "Ceph RBD",
                    { label: "HA activé", bgColor: "#16a34a" },
                    "Prod 172.16.70.0/24",
                  ]}
                  failoverText="cible du failover"
                />
              </div>
            </div>

            {/* Right subsection – Infrastructure
                FIX 2: teal / purple / blue left borders
                FIX 3: OPNsense content
                FIX 4: CLBS content + purple border
                FIX 5: Monitoring VM content + blue border */}
            <div className="flex-1 min-w-0">
              <h3 className="font-bold text-gray-900 mb-3">
                Services d'Infrastructure
              </h3>
              <div className="grid grid-cols-3 gap-3 min-w-0">
                {/* FIX 3 */}
                <VMCard
                  name="OPNsense"
                  subtitle="Routeur · Firewall"
                  subtitle2="WAN 192.168.10.65"
                  borderColor="#0d9488"
                  badges={["VLAN 10 + bridges"]}
                />
                {/* FIX 4 */}
                <VMCard
                  name="CLBS"
                  subtitle="Équilibrage de charge"
                  subtitle2="Python · API Proxmox"
                  borderColor="#8b5cf6"
                  badges={["VLAN 10"]}
                />
                {/* FIX 5 */}
                <VMCard
                  name="Monitoring VM"
                  subtitle="InfluxDB · Prometheus"
                  subtitle2="Grafana · Métriques"
                  borderColor="#3b82f6"
                  badges={["VLAN 10"]}
                />
              </div>
            </div>
          </div>
        </div>

        {/* VLAN Legend */}
        <VLANLegend />
      </div>
    </div>
  );
}
