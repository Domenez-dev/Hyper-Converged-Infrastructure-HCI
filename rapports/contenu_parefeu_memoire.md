# Contenu à insérer dans le Mémoire — Configuration du Pare-feu Proxmox

> Ce document présente deux blocs de contenu prêts à intégrer dans le mémoire :
> - **Bloc A** : Enrichissement de la section III.14.2 (Conception)
> - **Bloc B** : Nouvelle section IV.4.5 (Implémentation)

---

## BLOC A — Enrichissement de la section III.14 (Chapitre III — Conception)

### III.14.2 Pare-feu Proxmox : Filtrage au Niveau Hyperviseur

Le pare-feu intégré de Proxmox VE repose sur `iptables` et s'organise selon une hiérarchie
à trois niveaux : Datacenter, Nœud, et VM. Les règles se cumulent en cascade du niveau le
plus global au plus spécifique, chaque niveau héritant des règles du niveau supérieur.

#### Architecture à trois niveaux

| Niveau      | Portée                          | Fichier de configuration               |
|-------------|----------------------------------|----------------------------------------|
| Datacenter  | S'applique à tout le cluster     | `/etc/pve/firewall/cluster.fw`         |
| Nœud        | S'applique à un serveur Proxmox  | `/etc/pve/nodes/<nom>/host.fw`         |
| VM / CT     | S'applique à une seule VM        | `/etc/pve/firewall/<vmid>.fw`          |

La politique par défaut (input policy) sera configurée en `ACCEPT` lors de la phase de
test, puis basculée en `DROP` une fois toutes les règles validées, conformément aux bonnes
pratiques de mise en service.

#### Abstractions de sécurité : Alias, IPSets et Groupes

Pour organiser les règles de façon maintenable et éviter la duplication, trois abstractions
sont définies au niveau Datacenter.

**Alias IP** — chaque nœud du cluster est nommé symboliquement :

| Alias       | Adresse IP       | Usage                              |
|-------------|------------------|------------------------------------|
| `pve1`      | 192.168.10.100   | Nœud PVE-01                        |
| `pve2`      | 192.168.10.213   | Nœud PVE-02                        |
| `pve3`      | 192.168.10.84    | Nœud PVE-03                        |
| `laptop-gw` | 192.168.10.36    | Passerelle NAT et poste admin      |

**Ensembles IP (IPSets)** — chaque réseau logique VxLAN est représenté par un IPSet :

| IPSet              | Sous-réseau        | Réseau associé              |
|--------------------|--------------------|-----------------------------|
| `cluster-nodes`    | IPs des 3 nœuds    | Machines physiques du cluster|
| `management-net`   | 192.168.10.0/24    | VxLAN 10 — Management       |
| `corosync-net`     | 192.168.20.0/24    | VxLAN 20 — Corosync         |
| `ceph-cluster-net` | 10.10.30.0/24      | VxLAN 30 — Réplication Ceph |
| `ceph-public-net`  | 10.10.40.0/24      | VxLAN 40 — Accès RBD        |
| `migration-net`    | 10.10.50.0/24      | VxLAN 50 — Live Migration   |
| `backup-net`       | 10.10.90.0/24      | VxLAN 90 — Sauvegardes      |

**Groupes de Sécurité** — les règles sont regroupées par fonction, créés une seule fois
au niveau Datacenter et réutilisés sur les trois nœuds :

| Groupe                  | Fonction                                        |
|-------------------------|-------------------------------------------------|
| `proxmox-management`    | SSH (22), Web UI (8006), consoles VNC/SPICE     |
| `corosync-cluster`      | Heartbeats UDP 5404-5405 entre les nœuds        |
| `vxlan-overlay`         | UDP 4789 — tous les tunnels VxLAN (CRITIQUE)    |
| `ceph-storage`          | MON TCP 3300/6789, OSD TCP 6800-7300            |
| `live-migration`        | QEMU TCP 60000-60050 sur VxLAN 50               |
| `pbs-backup`            | PBS TCP 8007 sur VxLAN 90                       |

Le groupe `vxlan-overlay` est la règle la plus critique de toute l'infrastructure : sans
l'autorisation du port UDP 4789, aucun tunnel VxLAN ne fonctionne, ce qui isole intégralement les nœuds les uns des autres et rend le cluster inopérant.

#### Règles au niveau Datacenter (appliquées aux 3 nœuds)

| Direction | Action         | Groupe de sécurité      | Justification                                       |
|-----------|----------------|-------------------------|-----------------------------------------------------|
| in        | Security Group | `proxmox-management`   | Administration SSH et Web UI depuis VXLAN 10        |
| in        | Security Group | `corosync-cluster`     | Maintien du quorum, priorité absolue                |
| in        | Security Group | `vxlan-overlay`        | Tous les tunnels VxLAN — règle critique             |
| in        | Security Group | `ceph-storage`         | Réplication OSD et accès RBD des VMs                |
| in        | Security Group | `live-migration`       | Migrations à chaud déclenchées par CLBS             |
| in        | Security Group | `pbs-backup`           | Transferts de sauvegarde sur VxLAN 90               |

#### Règles au niveau de chaque nœud

