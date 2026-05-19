#!/bin/bash
make build
systemctl stop proxmox-drs
cp proxmox-drs /opt/proxmox-drs/proxmox-drs
cp .env /opt/proxmox-drs/.env
systemctl start proxmox-drs
