Good, now you’re at the point where everything comes together.

I’ll split this into 3 parts:

1. make VMs use OPNsense as router
2. should Proxmox nodes use it?
3. clean firewall design (prod/test/dmz)

---

# 🧠 1) Make VMs use OPNsense as router

## ✅ Principle

Each VM must:

* be in a VLAN (60 / 70 / 80)
* use OPNsense IP as **gateway**

---

## 🔧 Example (PROD VLAN 60)

VM config in Proxmox:

* Bridge: `vmbr0`
* VLAN tag: `60`

Inside VM:

```bash
IP: 172.16.60.X/24
Gateway: 172.16.60.1   ← OPNsense
DNS: 8.8.8.8 (or OPNsense later)
```

---

## 🔧 TEST VLAN 70

```bash
IP: 172.16.70.X/24
Gateway: 172.16.70.1
```

---

## 🔧 DMZ VLAN 80

```bash
IP: 172.16.80.X/24
Gateway: 172.16.80.1
```

---

## 🧪 Test

From any VM:

```bash
ping 172.16.X.1        # router
ping 192.168.10.36     # your laptop
ping 8.8.8.8           # internet
```

---

# ❗ 2) Should Proxmox nodes use OPNsense?

👉 **NO (important)**

---

## Keep Proxmox like this:

```bash
Gateway: 192.168.10.36 (your laptop)
```

---

## Why?

* cluster (Corosync) must be stable
* Ceph depends on stable routing
* if OPNsense VM dies → cluster dies ❌

---

## Best practice

| Component     | Gateway                     |
| ------------- | --------------------------- |
| Proxmox nodes | your laptop (192.168.10.36) |
| VMs           | OPNsense                    |
| Ceph          | internal only               |

---

👉 Router VM = **only for VMs**, not hosts

---

# 🔥 3) Production-grade firewall rules

Now the important part.

---

# 🧱 Zones recap

| Zone | Network      | Purpose          |
| ---- | ------------ | ---------------- |
| WAN  | 192.168.10.0 | upstream         |
| PROD | 172.16.60.0  | main VMs         |
| TEST | 172.16.70.0  | isolated         |
| DMZ  | 172.16.80.0  | exposed services |

---

# 🔐 RULE DESIGN

## 🟢 LAN (PROD) — trusted

Go to:
`Firewall → Rules → LAN`

### Rules:

1. Allow all outbound:

* Source: LAN net
* Destination: any
* Action: Pass

---

## 🟡 TEST — semi-isolated

`Firewall → Rules → OPT1`

### Rules:

1. Allow internet only:

* Source: TEST net
* Destination: any
* Gateway: WAN
* Action: Pass

2. Block access to PROD:

* Source: TEST net
* Destination: 172.16.60.0/24
* Action: Block

---

## 🔴 DMZ — highly restricted

`Firewall → Rules → OPT2`

### Rules:

1. Allow internet:

* Source: DMZ net
* Destination: any
* Action: Pass

2. Block access to internal:

* Source: DMZ net
* Destination:

  * 172.16.60.0/24
  * 172.16.70.0/24
* Action: Block

---

## 🌐 WAN — strict

`Firewall → Rules → WAN`

### Rules:

1. Allow your laptop (management):

* Source: 192.168.10.36
* Destination: WAN address
* Port: 443

2. (optional) allow ping

Everything else = blocked

---

# 🔁 NAT (verify)

`Firewall → NAT → Outbound`

👉 Use:

```text
Automatic
```

---

# 🧪 Validation checklist

From PROD VM:

* can reach internet ✅
* can reach TEST ❌ (optional)
* can reach DMZ (optional)

From TEST:

* internet ✅
* PROD ❌

From DMZ:

* internet ✅
* PROD ❌
* TEST ❌

---

# 🧠 Clean architecture result

You now have:

* Proxmox → infrastructure layer
* VXLAN → transport
* OPNsense → routing + security
* VLANs → segmentation

👉 This is very close to real enterprise design

---

# 🚀 Next step (high value)

I recommend next:

* put **HAProxy in DMZ**
* expose services via OPNsense NAT
* add DNS inside OPNsense

---

If you want, I can:
👉 design your **DMZ reverse proxy architecture (clean + realistic)**
