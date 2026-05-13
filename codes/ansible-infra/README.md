# Proxmox VM Provisioner

Ansible playbook that clones `debian-template` (ID 101) into a new VM with:
- A static IP derived from the VM ID: `172.16.60.(vm_id - 90)`
- Gateway `172.16.60.1`
- Configurable CPU/RAM (defaults match the template: 4 GB RAM, 2 cores)
- Post-boot IP verification and `apt update/upgrade`

## Directory layout

```
.
├── ansible.cfg
├── secrets.yml              # API credentials - encrypt with ansible-vault!
├── inventory/
│   ├── hosts.yml
│   └── proxmox_nodes.yml
└── playbooks/
    ├── provision_vm.yml       # single VM
    └── provision_vm_batch.yml # multiple VMs at once
```

## Prerequisites

```bash
# Ansible + Proxmox collection
pip install ansible --break-system-packages   # or via pacman on Arch
ansible-galaxy collection install community.proxmox
```

## Setup

1. Fill in `secrets.yml` with your PVE API token and storage pool name.
   Optionally encrypt it:
   ```bash
   ansible-vault encrypt secrets.yml
   ```

2. Adjust `ansible.cfg` if needed (it already points to `inventory/hosts.yml`).

## Usage

### Single VM

```bash
# Minimal - uses template defaults (2 cores, 4 GB)
ansible-playbook playbooks/provision_vm.yml \
  -e "vm_id=110 vm_name=web-01"

# Custom CPU and RAM
ansible-playbook playbooks/provision_vm.yml \
  -e "vm_id=115 vm_name=db-01 vm_cores=4 vm_memory=8192"

# Different proxmox node
ansible-playbook playbooks/provision_vm.yml \
  -e "vm_id=120 vm_name=app-01 target_node=pve-02"

# Resize disk too (default is whatever the template has)
ansible-playbook playbooks/provision_vm.yml \
  -e "vm_id=110 vm_name=big-disk vm_disk=50 vm_disk_resize=true"

# With vault password
ansible-playbook playbooks/provision_vm.yml \
  -e "vm_id=110 vm_name=web-01" --ask-vault-pass
```

### IP formula quick reference

| VM ID | Last octet | Full IP        |
|-------|-----------|----------------|
| 110   | 20        | 172.16.60.20   |
| 111   | 21        | 172.16.60.21   |
| 150   | 60        | 172.16.60.60   |
| 200   | 110       | 172.16.60.110  |

### Batch provisioning

Edit the `vms` list in `provision_vm_batch.yml`, then:

```bash
ansible-playbook playbooks/provision_vm_batch.yml
```

Or supply a vars file:

```bash
# my_vms.yml
vms:
  - id: 110
    name: web-01
  - id: 111
    name: db-01
    cores: 4
    memory: 8192

ansible-playbook playbooks/provision_vm_batch.yml -e @my_vms.yml
```

## What the playbook does

1. **Clone** `debian-template` (full clone, not linked)
2. **Configure hardware** - cores, RAM, NIC on VLAN 60
3. **Set cloud-init IP** - static, derived from `vm_id - 90`
4. **Start** the VM
5. **Wait** for SSH to come up (up to 180 s)
6. **Verify IP** - assert the expected address is actually live
7. **apt update + upgrade** - fully patch the new VM
8. **Reboot notice** - warns if `/var/run/reboot-required` exists
