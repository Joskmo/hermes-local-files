# Hermes Local Files — design contract

## Goal

A non-technical macOS user selects an existing local folder once. The folder remains the
normal source of truth she opens in Finder and other applications. Hermes runs on a remote
Linux server against a synchronized working copy. Changes flow both ways automatically.
No Hermes Desktop core files are modified.

## User experience

1. Open **Local Files** in Hermes Desktop.
2. Click **Add local project**.
3. Choose a folder using the native macOS folder picker and enter a display name.
4. The plugin provisions the server copy and a normal Hermes Project.
5. The project shows one of four states: **Syncing**, **Synced**, **Offline**, or
   **Needs attention**.

There are no Push/Pull buttons in the normal path. Finder remains the editing surface.
Advanced details, conflict copies, and recovery are behind a disclosure.

## Components

### Desktop plugin (`desktop/plugin.js`)

A local Hermes Desktop runtime plugin. It contributes a Local Files page and sidebar row.
It talks only to:

- the local companion on a token-authenticated loopback HTTP API;
- the active Hermes backend through `ctx.rest` and `host.request`.

It never receives the SSH private key or Syncthing API key.

### macOS companion (`companion/`)

A Python standard-library service supervised by launchd. It:

- presents the native folder picker through `osascript`;
- owns local project mapping metadata;
- configures the local Syncthing instance through its loopback REST API;
- owns a persistent SSH local forward from a random high local port to
  `127.0.0.1:22000` on the server;
- exposes a small bearer-token API on `127.0.0.1`;
- reports aggregate status without exposing arbitrary filesystem reads.

The API token and mapping file are mode 0600. The HTTP listener rejects non-loopback peers,
missing bearer tokens, traversal, symlink escape, and unknown project IDs.

### Server plugin (`server/`)

A Hermes agent/dashboard plugin enabled for the administrator-selected profile. It:

- provisions project roots below one configured server root;
- configures server Syncthing through its loopback REST API;
- returns the server Syncthing device ID and project status;
- creates/updates a normal Hermes project after the synchronized directory exists;
- never accepts an arbitrary destination path from the client.

Server root:

```text
/srv/hermes-local-files/<profile>/projects
```

### Sync engine

Syncthing performs continuous bidirectional synchronization. The Mac initiates its device
connection through an SSH tunnel; server port 22000 is never exposed publicly. Discovery,
relaying, NAT traversal, and global announcements are disabled for this pair.

## Provisioning protocol

1. Desktop asks the local companion to choose a folder.
2. Companion returns an opaque local mapping ID, basename, and local Syncthing device ID.
3. Desktop calls the authenticated server plugin with the mapping ID, display name, and
   local device ID.
4. Server creates a sanitized directory and configures a `sendreceive` folder with
   staggered versioning. It returns folder ID, server device ID, and fixed server path.
5. Desktop asks the local companion to configure the matching folder and server device at
   the loopback tunnel address.
6. Companion starts/verifies the SSH tunnel and triggers a Syncthing rescan.
7. Desktop calls `projects.create` with the server path after both sides report the same
   folder ID and healthy connection.
8. Repeating any step with the same mapping ID is idempotent.

## Consistency and conflict policy

- Both sides use Syncthing `sendreceive` mode and filesystem watching.
- Deletes propagate, but server staggered versioning preserves replaced/deleted files.
- Syncthing conflict files are never auto-deleted or auto-selected.
- The plugin collapses technical conflict filenames into a `Needs attention` state and
  offers **Reveal both versions on this Mac**.
- A mapping is `Synced` only when both completion values are 100%, neither side reports
  errors, and the SSH tunnel is healthy.
- Offline edits queue normally and reconcile when the Mac reconnects.
- Project removal defaults to disconnecting sync without deleting either copy.

## Default ignores

The installer writes `.stignore` entries for disposable/generated directories only:

```text
(?d).DS_Store
(?d).Spotlight-V100
(?d).Trashes
(?d)node_modules
(?d)__pycache__
(?d).pytest_cache
(?d).mypy_cache
(?d).ruff_cache
```

`.git` is excluded because syncing Git's transactional metadata file-by-file can corrupt or
confuse a repository; source files still synchronize normally and Git history should use a
remote. `.env` is not silently excluded because many real projects require it, but the create
dialog warns when likely secret files are present and requires one clear confirmation. The
plugin never reads or displays their contents.

## Compatibility

- macOS 13+ client.
- Debian server with systemd user services.
- Syncthing 1.29+ server and 2.x Mac client; only shared stable REST fields are used.
- Current Hermes Desktop plugin SDK. Feature detection is required for every optional SDK
  method; no `window.hermesDesktop` private APIs.

## Non-goals for v1

- Editing files inside the plugin.
- Arbitrary server path selection.
- Sharing one mapping with multiple Macs.
- Silent conflict resolution.
- Opening public Syncthing or helper ports.
- Patching or forking Hermes Desktop.

## Verification gates

1. Unit tests for slug/path validation, token auth, mapping state, idempotent provisioning,
   Syncthing config generation, status reduction, and conflict detection.
2. Integration tests against fake Syncthing REST servers and a temporary filesystem.
3. Browser test of the Desktop page against fake companion/server APIs.
4. Real server smoke test: service supervision, loopback-only listeners, project root on
   `/srv`, and no public 22000 listener.
5. End-to-end two-directory sync: Mac fixture → server fixture and server fixture → Mac
   fixture, including offline recovery and a conflict case.
