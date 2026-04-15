<p align="center">
  <img src=".assets/pipu.png" alt="pipu logo" width="300"/>
</p>

# pipu

**pipu** is a smart Python package updater that safely upgrades your installed packages while respecting dependency constraints. It's like `pip list --outdated` + automated upgrades, but safe and easy.

## Why pipu?

Keeping Python packages up-to-date is important, but upgrading packages manually is tedious and risky:

- Running `pip install --upgrade` for each package is time-consuming
- Upgrading one package might break others due to dependency constraints
- Hard to know which packages can be safely upgraded together

**pipu solves these problems:**

- Automatically finds all packages that can be safely upgraded
- Shows you exactly what will be upgraded before doing anything
- Upgrades everything in one command, letting pip handle the details
- Provides rollback capability if something goes wrong
- Beautiful terminal UI with progress indicators

## Installation

```bash
pip install pipu-cli
```

## Quick Start

The fastest way to upgrade all your packages:

```bash
pipu
```

This runs `pipu upgrade` — it checks for updates and upgrades everything safely.

Both `pipu` and `pipu upgrade` will:

1. Check all your installed packages
2. Find available updates (using cache if fresh, or fetching from PyPI)
3. Determine which ones are safe to upgrade
4. Show you a table of what will be upgraded
5. Ask for confirmation (press Y to proceed)
6. Upgrade everything safely

To preview what can be upgraded without installing anything:

```bash
pipu outdated
```

## Example Session

```
$ pipu

Step 1/5: Inspecting installed packages...
  Found 182 installed packages

Step 2/5: Checking for updates...
  Found 12 packages with newer versions available

Step 3/5: Resolving dependency constraints...
  3 packages can be safely upgraded

Step 4/5: Packages ready for upgrade:

┏━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┓
┃ Package       ┃ Current ┃ Latest  ┃
┡━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━┩
│ requests      │ 2.28.0  │ 2.31.0  │
│ rich          │ 13.0.0  │ 13.7.0  │
│ click         │ 8.1.3   │ 8.1.7   │
└───────────────┴─────────┴─────────┘

Do you want to proceed with the upgrade? [Y/n]: y

Step 5/5: Upgrading packages...
Successfully installed requests-2.31.0 rich-13.7.0 click-8.1.7

Successfully upgraded 3 package(s)
  - requests: 2.28.0 -> 2.31.0
  - rich: 13.0.0 -> 13.7.0
  - click: 8.1.3 -> 8.1.7
```

## Usage Examples

### Upgrade specific packages only

```bash
# Upgrade a single package
pipu upgrade requests

# Upgrade multiple specific packages
pipu upgrade requests numpy pandas
```

### Upgrade to a specific version

```bash
# Pin to exact version
pipu upgrade requests==2.31.0

# Use version constraints
pipu upgrade "requests>=2.30,<3.0"
```

### Check for outdated packages

```bash
pipu outdated
```

Shows all packages that have updates available without installing anything.

### Exclude packages from upgrade

```bash
# Exclude single package
pipu upgrade --exclude numpy
pipu upgrade -e numpy

# Exclude multiple packages (repeatable flag)
pipu upgrade -e numpy -e pandas -e scipy
```

### See why packages can't be upgraded

```bash
pipu upgrade --show-blocked
pipu upgrade -b
```

This displays packages that have updates available but can't be upgraded due to dependency constraints, along with the specific constraints blocking them.

### Skip confirmation prompt (for scripts/automation)

```bash
pipu -y
```

### Machine-readable JSON output

```bash
pipu outdated --output json
pipu upgrade --output json
pipu upgrade -o json
```

Useful for CI/CD pipelines and scripting.

### Speed up version checking with parallel requests

```bash
pipu upgrade --parallel 10
```

Uses multiple concurrent requests to check for updates (default: min(4, CPU count)).

### Update requirements.txt after upgrade

```bash
pipu upgrade --update-requirements requirements.txt
```

Automatically updates your requirements file with the new versions after a successful upgrade.

### Include pre-release versions

```bash
pipu upgrade --pre
```

### Debug mode

```bash
pipu upgrade --debug
```

Shows timing information and explains why packages can or cannot be upgraded.

## Rollback

If an upgrade causes problems, you can rollback to the previous state:

```bash
# Preview what would be rolled back
pipu rollback --dry-run

# Perform the rollback
pipu rollback

# List all saved states
pipu rollback --list

# Rollback to a specific state
pipu rollback --state state_20241205_143022.json
```

pipu automatically saves your package state before each upgrade, so you can always revert if needed.

## Caching

pipu caches package version information to speed up repeated runs. The cache is per-environment, so it works correctly with venv, conda, mise, and other environment managers.

```bash
# Refresh the cache manually
pipu update

# Upgrade using cached data (default behavior)
pipu upgrade

# Force fresh version check, ignoring cache
pipu upgrade --no-cache

# Clear cache for current environment
pipu clean

# Clear all caches
pipu clean --all
```

By default, the cache is considered fresh for 1 hour. You can adjust this:

```bash
# Use a shorter TTL (in seconds)
pipu upgrade --cache-ttl 300
```

## Command Reference

### Commands

| Command | Description |
|---------|-------------|
| `pipu upgrade` | Upgrade packages (default command) |
| `pipu outdated` | Show packages with updates available |
| `pipu update` | Refresh the package version cache |
| `pipu clean` | Clear caches |
| `pipu rollback` | Restore packages to a previous state |

