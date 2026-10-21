# Third-party notices

StormShift's original code is licensed under MIT. Dependencies retain their upstream licenses. The source distribution does not include virtual environments, npm packages, pretrained model weights, or runtime databases.

## Vendored Phaser runtime

The legacy browser implementation includes **Phaser 3.90.0** at `web/vendor/phaser.min.js`, obtained from the upstream npm distribution:

https://cdn.jsdelivr.net/npm/phaser@3.90.0/dist/phaser.min.js

Phaser is licensed under MIT. Its complete upstream license is retained in [web/vendor/PHASER-LICENSE.md](web/vendor/PHASER-LICENSE.md).

## Core runtime dependencies

| Project | License | Upstream |
| --- | --- | --- |
| OR-Tools | Apache-2.0 | https://github.com/google/or-tools |
| SimPy | MIT | https://gitlab.com/team-simpy/simpy |
| NetworkX | BSD-3-Clause | https://github.com/networkx/networkx |
| NumPy | BSD-3-Clause; binary wheels include additional upstream notices | https://github.com/numpy/numpy |
| FastAPI | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | BSD-3-Clause | https://github.com/encode/uvicorn |
| Pydantic | MIT | https://github.com/pydantic/pydantic |
| HTTPX | BSD-3-Clause | https://github.com/encode/httpx |
| LangGraph | MIT | https://github.com/langchain-ai/langgraph |
| OpenAI Python client | Apache-2.0 | https://github.com/openai/openai-python |
| React and React DOM | MIT | https://github.com/facebook/react |
| Phaser | MIT | https://github.com/phaserjs/phaser |
| XState and @xstate/react | MIT | https://github.com/statelyai/xstate |
| @xyflow/react | MIT | https://github.com/xyflow/xyflow |
| Lucide | ISC | https://github.com/lucide-icons/lucide |
| Recharts | MIT | https://github.com/recharts/recharts |

Lock files identify release dependency versions. Each installed distribution carries its own license and notices, including those for transitive dependencies. Some NumPy binary-wheel libraries have separate license conditions; those binaries are not redistributed in this source repository.

## Development and optional dependencies

Vite and Vitest use MIT licenses; TypeScript uses Apache-2.0. Installed packages include additional transitive license notices. The frontend toolchain includes **caniuse-lite**, whose browser-support data is licensed under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); upstream: https://github.com/browserslist/caniuse-lite.

Optional faster-whisper support is separately installed under its upstream MIT license. Optional model downloads retain the licenses of their respective model publishers. Model and serving-software licenses are separate from the license of this demo.

## Visuals and scenario data

Airport geometry, aircraft silhouettes, weather effects, and synthetic scenario records are generated in application code. The source includes no third-party visual asset packs, airline logos, or recorded passenger data.

Screenshots and the walkthrough video show the application's synthetic exercise. Public data adapters may contact Aviation Weather Center or OpenSky at runtime when explicitly used; no live-service responses or account credentials are included here. Users of those adapters must follow the relevant service terms.
