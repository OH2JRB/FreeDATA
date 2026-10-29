#!/usr/bin/env bash

# First start: create the config from the template, listening on all
# interfaces so the GUI can be reached through the published port, and
# without trying to open a browser inside the container
if [ ! -f "$FREEDATA_CONFIG" ]; then
    template=$(python3 -c "import importlib.util, os; print(os.path.join(importlib.util.find_spec('freedata_server').submodule_search_locations[0], 'config.ini.example'))")
    mkdir -p "$(dirname "$FREEDATA_CONFIG")"
    sed -e 's/^modemaddress = .*/modemaddress = 0.0.0.0/' \
        -e 's/^auto_run_browser = .*/auto_run_browser = False/' \
        "$template" > "$FREEDATA_CONFIG"
    echo "Created $FREEDATA_CONFIG"
fi

echo "Starting pulseaudio"
pulseaudio --exit-idle-time=-1 --daemon || echo "pulseaudio did not start"

if [ -z "${RIGCTLD_ARGS+x}" ]; then
    echo "No RIGCTLD_ARGS set, not starting rigctld"
else
    echo "Starting rigctld with args ${RIGCTLD_ARGS}"
    rigctld ${RIGCTLD_ARGS} &
fi

echo "Starting FreeDATA server"
exec freedata