En complément des règles Datacenter héritées, chaque nœud définit des règles inter-nœuds
explicites afin de garantir les communications API et SSH entre pairs du cluster :

| Direction | Action | Protocole | Source   | Port | Commentaire                  |
|-----------|--------|-----------|----------|------|------------------------------|
| in        | ACCEPT | tcp       | `@pveX`  | 8006 | API Proxmox inter-nœuds      |
| in        | ACCEPT | tcp       | `@pveX`  | 22   | SSH inter-nœuds              |
| in        | ACCEPT | udp       | `@pveX`  | 4789 | VxLAN (redondance locale)    |

où `@pveX` représente chacun des deux autres nœuds du cluster.

#### Règles au niveau des VMs applicatives

Le principe du moindre privilège est appliqué à chaque VM : la politique d'entrée est
configurée en `DROP` et seuls les ports strictement nécessaires à la fonction de la VM sont
autorisés.

**VM HAProxy (load balancer — réseau DMZ 172.16.80.0/24)**

| Direction | Action | Protocole | Source            | Port      | Justification                        |
|-----------|--------|-----------|-------------------|-----------|--------------------------------------|
| in        | ACCEPT | tcp       | 0.0.0.0/0         | 80, 443   | Trafic HTTP/HTTPS entrant depuis DMZ |
| in        | ACCEPT | tcp       | `+management-net` | 9000      | Statistiques HAProxy (admin)         |
| in        | ACCEPT | tcp       | `+management-net` | 22        | SSH de maintenance                   |
| out       | ACCEPT | tcp       | 172.16.60.0/24    | 80        | Distribution vers les instances Flask|

**VMs Flask app-1 et app-2 (réseau Production 172.16.60.0/24)**

| Direction | Action | Protocole | Source            | Port  | Justification                              |
|-----------|--------|-----------|-------------------|-------|--------------------------------------------|
| in        | ACCEPT | tcp       | 172.16.60.0/24    | 80    | HTTP uniquement depuis HAProxy             |
| in        | ACCEPT | tcp       | `+management-net` | 22    | SSH de maintenance                         |
| out       | ACCEPT | tcp       | 172.16.60.0/24    | 5432  | Connexion PostgreSQL                       |

**VM PostgreSQL (base de données — réseau Production)**

| Direction | Action | Protocole | Source          | Port  | Justification                                      |
|-----------|--------|-----------|-----------------|-------|----------------------------------------------------|
| in        | ACCEPT | tcp       | 172.16.60.11    | 5432  | Connexion depuis l'IP exacte de Flask app-1        |
| in        | ACCEPT | tcp       | 172.16.60.12    | 5432  | Connexion depuis l'IP exacte de Flask app-2        |
| in        | ACCEPT | tcp       | `+management-net`| 22   | SSH de maintenance uniquement                      |

Les connexions vers PostgreSQL sont restreintes aux adresses IP exactes des deux instances
Flask (et non au sous-réseau entier /24), ce qui applique le principe du moindre privilège
de façon maximale sur la ressource la plus sensible de l'application.

**VM CLBS / Monitoring (InfluxDB, Prometheus, Grafana)**

| Direction | Action | Protocole | Source            | Port  | Justification                            |
|-----------|--------|-----------|-------------------|-------|------------------------------------------|
| in        | ACCEPT | tcp       | `+management-net` | 3000  | Interface Grafana                        |
| in        | ACCEPT | tcp       | `+management-net` | 8086  | API InfluxDB                             |
| in        | ACCEPT | tcp       | `+management-net` | 22    | SSH de maintenance                       |
| out       | ACCEPT | tcp       | `+cluster-nodes`  | 8006  | Appels API Proxmox par le service CLBS   |

#### Tableau récapitulatif des ports

| Port(s)      | Protocole | Réseau source              | Service              |
|--------------|-----------|----------------------------|----------------------|
| 22           | TCP       | 192.168.10.0/24            | SSH administration   |
| 8006         | TCP       | 192.168.10.0/24 + nœuds    | Web UI + API Proxmox |
| 5900–5999    | TCP       | 192.168.10.0/24            | Consoles VNC         |
| 3128         | TCP       | 192.168.10.0/24            | Console SPICE        |
| 5404–5405    | UDP       | 192.168.20.0/24 (VxLAN 20) | Corosync heartbeat   |
| 4789         | UDP       | Nœuds physiques            | VxLAN (CRITIQUE)     |
| 3300, 6789   | TCP       | VxLAN 30 + 40              | Ceph MON v2 et v1    |
| 6800–7300    | TCP       | VxLAN 30 + 40              | Ceph OSD             |
| 8443         | TCP       | 192.168.10.0/24            | Ceph MGR dashboard   |
| 60000–60050  | TCP       | 10.10.50.0/24 (VxLAN 50)   | QEMU live migration  |
| 8007         | TCP       | 10.10.90.0/24 (VxLAN 90)   | PBS Backup           |
| 80, 443      | TCP       | 0.0.0.0/0                  | HTTP/HTTPS (HAProxy) |
| 5432         | TCP       | 172.16.60.11-12             | PostgreSQL           |
| 3000         | TCP       | 192.168.10.0/24            | Grafana              |
| 8086         | TCP       | 192.168.10.0/24            | InfluxDB API         |

