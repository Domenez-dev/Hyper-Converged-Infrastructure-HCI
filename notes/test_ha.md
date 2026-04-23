## Pourquoi ça n'a pas marché

Quand tu arrêtes seulement `pve-ha-lrm` et `pve-ha-crm`, le nœud **reste visible dans le cluster** via Corosync. Les autres nœuds voient que `pve-03` est toujours **alive** → pas de failover déclenché.

> Le HA se déclenche uniquement quand le **nœud est considéré mort** (perte du heartbeat Corosync), pas juste quand les services HA s'arrêtent.

---

## La bonne méthode — Couper le heartbeat Corosync

### Sur pve-03, bloquer le port Corosync :

```bash
# Bloquer le heartbeat cluster (port 5405 UDP)
iptables -I INPUT -p udp --dport 5405 -j DROP
iptables -I OUTPUT -p udp --dport 5405 -j DROP
iptables -I INPUT -p udp --sport 5405 -j DROP
iptables -I OUTPUT -p udp --sport 5405 -j DROP
```

### Sur pve-01 (Terminal 1) — surveiller :

```bash
watch -n 1 ha-manager status
```

### Ce que tu dois voir (après ~30-60 secondes) :

```
quorum OK
master pve-01 (active, ...)       ← nouveau master élu
lrm pve-01 (active, ...)
lrm pve-02 (active, ...)
lrm pve-03 (unknown, ...)          ← pve-03 détecté mort
service vm:100 (pve-01, started)   ← migré automatiquement !
service vm:101 (pve-01, started)
service ct:102 (pve-02, started)
```

---

## Aussi surveiller en parallèle

```bash
# Terminal 2 — logs HA en direct
journalctl -fu pve-ha-crm

# Terminal 3 — état du quorum
watch -n 1 pvecm status
```

---

## Restaurer pve-03 après le test

```bash
# Sur pve-03 — supprimer les règles iptables
iptables -D INPUT -p udp --dport 5405 -j DROP
iptables -D OUTPUT -p udp --dport 5405 -j DROP
iptables -D INPUT -p udp --sport 5405 -j DROP
iptables -D OUTPUT -p udp --sport 5405 -j DROP
```

Après ça, `pve-03` rejoint le cluster, le **fencing** (si configuré) ou le **watchdog** reboot le nœud automatiquement → il revient proprement dans le cluster.

---

## Si toujours pas de migration → vérifier ces points

```bash
# Sur pve-01 — le disque de vm:100 est bien sur CEPH ?
pvesh get /nodes/pve-03/qemu/100/config | grep -E "scsi|virtio|ide|sata"
# → doit montrer quelque chose comme : scsi0: ceph-pool:vm-100-disk-0

# Vérifier la fence policy du cluster
cat /etc/pve/ha/crm_commands
cat /etc/pve/ha/manager_status

# Si watchdog pas configuré, forcer le mode sans fencing pour les tests
```

> ⚠️ **Point crucial** : Sans watchdog/fencing configuré, Proxmox hésite à faire le failover par sécurité (risque de **split-brain**). C'est probablement la vraie raison pour laquelle ça ne migre pas.

---

## Résumé du flow HA correct

```
pve-03 perd Corosync
       ↓ (~30s timeout)
CRM détecte nœud mort
       ↓
Watchdog fence pve-03 (reboot forcé)
       ↓
VMs de pve-03 démarrées sur pve-01/pve-02
```

Sans l'étape **watchdog fence** → le CRM attend et ne migre pas pour éviter que la VM tourne sur 2 nœuds en même temps.
