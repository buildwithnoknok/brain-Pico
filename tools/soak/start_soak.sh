#!/bin/sh
# start_soak.sh - launch the overnight soak detached, so it survives the SSH
# session going away. Safe to run from a one-shot plink command.
#
#   ./start_soak.sh [hours]        default 12
#
# The sudo password is read from ~/.soak_sudo (chmod 600) - never from this
# repo, which is public.

HOURS=${1:-12}
DIR=$(dirname "$0")
cd "$DIR" || exit 1

if [ ! -f "$HOME/.soak_sudo" ] && [ -z "$SOAK_SUDO_PW" ]; then
    echo "FATAL: create ~/.soak_sudo (chmod 600) with the sudo password first"
    exit 2
fi

mkdir -p runs
rm -f runs/STOP

nohup python3 soak_run.py --hours "$HOURS" --dir "$DIR/runs" \
      > runs/console.log 2>&1 &

echo "soak started (pid $!), ${HOURS} h"
echo "  progress : tail -n 20 $DIR/runs/console.log"
echo "  stop     : touch $DIR/runs/STOP"
echo "  report   : python3 $DIR/soak_report.py"
