Here’s a **clean, minimal router VM setup** for your environment. This will give internet + routing to your VM networks (VXLAN 60/70/80).

We’ll use **Debian** (simpler and transparent).

---

# 🧱 Target Design

```
VMs (VXLAN 60/70/80)
        ↓
Router VM
        ↓
VLAN 10 (192.168.10.x)
        ↓
Your laptop (gateway)
        ↓
Internet
```

---

# 1) Create the Router VM

In Proxmox:

* OS: Debian 12
* CPU: 1–2 cores
* RAM: 1–2 GB
* Disk: 10–20 GB

---

## Add Network Interfaces

Add **4 NICs**:

| NIC  | Bridge | VLAN Tag | Purpose       |
| ---- | ------ | -------- | ------------- |
| net0 | vmbr0  | none     | WAN (VLAN 10) |
| net1 | vmbr0  | 60       | Production    |
| net2 | vmbr0  | 70       | Test          |
| net3 | vmbr0  | 80       | DMZ           |

---

# 2) Install Debian (inside VM)

Login and install basics:

```bash id="v7mt0b"
apt update
apt install iproute2 iptables net-tools
```

---

# 3) Configure Network Interfaces

Edit:

```bash id="ydt6xp"
nano /etc/network/interfaces
```

---

## Example config:

```bash id="2py2zy"
auto lo
iface lo inet loopback

# WAN (VLAN 10)
auto eth0
iface eth0 inet static
    address 192.168.10.50/24
    gateway 192.168.10.36

# PROD (VXLAN 60)
auto eth1
iface eth1 inet static
    address 172.16.60.1/24

# TEST (VXLAN 70)
auto eth2
iface eth2 inet static
    address 172.16.70.1/24

# DMZ (VXLAN 80)
auto eth3
iface eth3 inet static
    address 172.16.80.1/24
```

---

Apply:

```bash id="mb3x0i"
systemctl restart networking
```

---

# 4) Enable IP Forwarding

```bash id="a3l9it"
echo 1 > /proc/sys/net/ipv4/ip_forward
```

Persist:

```bash id="teahp7"
nano /etc/sysctl.conf
```

Add:

```bash id="57ycyt"
net.ipv4.ip_forward=1
```

---

# 5) Configure NAT (internet access)

```bash id="ycfb83"
iptables -t nat -A POSTROUTING -o eth0 -j MASQUERADE
```

Allow forwarding:

```bash id="m3sx89"
iptables -A FORWARD -i eth0 -o eth1 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i eth1 -o eth0 -j ACCEPT

iptables -A FORWARD -i eth0 -o eth2 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i eth2 -o eth0 -j ACCEPT

iptables -A FORWARD -i eth0 -o eth3 -m state --state RELATED,ESTABLISHED -j ACCEPT
iptables -A FORWARD -i eth3 -o eth0 -j ACCEPT
```

---

## Save rules:

```bash id="r2gy0j"
apt install iptables-persistent
netfilter-persistent save
```

---

# 6) Configure your VMs

Example (Production VM):

* VLAN tag: `60`
* IP: `172.16.60.10`
* Gateway: `172.16.60.1`
* DNS: `8.8.8.8`

---

# 7) Test

From a VM:

```bash id="2ch7wg"
ping 172.16.60.1     # router
ping 192.168.10.36   # your laptop
ping 8.8.8.8         # internet
```

---

# 8) (Optional but recommended) Add DNS

Install:

```bash id="pqip3m"
apt install dnsmasq
```

Basic config:

```bash id="y0egyw"
nano /etc/dnsmasq.conf
```

Add:

```bash id="1r3pm9"
interface=eth1
dhcp-range=172.16.60.100,172.16.60.200,12h
```

---

# 🧠 What you achieved

* Full routing between VXLAN networks
* Internet access for all VMs
* Isolation (Prod / Test / DMZ separated)

---

# ⚠️ Common mistakes

* Forgetting VLAN tag on VM NIC
* Wrong gateway in VM
* Not enabling IP forwarding
* Missing NAT rule

---

# 🚀 Next logical steps

1. Put **HAProxy in DMZ (VLAN 80)**
2. Add firewall rules (isolate networks)
3. Connect Backup (VXLAN 90)
4. Tune MTU if Ceph complains

---

If you want next:

* I can give you **strict firewall rules (prod ↔ dmz ↔ test isolation)**
* or a **pfSense version (GUI-based, cleaner long-term)**
