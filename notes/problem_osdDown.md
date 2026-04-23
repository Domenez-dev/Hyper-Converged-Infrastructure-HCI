Merci pour ces détails. La bonne nouvelle est que votre horloge est maintenant synchronisée (`Leap status: Normal`). Le problème persistant est l'échec de l'activation de l'OSD 1. La commande `lvmconf` n'est pas disponible sur votre système (c'est normal sur certaines versions de Proxmox), nous allons donc nous concentrer sur la résolution directe de l'OSD.

## 🔍 Diagnostic de l'OSD 1

Exécutez les commandes suivantes pour comprendre pourquoi l'activation échoue :

```bash
# 1. Vérifier l'état du service systemd
systemctl status ceph-osd@1

# 2. Consulter les logs récents de l'OSD
journalctl -u ceph-osd@1 -n 50 --no-pager

# 3. Vérifier l'existence et les permissions du répertoire de l'OSD
ls -ld /var/lib/ceph/osd/ceph-1
ls -l /var/lib/ceph/osd/ceph-1/

# 4. Vérifier que le périphérique bloc existe toujours
ls -l /dev/ceph-715b8c56-fad9-46d7-9ba1-e9d1076d43ed/osd-block-ec333e88-3a08-4b46-909f-7bbd3cfc86cc
```

Collez les résultats. Très probablement, le répertoire `/var/lib/ceph/osd/ceph-1` est manquant ou corrompu.

---

## 🛠️ Solution rapide : purger et recréer l'OSD 1

Puisque l'OSD 1 est déjà `down/out` (selon votre `ceph -s` précédent), le plus efficace est de le recréer proprement. Cela résoudra l'échec d'activation.

**Attention** : cela supprime les données de l'OSD 1, mais comme le cluster est dégradé et que les autres OSDs (0 et 2) contiennent probablement les réplicas, la reconstruction se fera automatiquement.

### Étapes sur `pve-01` :

```bash
# 1. Purger l'OSD 1 du cluster (s'il est encore connu)
ceph osd out 1
ceph osd purge 1 --yes-i-really-mean-it

# 2. Supprimer le volume LVM associé (d'après votre lsblk, c'est le LV avec l'ID ec333e88...)
lvremove -f /dev/ceph-715b8c56-fad9-46d7-9ba1-e9d1076d43ed/osd-block-ec333e88-3a08-4b46-909f-7bbd3cfc86cc

# 3. Supprimer le groupe de volumes (optionnel mais propre)
vgremove -f ceph-715b8c56-fad9-46d7-9ba1-e9d1076d43ed

# 4. Nettoyer le disque physique (par exemple /dev/sdb)
wipefs -a /dev/sdb

# 5. Recréer l'OSD avec pveceph (choisir le bon disque, ex: /dev/sdb)
pveceph osd create /dev/sdb
```

Si vous avez plusieurs disques, identifiez bien celui qui correspond à l'OSD 1 (regardez la taille, 279.4G dans votre `lsblk`).

### Vérification finale

```bash
ceph osd tree
ceph -s
```

L'OSD devrait réapparaître avec un nouvel ID (ex: 3) et être `up/in`. Les PGs dégradées commenceront à se reconstruire.

---

## ⚠️ Concernant la commande `lvmconf`

Sur Proxmox VE récent, l'auto-activation des volumes LVM Ceph est gérée différemment (via les unités systemd `ceph-volume@...`). Le problème que vous avez rencontré après la coupure de courant est typique : ces unités n'ont pas été correctement réactivées. La solution pérenne est de s'assurer que les services sont bien activés :

```bash
systemctl enable ceph-volume@lvm-1-ec333e88-3a08-4b46-909f-7bbd3cfc86cc
systemctl enable ceph-osd@1
```

Mais après une purge, ces unités seront supprimées. La recréation de l'OSD les recréera correctement.

---

**Exécutez les commandes de diagnostic demandées au début pour confirmer, puis passez à la purge/re-création si nécessaire. Je reste à votre disposition pour interpréter les logs.**
