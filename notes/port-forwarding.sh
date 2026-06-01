#!/bin/bash
sudo sysctl -w net.ipv4.ip_forward=1

sudo ip addr add 10.0.0.1/24 dev enp0s20f0u4c2
sudo ip link set enp0s20f0u4c2 up

sudo ip route add 172.16.60.0/24 via 192.168.10.65
sudo ip route add 172.16.70.0/24 via 192.168.10.65
sudo ip route add 172.16.80.0/24 via 192.168.10.65
