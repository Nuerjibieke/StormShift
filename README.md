# StormShift

**An interactive airport disruption and recovery demo — from a storm warning to a new flight schedule, operator approval, and aircraft movement.**

[中文说明](README.zh-CN.md) · [Watch the English demo](https://github.com/Nuerjibieke/StormShift/releases/download/v2.0.0/stormshift-demo-en-2x.mp4) · [Configuration](docs/configuration.md) · [Server deployment](docs/deployment.md)

![Aircraft and passengers in the StormShift airport simulation](docs/media/airport-overview.jpg)

StormShift makes a scheduling decision visible. Inspect the flight information that went into it, compare candidate timetables, follow information passed between specialist agents, and watch the approved plan play out on an airport map.

The default exercise starts during a severe evening storm. Runway **07R** is closed, a stand is occupied, and **eight incoming flights** need new landing and stand assignments. A tug fails during execution, forcing the remaining flights to be rescheduled. A passenger journey follows **24 connecting passengers from SK218 to NH701**, through boarding and takeoff.

The included scenarios and passenger records are synthetic. This is a research and teaching demo, not a system for directing real aircraft.

## What you can explore

- **A complete timetable:** landing times, runways, taxiing, stand assignments, and the inputs used to calculate them.
- **Agent handoffs:** who supplied a flight time or resource update, who checked it, and what happened next. Messages are assembled from the run's recorded tool results.
- **Three candidate plans:** compare delay and connection forecasts before choosing a plan.
- **Approval and execution:** inspect a candidate before allowing it to run; follow aircraft, stand use, and passenger movement.
- **A disruption during execution:** pause at a tug failure, inspect the revised timetable, and preserve actions that already happened.
- **Saved sessions and replay:** inspect recorded events and return to earlier points in the exercise.
- **English and Chinese interfaces:** switch languages in the lobby or the running exercise.

### What do the agents and solver each do?

| Component | What it reads | What it produces |
| --- | --- | --- |
| Weather and runway specialists | Weather observations, reopening estimates, runway availability | Timing information and runway restrictions |
| Ground and passenger specialists | Stand availability, taxi times, transfer deadlines | Stand and passenger checks |
| Coordinator and safety checks | Proposed conditions, flight schedules, fuel deadlines, occupied resources | Evidence checks, candidate status, and approval requirements |
| **OR-Tools CP-SAT** | All flights in the current planning round and the implemented constraints | Landing times, runway choices, and stand assignments |
| SimPy and passenger studies | A candidate schedule and modeled variation | Forecast delay and missed-connection statistics |

**The default setup uses deterministic specialist agents.** It needs no GPU, downloaded language model, or API key. Optional local language-model services can assist with structured analysis and intent interpretation; executable flight assignments still come from CP-SAT.

![A runway-to-ground handoff for flight SK218](docs/media/agent-handoffs.jpg)

## Quick start

This source setup targets **Linux**, with **Python 3.10** and **Node.js 22.12 or newer**. Install Git, Node.js/npm, and [uv](https://docs.astral.sh/uv/getting-started/installation/) first. Run all commands from the repository root.

### 1. Clone and install

~~~bash
git clone https://github.com/Nuerjibieke/StormShift.git
cd StormShift

uv venv --python 3.10 .venv
uv pip sync --python .venv/bin/python requirements-lock.txt

npm --prefix frontend ci
npm --prefix frontend run build
~~~

The Python lock file records the release dependencies. The frontend uses its npm lock file. Python serves the production frontend build, so a separate Vite server is unnecessary for the demo.

### 2. Configure and run

~~~bash
cp .env.example .env
set -a
source .env
set +a

.venv/bin/python main_v2.py --host 127.0.0.1 --port 8080
~~~

Open **[http://127.0.0.1:8080](http://127.0.0.1:8080)**. Stop the foreground process with **Ctrl+C**.

The example configuration disables optional model and voice services. It uses local data and cache directories; no credentials are included. The Python entry point reads environment variables, so load the file as shown above when starting a new shell.

For a remote server, run this command on your own computer and keep it open:

~~~bash
ssh -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
  -L 127.0.0.1:8080:127.0.0.1:8080 user@your-server
~~~

Then open the same local URL. See the [deployment guide](docs/deployment.md) for background execution, systemd, logs, and stopping the service.

### 3. Check the service

~~~bash
curl --fail http://127.0.0.1:8080/api/v2/health
~~~

The API, SQLite, and optimizer should report `ready`. In the default setup, model components report `fallback`, voice reports `disabled`, and overall health can read `degraded`. These values describe optional components; the synthetic exercise remains available.

## Try the storm recovery story

1. Choose **EN**, enter the exercise, and optionally set playback to **2×**.
2. Enter an operational goal, such as **Prioritize connecting passengers**, then confirm the interpreted request.
3. Select **Analyze & Solve Safely** to generate candidate schedules.
4. Open **Agent work → Full record**. Inspect a handoff and scroll to the timetable for all eight arrivals.
5. Preview the candidates. For the recorded walkthrough, select **Adapt to Weather Changes**, then **Approve and execute** when available.
6. Follow the first arrivals. At the **T+9 tug failure**, inspect the affected flights and revised timetable; use **Resume** after the briefing. If the revised candidate requests approval, review and approve it before continuing.
7. Watch the stand handover, connecting passengers boarding **NH701**, pushback, and takeoff. The completed exercise offers results and replay.

Times such as **T+9** mean nine simulated minutes after the exercise starts. Playback speed changes how quickly the animation is shown.

The [recorded English walkthrough](https://github.com/Nuerjibieke/StormShift/releases/download/v2.0.0/stormshift-demo-en-2x.mp4) is **4:13, 1080p, with English captions and 2× aircraft playback**. The MP4 plays independently of the demo service.

![A complete timetable for all eight incoming flights](docs/media/schedule.jpg)

## How the application fits together

~~~mermaid
flowchart LR
    A[Scenario and operator goal] --> B[Specialist evidence checks]
    B --> C[CP-SAT schedules]
    C --> D[Simulation and candidate checks]
    D --> E[Operator approval]
    E --> F[Aircraft and passenger execution]
    F --> G[New event and remaining-flight repair]
    G --> B
~~~

| Location | Purpose |
| --- | --- |
| `main_v2.py` | Starts the single-port FastAPI application |
| `stormshift_v2/` | Scenario state, specialist tools, scheduling, simulation, approval, execution, and replay |
| `frontend/src/` | React/TypeScript interface, Phaser airport animation, and playback |
| `tests/` | Python tests |
| `ops/` and `deploy/` | Optional process-management helpers and a systemd template |
| `stormshift/`, `server.py`, and `web/` | Legacy compatibility implementation; use `main_v2.py` for this demo |

SQLite stores sessions and execution events under `data/`. Runtime databases, credentials, model files, build output, and caches are excluded from Git.

## Development and tests

~~~bash
# Keep test data separate from saved demo sessions.
STORMSHIFT_ENABLE_LLM=off STORMSHIFT_ENABLE_VOICE=off \
STORMSHIFT_DATA_DIR="$PWD/.runtime/test-data" \
  .venv/bin/python -m pytest -q

npm --prefix frontend test
npm --prefix frontend run build
npm --prefix frontend audit
~~~

For frontend development, run the Python API on port 8080 and `npm --prefix frontend run dev` in another terminal. Vite proxies API and WebSocket requests to the backend.

## Optional services and current limits

- **Local language models:** configure two OpenAI-compatible local endpoints as described in [configuration](docs/configuration.md). Model weights, GPU-serving dependencies, and service credentials are not distributed with this repository.
- **Speech input:** optional faster-whisper support can be installed separately. Text input remains available.
- **Public data adapters:** weather and OpenSky clients are optional. The default exercise runs on synthetic data without contacting these services; service terms apply if you enable them.
- **Implemented rule families:** runway, separation, stand, turnaround, fuel, and committed-action rules are expressed in Python. Supporting an additional airport rule requires changing and testing that code.
- **Forecasts:** expected delays and missed connections are simulation estimates. The 24 animated connecting passengers illustrate one journey; they are not the entire airport passenger population.
- **Access:** the API has no multi-user authentication. Keep it on loopback and use SSH forwarding for remote demonstrations.

## License

StormShift's original code is available under the [MIT License](LICENSE). Third-party dependencies retain their own licenses; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
