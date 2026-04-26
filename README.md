<p align="center">
  <img src=".assets/pipu.png" alt="pipu logo" width="300"/>
</p>

# pipu

A smart Python package updater that safely upgrades your installed packages while respecting dependency constraints.

```bash
pip install pipu-cli
```

Requires Python 3.10+.

## Quick Start

Upgrade all packages safely:

```bash
pipu
```

This checks all installed packages, finds available updates, determines which ones are safe to upgrade (respecting dependency constraints), and upgrades them after confirmation.

Preview what can be upgraded without installing anything:

```bash
pipu outdated
```

## Usage

### Upgrade packages

```bash
# Upgrade all packages
pipu upgrade

# Upgrade specific packages
pipu upgrade requests numpy pandas

# Upgrade to a specific version
pipu upgrade requests==2.31.0

# Use version constraints
pipu upgrade "requests>=2.30,<3.0"

# Exclude packages from upgrade
pipu upgrade -e numpy -e pandas

# See why packages can't be upgraded
pipu upgrade --show-blocked

# Include pre-release versions
pipu upgrade --pre

# Skip confirmation prompt
pipu upgrade --yes

# Speed up version checking
pipu upgrade --parallel 10

# Update requirements.txt after upgrade
pipu upgrade --update-requirements requirements.txt
# Comments, blank lines, -r / -e / --extra-index-url options, extras,
# and PEP 508 environment markers are preserved; only matching pins change.

# JSON output for scripting
pipu upgrade -o json
```

### Install packages

```bash
# Install packages (uses pip install -U by default)
pipu install requests flask numpy

# Install without updating existing packages
pipu install requests --no-update

# Install with version constraints
pipu install "requests>=2.30" "flask==2.3.0"

# Install from a local file
pipu install ./mypackage-1.0.tar.gz

# Include pre-release versions
pipu install requests --pre
```

### Uninstall packages

```bash
# Uninstall packages
pipu uninstall requests flask

# Skip confirmation prompt
pipu uninstall requests -y

# JSON output
pipu uninstall requests -o json
```

If a package is already not installed, pipu treats it as a success (the desired state is already achieved).

### Inspect a package's dependencies

```bash
# Show direct required-by and requires for a package
pipu deps requests

# Recurse three hops on each side
pipu deps requests --depth 3

# Unlimited depth (cycles terminate safely)
pipu deps requests --depth 0

# Exit non-zero if any problem is found (useful in CI)
pipu deps requests --check

# Inspect the same package across every env in a group
pipu deps requests -g prod

# Machine-readable output
pipu deps requests -o json
```

`pipu deps` renders a tree with two branches:

- **Required by** — packages in the current environment that require `PACKAGE`.
- **Requires** — packages `PACKAGE` depends on.

If any dependency is missing, installed at a version that violates a
constraint, or points at a broken editable path, the affected rows are
marked and summarized in a `Problems` panel below the tree.

### Check environment consistency

```bash
# Scan the current environment for consistency problems
pipu check

# Group findings by package instead of by problem kind
pipu check --by package

# Machine-readable output
pipu check -o json

# Check every environment in a saved group
pipu check -g prod
```

`pipu check` scans for:

- **Missing** — packages required by something installed but not installed.
- **Violates** — installed versions that violate a constraint.
- **Broken editable** — editable installs whose source path no longer exists.
- **Duplicate install** — two or more distributions sharing a canonical name.
- **Stale metadata** — leftover `.dist-info` / `.egg-info` directories that pip no longer counts.

Exit code is `0` if clean, `1` if any problems are found. Group mode exits
`1` if any env has problems.

#### Automatic remediation

```bash
# Auto-fix stale-metadata (delete) and violates (install satisfying version)
pipu check --fix

# Prompt before each action (y/n/a/q)
pipu check --fix --interactive

# Machine-readable output with per-fix status
pipu check --fix -o json

# Fix every env in a group, sequentially
pipu check --fix -g prod
```

`--fix` attempts remediation for two problem kinds; the others
(`missing`, `broken-editable`, `duplicate-install`) are reported as
unfixable and must be resolved manually. Before any `violates` fix runs,
pipu saves a rollback state so the batch can be reverted with
`pipu rollback`. Combining `--interactive` with `-o json` is an error —
JSON consumers can't answer prompts.

### Automatic consistency check after changes

`pipu upgrade`, `pipu install`, and `pipu uninstall` automatically run
`pipu check` on the affected environment after completing. The auto-check
only informs; it never changes the host command's exit code.

Disable per-invocation with `--no-check`:

```bash
pipu upgrade --no-check
pipu install requests --no-check
pipu uninstall numpy --no-check
```

Disable globally in your config (`~/.pipu/config.toml`) or via environment
variable:

```toml
[settings]
check_after_changes = false
```

```bash
export PIPU_CHECK_AFTER_CHANGES=false
```

### Rollback

pipu saves package state before each upgrade. If something goes wrong:

```bash
# Rollback to the previous state
pipu rollback

# Preview what would be rolled back
pipu rollback --dry-run

# List all saved states
pipu rollback --list

# Rollback to a specific state
pipu rollback --state state_20241205_143022.json
```

If any package fails to reinstall, pipu prints the pip exit code and stderr for that package and exits non-zero. Successful packages are still rolled back.

### Caching

pipu caches version information per-environment to speed up repeated runs. The cache is fresh for 1 hour by default.

