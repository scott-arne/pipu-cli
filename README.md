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
| `--debug` | | Show detailed logging |

### Uninstall Options

| Option | Short | Description |
|--------|-------|-------------|
| `PACKAGES` | | Package names to uninstall (required) |
| `--group TEXT` | `-g` | Uninstall across a named group |
| `--output [human\|json]` | `-o` | Output format |
| `--timeout INTEGER` | | Timeout in seconds (default: 300) |
| `--yes` | `-y` | Skip confirmation |
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
