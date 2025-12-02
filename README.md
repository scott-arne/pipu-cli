<p align="center">
  <img src=".assets/pipu.png" alt="pipu logo" width="300"/>
</p>

# pipu

**pipu** is a smart Python package updater that safely upgrades your installed packages while respecting dependency constraints. It's like `pip list --outdated` + automated upgrades, but safe and easy.

## Why pipu?

Keeping Python packages up-to-date is important, but upgrading packages manually is tedious and risky:

- 😓 Running `pip install --upgrade` for each package is time-consuming
- 💥 Upgrading one package might break others due to dependency constraints
- 🤷 Hard to know which packages can be safely upgraded together

**pipu makes it easy:**

- ✅ Automatically finds all packages that can be safely upgraded
- ✅ Shows you exactly what will be upgraded before doing anything
- ✅ Upgrades everything in one command, letting pip handle the details
- ✅ Beautiful terminal UI with progress indicators

## Installation

```bash
pip install pipu-cli
```

## Quick Start

Simply run `pipu` in your Python environment:

```bash
pipu
```

That's it! pipu will:

1. Check all your installed packages
2. Find available updates
3. Determine which ones are safe to upgrade
4. Show you a table of what will be upgraded
5. Ask for confirmation (press Y to proceed)
6. Upgrade everything safely

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

✓ Successfully upgraded 3 package(s)
  - requests: 2.28.0 -> 2.31.0
  - rich: 13.0.0 -> 13.7.0
  - click: 8.1.3 -> 8.1.7
```

## Usage Examples

### Skip confirmation prompt (for scripts/automation)

```bash
pipu --yes
```

### Include pre-release versions

```bash
pipu --pre
```

### Increase timeout for slow connections

```bash
pipu --timeout 30
```

### Debug mode (see what's happening behind the scenes)

```bash
pipu --debug
```

Debug mode shows timing information and explains why packages can or cannot be upgraded.

### Combine options

```bash
pipu --yes --timeout 30 --debug
```

## Command Options

| Option | Short | Description |
|--------|-------|-------------|
| `--timeout INTEGER` | | Network timeout in seconds (default: 10) |
| `--pre` | | Include pre-release versions |
| `--yes` | `-y` | Skip confirmation prompt |
| `--debug` | | Show detailed logging and timing info |
| `--help` | | Show help message |

## How Does It Work?

pipu analyzes your package dependencies to determine which packages can be upgraded without breaking anything:

- **Safe by default**: Only upgrades packages when all dependency constraints are satisfied
- **Batch upgrades**: Upgrades compatible packages together, letting pip's resolver handle the details
- **Smart resolution**: Handles complex dependency scenarios, including circular dependencies

If pipu says a package can't be upgraded, it's usually because upgrading it would break another package. This is a good thing - pipu is protecting your environment!

## Common Questions

**Q: Is it safe to use pipu?**
A: Yes! pipu only upgrades packages when it's safe to do so. It uses the same pip underneath, just smarter.

**Q: Why didn't pipu upgrade package X?**
A: Probably because upgrading it would break another package. Use `--debug` to see why.

**Q: Can I use pipu in scripts or CI/CD?**
A: Absolutely! Use `pipu --yes` to skip the confirmation prompt.

**Q: What if all my packages are blocked?**
A: This means upgrading them would cause conflicts. This is actually good - pipu is preventing a broken environment.

**Q: Does pipu modify my packages without asking?**
A: No! pipu always asks for confirmation before upgrading (unless you use `--yes`).

**Q: Can I upgrade just one package?**
A: Currently pipu upgrades all compatible packages. To upgrade a specific package, use `pip install --upgrade package-name`.

**Q: Does pipu work with private PyPI repositories?**
A: Yes! pipu respects your pip configuration (index-url, extra-index-url, etc.).

## Tips

- **Run pipu regularly** to keep your packages up-to-date
- **Use `--debug`** if you're curious why a package can't be upgraded
- **Commit your requirements.txt** before running pipu in case you need to rollback
- **Use virtual environments** to isolate different projects

## Troubleshooting

### "No packages can be upgraded (all blocked by constraints)"

This means all available updates would violate dependency constraints. Your environment is in a stable state, which is good! If you really need a specific update, you may need to:

1. Manually upgrade the package: `pip install --upgrade package-name`
2. Upgrade other packages that are blocking it
3. Check if there's a newer version of the constraining package

### Network timeout errors

If you see timeout errors, increase the timeout:

```bash
pipu --timeout 30
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