```bash
# Refresh cache manually
pipu update

# Skip cache and fetch fresh data
pipu upgrade --no-cache

# Adjust cache TTL (in seconds)
pipu upgrade --cache-ttl 300

# Clear cache for current environment
pipu clean

# Clear all caches
pipu clean --all
```

### Environment Groups

Groups let you run pipu commands across multiple Python environments at once. When using groups, pipu shows an aggregate matrix view of all environments and runs operations in parallel.

```bash
# Add current environment to a group
pipu group add data-science

# Add a specific environment
pipu group add data-science --python /path/to/envs/ml/bin/python

# List all groups
pipu group list

# Upgrade all environments in a group
pipu upgrade -g data-science

# Check for outdated packages across a group
pipu outdated -g data-science

# Install across a group
pipu install requests -g data-science

# Uninstall across a group
pipu uninstall requests -g data-science

# Remove an environment from a group
pipu group remove data-science --python /path/to/envs/ml/bin/python

# Delete an entire group
pipu group delete data-science
```

Group operations show a matrix table with packages as rows and environments as columns, making it easy to see what will change where before confirming.

Groups are stored at `~/.config/pipu/groups.toml`. Group names must match `[A-Za-z0-9_.-]+` and contain at least one alphanumeric character. Interpreter paths are resolved through symlinks before being stored, so two entries pointing at the same Python via different paths collapse to one canonical entry.

## Configuration

Create a `.pipu.toml` in your project directory:

```toml
timeout = 30
exclude = ["numpy", "pandas"]
pre = false
parallel = 5
cache_ttl = 3600
cache_enabled = true
```

Or add a `[tool.pipu]` section to `pyproject.toml`:

```toml
[tool.pipu]
timeout = 30
exclude = ["numpy", "pandas"]
parallel = 5
```

A user-level config can be placed at `~/.config/pipu/config.toml`.

**Priority:** CLI options > Project config > User config

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `PIPU_TIMEOUT` | Network timeout in seconds | 10 |
| `PIPU_CACHE_TTL` | Cache freshness in seconds | 3600 |
| `PIPU_CACHE_ENABLED` | Enable/disable caching | true |
| `PIPU_CACHE_DIR` | Cache directory | ~/.pipu/cache |

## Command Reference

| Command | Description |
|---------|-------------|
| `pipu upgrade` | Upgrade packages (default command) |
| `pipu install` | Install packages (wraps pip install -U) |
| `pipu uninstall` | Uninstall packages |
| `pipu outdated` | Show packages with updates available |
| `pipu deps` | Inspect a package's required-by and requires |
| `pipu check` | Scan an environment for consistency problems |
| `pipu update` | Refresh the package version cache |
| `pipu clean` | Clear caches |
| `pipu rollback` | Restore packages to a previous state |
| `pipu group add` | Add an environment to a group |
| `pipu group list` | List all environment groups |
| `pipu group remove` | Remove an environment from a group |
| `pipu group delete` | Delete an entire group |

### Upgrade Options

| Option | Short | Description |
|--------|-------|-------------|
| `PACKAGES` | | Package names to upgrade (default: all) |
| `--exclude TEXT` | `-e` | Exclude a package (repeatable) |
| `--show-blocked` | `-b` | Show packages blocked by constraints |
| `--group TEXT` | `-g` | Run across a named group |
| `--output [human\|json]` | `-o` | Output format |
| `--parallel INTEGER` | `-p` | Parallel version checks (default: min(4, CPU count)) |
| `--update-requirements PATH` | | Update a requirements.txt after upgrade |
| `--no-cache` | | Skip cache |
| `--cache-ttl INTEGER` | | Cache freshness in seconds (default: 3600) |
| `--timeout INTEGER` | | Network timeout in seconds (default: 10) |
| `--pre` | | Include pre-release versions |
| `--yes` | `-y` | Skip confirmation |
| `--no-check` | | Skip the post-upgrade consistency check |
| `--debug` | | Show detailed logging |

### Install Options

| Option | Short | Description |
|--------|-------|-------------|
| `PACKAGES` | | Package specs or file paths to install (required) |
| `--no-update` | | Plain pip install without -U |
| `--group TEXT` | `-g` | Install across a named group |
| `--output [human\|json]` | `-o` | Output format |
| `--timeout INTEGER` | | Timeout in seconds (default: 300) |
| `--pre` | | Include pre-release versions |
| `--yes` | `-y` | Skip confirmation |
| `--no-check` | | Skip the post-install consistency check |
| `--debug` | | Show detailed logging |

### Uninstall Options

| Option | Short | Description |
|--------|-------|-------------|
| `PACKAGES` | | Package names to uninstall (required) |
| `--group TEXT` | `-g` | Uninstall across a named group |
| `--output [human\|json]` | `-o` | Output format |
| `--timeout INTEGER` | | Timeout in seconds (default: 300) |
| `--yes` | `-y` | Skip confirmation |
| `--no-check` | | Skip the post-uninstall consistency check |
| `--debug` | | Show detailed logging |

### Rollback Options

| Option | Short | Description |
|--------|-------|-------------|
| `--list` | `-l` | List all saved states |
| `--dry-run` | | Preview what would be rolled back |
| `--state FILE` | | Rollback to a specific state |
| `--yes` | `-y` | Skip confirmation |

## License

MIT License - See [LICENSE](LICENSE) for details.
