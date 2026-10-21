# Run on a server

Complete the [source installation](../README.md#quick-start) first. Build the frontend and keep the Python API listening on loopback. Use SSH forwarding to open the application from another computer.

## Foreground

~~~bash
cd ~/StormShift
set -a
source .env
set +a
.venv/bin/python main_v2.py --host "$STORMSHIFT_HOST" --port "$STORMSHIFT_PORT"
~~~

Use Ctrl+C to stop.

## Keep it running with tmux

Install tmux using your operating system's package manager, then run from the repository root:

~~~bash
tmux new-session -s stormshift-demo
set -a
source .env
set +a
.venv/bin/python main_v2.py --host "$STORMSHIFT_HOST" --port "$STORMSHIFT_PORT"
~~~

Detach with **Ctrl+B**, then **D**. Reattach with `tmux attach -t stormshift-demo` and use Ctrl+C to stop the server. To remove the entire demo session:

~~~bash
tmux kill-session -t stormshift-demo
~~~

## Optional user systemd service

The template assumes the clone is at `~/StormShift` and uses port 8080. If your installation is elsewhere, change its working directory, environment file, and executable paths before installing it.

~~~bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/stormshift-demo.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now stormshift-demo.service

systemctl --user status stormshift-demo.service
journalctl --user -u stormshift-demo.service -n 80 --no-pager
~~~

Stop or restart it with:

~~~bash
systemctl --user stop stormshift-demo.service
systemctl --user restart stormshift-demo.service
~~~

To prevent automatic startup:

~~~bash
systemctl --user disable --now stormshift-demo.service
~~~

A user service surviving logout may require linger to be enabled by the server administrator. The template restarts an unexpectedly exited process after three seconds; an explicit `systemctl stop` leaves it stopped.

Use one process-management method at a time. A tmux stop command does not stop a systemd-managed process.

## Open the demo from your computer

~~~bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8080:127.0.0.1:8080 user@your-server
~~~

Open [http://127.0.0.1:8080](http://127.0.0.1:8080). Add your usual jump-host option if required. If port 8080 is occupied on your computer, change the first local port to 18080 and open `http://127.0.0.1:18080`.

The API has no built-in multi-user authentication. Loopback binding and an SSH tunnel are the intended setup for a remote demonstration.

## Updating

Stop the running service before updating frontend and backend:

~~~bash
git pull --ff-only
uv pip sync --python .venv/bin/python requirements-lock.txt
npm --prefix frontend ci
npm --prefix frontend run build
~~~

Reload your environment, restart with the same process manager, and check:

~~~bash
curl --fail http://127.0.0.1:8080/api/v2/health
curl --fail --head http://127.0.0.1:8080/
~~~

Keep `data/` if you need saved sessions. It is a database directory, not a disposable build cache.

## Keep installation caches inside the project

Optionally set these variables before installing to keep a self-contained server workspace:

~~~bash
mkdir -p .runtime .cache tmp
export TMPDIR="$PWD/tmp"
export XDG_CACHE_HOME="$PWD/.cache"
export UV_CACHE_DIR="$PWD/.cache/uv"
export UV_PYTHON_INSTALL_DIR="$PWD/.runtime/python"
export UV_PYTHON_BIN_DIR="$PWD/.runtime/python-bin"
export PIP_CACHE_DIR="$PWD/.cache/pip"
export npm_config_cache="$PWD/.cache/npm"
export HF_HOME="$PWD/.cache/huggingface"
~~~
