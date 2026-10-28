# anolis-workbench

The workbench is a desktop and browser app for an Anolis machine. You build
the machine's configuration in it, run that configuration on your own
computer, install it on the target host, and operate the running runtime. It
serves its interface at `http://127.0.0.1:3010`.

## Install

- **Desktop app** (Windows, macOS, x86_64 Linux): download the installer from
  [Releases](https://github.com/anolishq/anolis-workbench/releases/latest).
  It bundles its own Python and opens a window over the same local server.
- **Python package** (Python 3.10 or later):

  ```sh
  pip install anolis-workbench
  anolis-workbench
  ```

  Then open `http://127.0.0.1:3010`.
- **Raspberry Pi**: install with `pipx install anolis-workbench`. It opens no
  browser on the Pi; reach it from another computer over an SSH tunnel:

  ```sh
  ssh -L 3010:127.0.0.1:3010 <user>@<pi>
  ```

The workbench has no login. It listens on `127.0.0.1` unless `--host` or
`ANOLIS_WORKBENCH_HOST` sets another address, and anyone who can reach that
address can use it. Treat access to it as access to the account it runs as.

## Compose, Commission, Operate

- **Compose** creates or imports a project: a machine profile with the runtime
  configuration, providers, devices and behavior trees. Provider settings are
  edited in forms generated from each provider's published config schema.
- **Commission** runs the project's runtime on this computer and checks it,
  exports a handoff package that records what was commissioned, and deploys
  the project to a target host. Projects imported from a machine profile can
  be deployed but not run locally.
- **Operate** connects to a running runtime through its HTTP API: device
  state, function calls, mode and the software stop.

Deployment always goes through the runtime's `install.sh`; see
[Deployment](https://github.com/anolishq/anolis/blob/main/docs/deploy/README.md).

## Command line

| Command | Use |
| --- | --- |
| `anolis-workbench` | Start the workbench server |
| `anolis-provision` | Install on this machine (`install`), over SSH (`remote`), to several hosts (`fleet`), or build an offline bundle (`bundle`); also `rollback`, `update` and `check-update` |
| `anolis-package` | Build a handoff package from a project |
| `anolis-validate` | Validate a handoff package |

## Reference

- [Commissioning handoff runbook](./commissioning-handoff-runbook.md)
- [Handoff package v1](./contracts/handoff-package-v1.md)
- [Versioning](./versioning.md)
