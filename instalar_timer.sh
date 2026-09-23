#!/usr/bin/env bash
# Instala el timer systemd que corre pipeline_nocturno.sh todos los días a las 02:00 de Perú (07:00 UTC).
set -euo pipefail
cd "$(dirname "$0")"
DIR="$(pwd)"
sed "s#__DIR__#$DIR#g" systemd/bodega-nocturno.service | sudo tee /etc/systemd/system/bodega-nocturno.service >/dev/null
sudo cp systemd/bodega-nocturno.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bodega-nocturno.timer
systemctl list-timers --all | grep bodega-nocturno