---

## BLOC B — Section IV.4.5 à insérer dans le Chapitre IV (Implémentation)

### IV.4.5 Configuration du Pare-feu Proxmox

La configuration du pare-feu a été réalisée depuis l'interface web Proxmox, en trois
phases successives : création des abstractions (Alias et IPSets), définition des groupes de
sécurité, puis application des règles au niveau Datacenter, nœud et VM.

#### Création des Alias et IPSets

Les alias ont été créés via `Datacenter → Firewall → Alias` pour nommer symboliquement
chacun des trois serveurs physiques et la passerelle. Les IPSets ont ensuite été définis dans
`Datacenter → Firewall → IPSet` pour représenter chacun des neuf réseaux logiques VxLAN
de l'infrastructure.

#### Création des Groupes de Sécurité

Six groupes de sécurité ont été créés dans `Datacenter → Firewall → Security Groups`.
Chaque groupe regroupe les règles d'un service fonctionnel précis. La méthode est la
suivante : créer le groupe, l'ouvrir, puis y ajouter les règles individuelles via le bouton `Add`.

Le groupe `vxlan-overlay` mérite une attention particulière. Il contient une unique règle
autorisant le port UDP 4789 depuis les nœuds du cluster. Ce port est celui utilisé par le
protocole VxLAN (RFC 7348) pour l'encapsulation de tous les tunnels overlay. Le bloquer
aurait pour effet d'interrompre simultanément Corosync, Ceph, les migrations à chaud et
l'ensemble des réseaux applicatifs.

Le groupe `ceph-storage` autorise les ports TCP des démons Ceph sur les deux réseaux
logiques dédiés (VxLAN 30 et VxLAN 40) : les ports 3300 et 6789 pour les Monitors (protocoles v2 et v1), et la plage 6800-7300 pour les OSDs. Cette plage couvre l'ensemble des
ports dynamiquement alloués par chaque démon OSD lors de ses communications de réplication et d'accès aux données.

Le groupe `live-migration` ouvre la plage TCP 60000-60050 sur le réseau dédié VxLAN 50.
QEMU alloue dynamiquement un port dans cette plage pour chaque migration à chaud. La
plage de 50 ports couvre les besoins du service CLBS, qui ne déclenche qu'une migration
par cycle de 60 secondes.

#### Application au niveau Datacenter

Les six groupes ont été appliqués dans `Datacenter → Firewall → Rules` en tant que
règles de type `Security Group`. Cette étape propage automatiquement toutes les règles
aux trois nœuds physiques sans configuration redondante.

La politique d'entrée a d'abord été laissée en `ACCEPT` le temps de valider le bon
fonctionnement de l'ensemble des services, puis basculée en `DROP` après vérification complète.

#### Règles spécifiques par nœud

Sur chaque nœud, des règles complémentaires ont été définies pour les communications
inter-nœuds directes : accès à l'API Proxmox (port 8006) depuis les deux autres serveurs,
SSH inter-nœuds (port 22), et une règle de redondance VxLAN (UDP 4789) locale. Ces
règles s'ajoutent aux règles Datacenter héritées et couvrent les cas de communication
directe entre pairs du cluster.

#### Configuration du pare-feu au niveau VM

Le pare-feu a été activé sur chacune des VMs applicatives de Sona-Web avec une
politique d'entrée `DROP`. Les règles autorisées ont été définies au minimum nécessaire à
la fonction de chaque VM.

La configuration la plus restrictive concerne la VM PostgreSQL : les connexions entrantes
sur le port 5432 sont autorisées uniquement depuis les adresses IP exactes des deux
instances Flask (172.16.60.11 et 172.16.60.12), et non depuis le sous-réseau /24 entier.
Cette approche applique le principe du moindre privilège de façon maximale sur la
ressource la plus sensible de l'application.

La VM CLBS bénéficie d'une règle sortante explicite vers le port 8006 des trois nœuds
Proxmox, nécessaire au service Go pour interroger les métriques du cluster et déclencher
les migrations via l'API REST.

#### Procédure de validation

Après activation du mode `DROP`, les commandes suivantes ont été exécutées pour valider
que le pare-feu ne perturbait aucun service de l'infrastructure :

```bash
pvecm status           # Cluster en quorum, 3 nœuds visibles
ceph -s                # HEALTH_OK, tous les OSDs up+in
pve-firewall status    # Pare-feu actif et règles chargées
pve-firewall compile   # Affichage des règles iptables compilées
```

La connectivité réseau entre les VxLANs a également été vérifiée :

```bash
ping 192.168.20.213    # Corosync VXLAN 20 depuis pve1 vers pve2
ping 10.10.30.213      # Ceph cluster VXLAN 30 depuis pve1 vers pve2
ping 10.10.40.213      # Ceph public VXLAN 40 depuis pve1 vers pve2
```

L'ensemble des services — cluster Proxmox, stockage Ceph, migrations à chaud et application
Sona-Web — sont restés opérationnels après l'activation complète du pare-feu en mode DROP.
