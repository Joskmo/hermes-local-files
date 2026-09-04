---
name: hermes-local-files-operations
description: Install and verify Hermes Local Files synchronization.
version: 0.1.0
author: Joskmo, Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [Hermes, Syncthing, SSH, macOS, Operations]
    related_skills: []
---

# Hermes Local Files operations skill

Install and diagnose the Local Files plugin without patching Hermes core. Use the
repository CLI as the control plane; do not reconstruct installer steps by hand.

## When to use

- Install the server half for a remote Hermes profile.
- Install the Desktop/companion half on a Mac.
- Diagnose a Local Files status other than `Синхронизировано`.
- Verify an upgrade or repair after Hermes, macOS, or Debian changes.

Do not use this skill for ordinary Hermes projects that already live on the
backend, or for copying a single attachment into chat.

## Prerequisites

- Work from the checked-out `hermes-local-files` repository or its installed
  plugin directory.
- Obtain the profile, SSH target, connection ID, host-key fingerprint, and
  `/srv` data root from the current user. Never reuse another deployment's values.
- The Mac has Homebrew, Python 3.9+, and one working administrative SSH login to
  the server.
- Do not request or print SSH private keys, Syncthing API keys, local capability
  tokens, OAuth tokens, or `.env` contents.
- Treat `doctor --json` as authoritative. A process existing is not enough.

## How to run

Start every operation with a machine-readable diagnosis:

```text
terminal(command="./bin/hermes-local-files doctor --scope auto --json", timeout=60)
```

The command exits zero only when every reported check has `ok: true`. Parse the
JSON and report each failed check by `id`, `detail`, and `remedy`.

## Quick reference

```text
terminal(command="./bin/hermes-local-files version", timeout=30)
terminal(command="./bin/hermes-local-files doctor --scope server --json", timeout=60)
terminal(command="./bin/hermes-local-files doctor --scope macos --json", timeout=60)
terminal(command="./bin/hermes-local-files status --scope auto", timeout=60)
terminal(command="./bin/hermes-local-files install-server --data-root /srv/hermes-local-files/<profile>", timeout=300)
terminal(command="./bin/hermes-local-files install-macos --ssh-target <user>@<server> --host-key-sha256 SHA256:<fingerprint> --connection-id <id> --profile <profile>", timeout=600, pty=true)
```

Use `pty=true` for the Mac installer because the one-time administrative SSH
bootstrap may need an interactive credential. Never place that credential in the
command line.

## Procedure

### 1. Identify the host role

Run `doctor --scope auto --json`. If automatic detection fails, use `server` on
Linux or `macos` on macOS.

Completion criterion: the output parses as JSON with `schema_version: 1` and a
known `scope`.

### 2. Install the server half

On the Debian server, using values supplied for this deployment, run:

```text
terminal(command="./bin/hermes-local-files install-server --data-root /srv/hermes-local-files/<profile>", timeout=300)
```

Then ensure the plugin is enabled in the intended Hermes profile using the
normal Hermes plugin command. Do not hand-edit YAML:

```text
terminal(command="HERMES_HOME=$HOME/.hermes/profiles/<profile> hermes plugins enable local-files --no-allow-tool-override", timeout=60)
```

Reconnect the profile-scoped Desktop SSH backend after enabling it. Do not
restart an unrelated messaging gateway.

Completion criterion: `doctor --scope server --json` exits zero and every check
is true.

### 3. Install the Mac half

Run from the same checkout on the Mac:

```text
terminal(command="./bin/hermes-local-files install-macos --ssh-target <user>@<server> --host-key-sha256 SHA256:<fingerprint> --connection-id <id> --profile <profile>", timeout=600, pty=true)
```

Obtain the fingerprint through an independent trusted channel. The installer
verifies it before the credentialed SSH bootstrap. A mismatch is a hard stop: do not bypass it with
`StrictHostKeyChecking=no` or `accept-new`.

Completion criterion: `doctor --scope macos --json` exits zero after one Hermes
Desktop restart.

### 4. Create the first mapping

Use the Local Files page in Hermes Desktop and choose a disposable test folder.
Wait until the UI reports `Синхронизировано`. Do not register the project if the
initial transfer reports `Нужно внимание`.

Completion criterion: the local mapping inventory contains one complete mapping
and Hermes lists a project whose canonical primary path equals the returned
server path.

### 5. Verify both directions

Create a unique harmless marker in the local test folder and verify the same
relative path appears under the mapped server project. Then create a different
marker on the server and verify it appears locally. Remove both markers and
verify the deletions converge.

Use `write_file` for marker creation and `read_file` or `search_files` for
read-back. Do not use `.env`, credentials, existing documents, or destructive
commands as test data.

Completion criterion: both creates and both deletes are observed on both peers;
status returns to healthy after each direction.

## Pitfalls

- `ctx.rest` is ambient to the active Desktop route. Keep the target profile
  selected during provisioning and verify the returned Hermes project path.
- `.env`, `.env.*`, `.git`, dependency trees, and tool caches are intentionally
  ignored. Their absence on the server is expected.
- Syncthing versioning archives remote changes received by a device. It is not a
  universal backup for every local edit.
- A `sync-conflict-*` file means both sides edited concurrently. Preserve both
  copies and report `Нужно внимание`; do not auto-merge binary files.
- Closing Hermes does not stop sync. LaunchAgent and systemd services have their
  own lifecycle.
- Never broaden the restricted tunnel key beyond its declared loopback
  `permitopen` destinations to make a test pass.

## Verification

Run both project gates before declaring an installer change complete:

```text
terminal(command="npm run check && npm test", timeout=180)
terminal(command="hermes plugins doctor . --ci", timeout=120)
```

For an installed system, preserve the JSON result from both host roles and the
four observed E2E transitions: local create, server create, local delete, server
delete. Report unit/contract success separately from live E2E success.
