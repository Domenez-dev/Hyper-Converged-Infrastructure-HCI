import { Database, ArrowRight } from "lucide-react";
import { NodeCard } from "./NodeCard";
import { VMCard } from "./VMCard";
import { VLANLegend } from "./VLANLegend";

export function InfrastructureDiagram() {
  return (
    <div className="bg-white p-8">
      {/* Layer 1 - Physical Server Container */}
      <div className="bg-blue-100 rounded-2xl p-6 pb-8">
        {/* Server Label */}
        <div className="mb-4">
          <span className="inline-block bg-[#1e3a8a] text-white px-4 py-2 rounded-full text-sm">
            Serveur Physique Hôte - Proxmox VE Bare Metal
          </span>
        </div>

        {/* Layer 2 - Cluster Container */}
        <div className="bg-white rounded-xl border-2 border-dashed border-blue-500 p-6 w-full">
          {/* Cluster Label */}
          <h2 className="text-center font-bold text-lg text-gray-900 mb-4">
            Cluster Proxmox VE
          </h2>

          {/* Three Node Cards */}
          <div className="flex gap-4 mb-6">
            <NodeCard name="PVE-01" />
            <NodeCard name="PVE-02" hasCephMgr="actif" />
            <NodeCard name="PVE-03" hasCephMgr="standby" />
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
              ~256GB utilisables · Réplication x3
            </div>
          </div>

          {/* VM Section (NOW INSIDE) */}
          <div className="mt-6 flex gap-6 w-full flex-nowrap">
            {/* Left subsection */}
            <div className="flex-[2] min-w-0">
              <h3 className="font-bold text-gray-900 mb-3">
                VMs Applicatives · Sona-Web
              </h3>
              <div className="grid grid-cols-4 gap-3 min-w-0">
                <VMCard
                  name="HAProxy"
                  subtitle="Load Balancer"
                  borderColor="#ef4444"
                  badges={["VLAN 80 DMZ"]}
                />
                <VMCard
                  name="Apache VM 1"
                  borderColor="#10b981"
                  badges={["Ceph RBD", "VLAN 60"]}
                />
                <VMCard
                  name="Apache VM 2"
                  borderColor="#10b981"
                  badges={["Ceph RBD", "VLAN 60"]}
                />
                <VMCard
                  name="PostgreSQL"
                  borderColor="#8b5cf6"
                  badges={["Ceph RBD", "VLAN 60"]}
                  failoverText="cible du failover"
                />
              </div>
            </div>

            {/* Right subsection */}
            <div className="flex-1 min-w-0">
              <h3 className="font-bold text-gray-900 mb-3">
                Services d'Infrastructure
              </h3>
              <div className="grid grid-cols-2 gap-3 min-w-0">
                <VMCard
                  name="Service DRS"
                  subtitle="Python · API Proxmox"
                  borderColor="#3b82f6"
                  badges={["VLAN 10"]}
                />
                <VMCard
                  name="InfluxDB + Grafana"
                  subtitle="Monitoring · Métriques"
                  borderColor="#06b6d4"
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
