# PublishControl

A native Python + PySide6 desktop application for preparing and publishing individual npm packages. Includes a dark dashboard, package inspection, release preparation, archive contents preview, live logs, publish confirmation, history, and settings.

## Run

Requires Python 3.10+, Node.js/npm on PATH, and a graphical desktop.

```powershell
cd C:\path\to\PublishControl
py -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.\run.bat
```

On macOS/Linux:

```bash
cd /home/PublishControl
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
./run.sh
```

Or launch with `.venv\\Scripts\\python main.py` on Windows or `.venv/bin/python main.py` on macOS/Linux. Ensure `npm --version` works in the same terminal. On Linux, Qt may require your distribution's desktop/XCB libraries (including `libxcb-cursor0`).

## Release a package

1. Choose the package folder from Dashboard or Package. PublishControl reads `package.json`, checks npm authentication and the registry version, and reports Git status and missing documentation.
2. Open Publish. Choose Patch, Minor, Major, or Custom; select registry, tag, and public/restricted access.
3. Enable the checkbox acknowledging local version/lockfile changes and script execution. Only prepare projects you trust.
4. Run preflight. The app checks authentication and version availability, updates the version with `npm version --no-git-tag-version --ignore-scripts`, runs `npm install`, then available `test`, `build`, and `prepublishOnly` scripts.
5. It runs `npm pack --dry-run`, then creates a real archive (including npm's normal pack lifecycle hooks). Review the file list and compressed size of that exact archive.
6. Click **Publish package…**, review the package/version/registry/tag/access and exact command, optionally enter a one-time password, then explicitly click **Publish**.
7. Review the success state and History. Public npm releases include an “Open npm package” button.

The app invokes commands as argument arrays without a shell. npm scripts themselves execute normally through npm. Preparation never invokes `npm publish`. Publishing uploads the reviewed archive with `--ignore-scripts`, preventing a second set of publish hooks from changing the upload. Its SHA-256 is checked immediately before publishing. Changing release options invalidates the preview and requires new preparation. Registry, access, or tag conflicts in `publishConfig` block preparation.

## Authentication and local state

Authentication is owned by npm. Sign in using `npm login --registry https://registry.npmjs.org/` in your terminal. Use **Settings → Authentication & storage** to enter a masked granular token. Enable **Remember securely for future releases** to store it in the OS credential store; otherwise it lasts only for the current session. Tokens are scoped to the HTTPS registry entered and automatically reused for its npm operations. **Remove token** deletes the app credential and restores npm’s existing authentication. No token is written to settings, history, command arguments, or project files. If the secure keyring is unavailable, unlock/configure your desktop keyring or use session-only mode. The app passes the token through a registry-scoped npm subprocess environment variable; trusted package scripts inherit that environment. It does not edit `.npmrc`. A supplied one-time password exists only in memory and the publish subprocess environment.

Settings and publish history are stored under `~/.publishcontrol/`; use `PUBLISHCONTROL_DATA_DIR` to override. The legacy `PUBLISHCONSOLE_DATA_DIR` variable is also accepted. Writes use atomic replacement. Prepared archives are stored outside your package folder and removed when invalidated or closed normally. Archives left by a crash can be removed from that directory when the app is closed.

History records an attempt before publishing. A `publishing` entry left after a crash, or `failed / verify registry`, means check the registry before retrying: a network failure can occur after the registry accepted an upload. Commands have timeouts; preparation can be cancelled. Publishing cannot be cancelled from the UI, and the window stays open until the operation completes.

## Boundaries

- Individual packages only; select a package inside a monorepo rather than a workspace root. `publishConfig.directory` is unsupported.
- Preparation changes `package.json`, lockfiles, dependencies, and anything package scripts generate. These changes remain on failure/cancellation so you can inspect and retry. No automatic Git commits, tags, resets, or rollback.
- Missing README/license/repository and dirty Git state are warnings. Authentication failures, invalid package metadata, `private: true`, existing versions, and command failures block release preparation.
- `prepublishOnly` runs before packing. npm pack runs `prepack`/`prepare`/`postpack` normally. `publish`/`postpublish` hooks are not run on upload; move essential validation into test/build/prepublishOnly.
- Browser-based npm authentication challenges may need to be completed through npm in a terminal. The GUI has an optional OTP field but no interactive terminal.
- Arbitrary script output appears in live logs. Common npm token patterns are redacted, but package scripts should never print secrets. Logs are session-only; history stores release metadata and command, not console output.
- Atomic JSON storage is intended for one running app instance.

## Tests

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

Tests cover validation, release ordering, archive tampering, failure paths, persistence, cancellation timeout, background GUI jobs, navigation, and preview invalidation. A real npm integration test bumps a temporary package and lockfile, runs its test script, and verifies dry-run/packed contents offline. No test publishes or uses your npm account.

## Layout

```text
main.py              Application entry point
app/core.py          Package validation, subprocess runner, npm workflow, storage
app/ui.py            Five-page Qt interface and worker threads
requirements.txt     PySide6 dependency
run.bat               Launch using the local virtual environment on Windows
run.sh                Launch using the local virtual environment on macOS/Linux
tests/               Service, npm integration, and offscreen GUI tests
```

Implementation references: [Qt thread signals](https://doc.qt.io/qtforpython-6/examples/example_widgets_thread_signals.html), [npm version](https://docs.npmjs.com/cli/v11/commands/npm-version/), [npm pack](https://docs.npmjs.com/cli/v11/commands/npm-pack/), and [npm publish](https://docs.npmjs.com/cli/v11/commands/npm-publish/).

## Staged publishing

On Publish, choose **Release mode → Stage for approval** before preflight. The app verifies CLI staging support and that the package already exists, then prepares the archive. Confirmation uploads that reviewed archive using `npm stage publish`. History records **staged**, and keeps npm’s staging output and review instructions. A stage is not a live release; the dashboard’s published version is not advanced.

Review using `npm stage list <package> --registry https://registry.npmjs.org/`, then `npm stage view <stage-id>`. A maintainer approves with `npm stage approve <stage-id>` in an interactive terminal with 2FA. Include `--registry` for custom registries. The GUI does not approve automatically or track subsequent external approvals. Pending stages reserve their version; an attempted duplicate upload will be rejected by npm.

**First release:** npm staging requires the package to exist already. A stage-only token cannot create a new package. Publish its first version with authorized direct publishing (interactive 2FA or an eligible direct-capable token), then use staging for future versions. A failed attempt does not itself require bumping the version.

Reference: https://docs.npmjs.com/cli/v11/commands/npm-stage/
#   p u b l i s h c o n t r o l  
 