### Upgrade Options

| Option | Short | Description |
|--------|-------|-------------|
| `PACKAGES` | | Optional package names to upgrade (default: all) |
| `--exclude TEXT` | `-e` | Package to exclude (repeatable) |
| `--show-blocked` | `-b` | Show packages blocked by constraints |
| `--output [human\|json]` | `-o` | Output format (default: human) |
| `--parallel INTEGER` | | Number of parallel version check requests (default: min(4, CPU count)) |
| `--update-requirements PATH` | | Update a requirements.txt file after upgrade |
| `--no-cache` | | Skip cache and fetch fresh version data |
| `--cache-ttl INTEGER` | | Cache freshness threshold in seconds (default: 3600) |
| `--timeout INTEGER` | | Network timeout in seconds (default: 10) |
| `--pre` | | Include pre-release versions |
| `--yes` | `-y` | Skip confirmation prompt |
| `--debug` | | Show detailed logging and timing info |

### Rollback Options

| Option | Short | Description |
|--------|-------|-------------|
| `--list` | `-l` | List all saved rollback states |
| `--dry-run` | | Show what would be rolled back |
| `--state FILE` | | Rollback to a specific state file |
| `--yes` | `-y` | Skip confirmation prompt |

### Clean Options

| Option | Short | Description |
|--------|-------|-------------|
| `--all` | `-a` | Clear cache for all environments |

## Configuration File

pipu supports configuration files to set default options. Create a `.pipu.toml` file in your project directory:

```toml
timeout = 30
exclude = ["numpy", "pandas"]
pre = false
parallel = 5
cache_ttl = 3600
cache_enabled = true
```

Or add a `[tool.pipu]` section to your `pyproject.toml`:

```toml
[tool.pipu]
timeout = 30
exclude = ["numpy", "pandas"]
parallel = 5
```

You can also create a user-level config at `~/.config/pipu/config.toml`.

**Priority:** Command-line options > Project config > User config

### Environment Variables

| Variable | Description |
|----------|-------------|
| `PIPU_TIMEOUT` | Network timeout in seconds (default: 10) |
| `PIPU_CACHE_TTL` | Cache freshness threshold in seconds (default: 3600) |
| `PIPU_CACHE_ENABLED` | Enable/disable caching (default: true) |
| `PIPU_CACHE_DIR` | Cache directory (default: ~/.pipu/cache) |

## How Does It Work?

pipu analyzes your package dependencies to determine which packages can be upgraded without breaking anything:

- **Safe by default**: Only upgrades packages when all dependency constraints are satisfied
- **Batch upgrades**: Upgrades compatible packages together, letting pip's resolver handle the details
- **Smart resolution**: Uses a fixed-point iteration algorithm to handle complex dependency scenarios, including circular dependencies
- **Rollback support**: Saves package state before upgrades so you can revert if needed
- **Per-environment caching**: Caches version data separately for each Python environment (venv, conda, mise, etc.)

If pipu says a package can't be upgraded, it's usually because upgrading it would break another package. Use `--show-blocked` to see exactly which constraints are preventing the upgrade.

## Common Questions

**Q: Is it safe to use pipu?**
A: Yes! pipu only upgrades packages when it's safe to do so. It uses the same pip underneath, with added safety checks. Plus, you can always rollback if needed.

**Q: Why didn't pipu upgrade package X?**
A: Probably because upgrading it would break another package. Use `--show-blocked` to see the specific constraints, or `--debug` for more details.

**Q: Can I use pipu in scripts or CI/CD?**
A: Absolutely! Use `pipu --yes --output json` for automated workflows.

**Q: What if all my packages are blocked?**
A: This means upgrading them would cause conflicts. This is actually good - pipu is preventing a broken environment.

**Q: Does pipu work with private PyPI repositories?**
A: Yes! pipu respects your pip configuration (index-url, extra-index-url, trusted-host, etc.).

**Q: How do I speed up pipu for large environments?**
A: Use `--parallel 10` (or higher) to check multiple packages concurrently.

## Tips

- **Run `pipu` regularly** to keep your packages up-to-date
- **Use `pipu outdated`** first to preview what can be upgraded
- **Use `--show-blocked`** to understand why certain packages can't be upgraded
- **Use `--update-requirements`** to keep your requirements.txt in sync
- **Use `pipu update`** to refresh the cache before checking for updates
- **Use virtual environments** to isolate different projects

## Troubleshooting

### "No packages can be upgraded (all blocked by constraints)"

This means all available updates would violate dependency constraints. Your environment is in a stable state, which is good! If you really need a specific update:

1. Use `--show-blocked` to see what's blocking each package
2. Consider upgrading the blocking packages first
3. Or manually upgrade with `pip install --upgrade package-name` if you accept the risk

### Network timeout errors

If you see timeout errors, increase the timeout:

```bash
pipu --timeout 30
```

### Stale cache data

If pipu shows outdated version info, refresh the cache:

```bash
pipu update
# or
pipu upgrade --no-cache
```

### Rolling back after a bad upgrade

If something broke after an upgrade:

```bash
pipu rollback
```

## Requirements

- Python 3.10 or higher
- pip (comes with Python)

## License

MIT License - See LICENSE file for details

## Author

Scott Arne Johnson (scott.arne.johnson@gmail.com)

## Contributing

Found a bug or want to contribute? Check out the [GitHub repository](https://github.com/scott-arne/pipu-cli)!
