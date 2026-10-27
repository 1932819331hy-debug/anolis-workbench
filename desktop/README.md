# Desktop Wrapper

This directory contains the native Tauri wrapper for `anolis-workbench`.

## Runtime Model

1. Tauri hosts a native WebView window.
2. On app startup, Rust launches the packaged Python sidecar.
3. The sidecar serves the workbench at `http://127.0.0.1:3010`.
4. Frontend traffic stays on localhost HTTP/SSE exactly like browser mode.

## Development

Prerequisites:

1. Rust toolchain
2. Node 20+
3. Frozen sidecar executable at
   `desktop/src-tauri/binaries/anolis-workbench-sidecar-<host-triple>[.exe]`:
   with the `dev` extra installed, build the frontend first
   (`cd frontend && npm ci && npm run build`), then from the repository root run
   `python scripts/freeze_server.py --name anolis-workbench --output-dir dist`
   and copy `dist/anolis-workbench[.exe]` to that path. A local Python
   environment alone is not enough; the wrapper launches only this executable.

Run:

```bash
cd desktop
npm install
npm run tauri:dev
```

## Build

The release workflow stages a frozen sidecar binary into:

- `desktop/src-tauri/binaries/anolis-workbench-sidecar-<target>[.exe]`

Then runs:

```bash
cd desktop
npx tauri build --target <rust-target-triple>
```

Installer targets:

1. Windows `.msi`
2. Linux `.AppImage` and `.deb`
3. macOS `.dmg` (Apple Silicon and Intel)

Versioning note:

- Desktop metadata versions in `desktop/package.json`,
  `desktop/src-tauri/Cargo.toml`, and `desktop/src-tauri/tauri.conf.json` are
  intentionally kept in lockstep with `pyproject.toml`.

Port note:

- `3010` is hardcoded in the desktop wrapper. If another Anolis Workbench
  instance already holds it, the window attaches to that instance. If an
  unrelated process holds it, startup times out after 20 seconds and the
  wrapper requests a native error dialog and exits.
