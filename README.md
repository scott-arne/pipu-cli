<p align="center">
  <img src=".assets/pipu.png" alt="pipu logo" width="300"/>
</p>

# pipu

**Author:** Scott Arne Johnson ([sajohn2@gmail.com](mailto:sajohn2@gmail.com))

A powerful and user-friendly Python package updater with advanced constraint handling, beautiful TUI, and intelligent version management. Keep your Python packages up-to-date while maintaining stability through smart constraint enforcement.

## ✨ Key Features

- 🎮 **Interactive TUI** - Beautiful terminal interface with keyboard navigation and real-time feedback
- 🎯 **Smart Constraints** - Automatic constraint discovery and enforcement to prevent breaking updates
- 🚫 **Flexible Ignoring** - Exclude packages from updates with environment-specific rules
- 📊 **Rich Output** - Color-coded tables showing constraints, versions, and update safety
- 🔍 **Pre-release Support** - Optional inclusion of alpha, beta, and RC versions
- ⚙️ **Configurable** - 10+ environment variables for advanced customization
- 🔒 **Safe Updates** - Thread-safe operations with guaranteed resource cleanup
- 🌐 **Cross-Platform** - Full support for Windows, macOS, and Linux

## 🚀 Quick Start

### Installation

```bash
# Install in editable mode for development
pip install --config-settings editable_mode=compat -e ".[dev]"
```

### Basic Usage

```bash
# Launch interactive TUI (recommended)
pipu

# List outdated packages
pipu list

# Update all packages with confirmation
pipu update

# Auto-update without prompting
pipu update --yes
```

That's it! For most users, just running `pipu` will give you everything you need.

## 📖 Detailed Usage

### Commands Overview

| Command | Description | Example |
|---------|-------------|---------|
| `pipu` | Launch interactive TUI | `pipu` |
| `pipu list` | List outdated packages | `pipu list --pre` |
| `pipu update` | Update packages (with confirmation) | `pipu update --yes` |
| `pipu constrain` | Manage version constraints | `pipu constrain "requests<3.0"` |
| `pipu ignore` | Manage ignored packages | `pipu ignore setuptools pip` |

### Interactive TUI Mode

The TUI is the easiest way to manage updates. Just run `pipu` with no arguments.

**Navigation:**
- **↑/↓** - Navigate packages
- **Space** - Toggle package selection
- **A** - Select all packages
- **N** - Deselect all packages
- **Enter** - Confirm and update selected packages
- **Esc/Q** - Cancel and exit
- **H** - Show help

**Visual Indicators:**
- ✓ Green checkmark = Selected for update
- 🟢 Green constraint = Safe to update
- 🔴 Red constraint = Update blocked by constraint
- ⚫ Gray dash (-) = No constraint

### Command-Line Mode

For automation and scripting, use command-line mode:

```bash
# List outdated packages
pipu list

# Include pre-releases (alpha, beta, rc)
pipu list --pre

# Update with confirmation prompt
pipu update

# Update without confirmation (for scripts)
pipu update --yes

# Update including pre-releases
pipu update --pre --yes
```

### Managing Constraints

Constraints prevent packages from updating beyond specific versions, ensuring stability.

**Add Constraints:**
```bash
# Single constraint
pipu constrain "requests>=2.25.0,<3.0.0"

# Multiple constraints
pipu constrain "numpy>=1.20.0" "pandas<2.0.0" "django~=4.1.0"

# Environment-specific (for conda/venv/poetry environments)
pipu constrain "pytest>=7.0.0" --env development
```

**List Constraints:**
```bash
# All constraints
pipu constrain --list

# Environment-specific
pipu constrain --list --env production
```

**Remove Constraints:**
```bash
# Remove specific packages
pipu constrain --remove requests numpy

# Remove from specific environment
pipu constrain --remove django --env production

# Remove ALL constraints (prompts for confirmation)
pipu constrain --remove-all

# Skip confirmation
pipu constrain --remove-all --yes
```

**Auto-Discovery:**

Pipu automatically discovers constraints from your installed packages' dependencies. For example, if `deprecated==1.2.10` depends on `wrapt<2`, pipu will automatically constrain `wrapt<2` and show "deprecated>1.2.10" as the trigger. These auto-constraints are temporary and removed when no longer needed.

### Managing Ignored Packages

Ignored packages are completely excluded from update checks.

**Add Ignores:**
```bash
# Ignore packages globally
pipu ignore setuptools pip wheel

# Environment-specific ignores
pipu ignore pytest black mypy --env development
```

**List Ignores:**
```bash
pipu ignore --list
pipu ignore --list --env production
```

**Remove Ignores:**
```bash
pipu ignore --remove setuptools wheel
pipu ignore --remove-all
pipu ignore --remove-all --yes  # Skip confirmation
```

## ⚙️ Configuration

### Environment Variables

Customize pipu's behavior with environment variables:

**Network Settings:**
```bash
export PIPU_TIMEOUT=30              # Network timeout (default: 10s)
export PIPU_RETRIES=3               # Retry attempts (default: 0)
export PIPU_MAX_NETWORK_ERRORS=3    # Max consecutive errors (default: 1)
export PIPU_RETRY_DELAY=1.0         # Delay between retries (default: 0.5s)
```

**Subprocess Settings:**
```bash
export PIPU_SUBPROCESS_TIMEOUT=60   # Subprocess timeout (default: 30s)
export PIPU_UNINSTALL_TIMEOUT=180   # Uninstall timeout (default: 120s)
export PIPU_FORCE_KILL_TIMEOUT=10   # Force kill timeout (default: 5s)
```

**Caching & Logging:**
```bash
export PIPU_CACHE_TTL=120           # Cache lifetime (default: 60s)
export PIPU_LOG_LEVEL=DEBUG         # Logging level (default: WARNING)
```

**Example: Slow Network Configuration**
```bash
# For slow or unreliable networks
export PIPU_TIMEOUT=60
export PIPU_RETRIES=5
export PIPU_RETRY_DELAY=2.0
pipu update --yes
```

### Pip Configuration Integration

Pipu seamlessly integrates with pip's configuration files:

**Linux/macOS:**
- `~/.config/pip/pip.conf` (user-specific)
- `~/.pip/pip.conf` (legacy)
- `/etc/pip.conf` (system-wide)

**Windows:**
- `%APPDATA%\pip\pip.ini` (user-specific)
- `C:\ProgramData\pip\pip.ini` (system-wide)

**Example Configuration:**
```ini
[global]
index-url = https://pypi.org/simple/
trusted-host = pypi.org

# Global constraints
constraints =
    requests>=2.25.0,<3.0.0
    numpy>=1.20.0
    django>=4.1.0,<5.0.0

# Global ignores
ignores = pip setuptools wheel

[development]
# Development-specific constraints
constraints =
    pytest>=7.0.0
    black>=22.0.0
    mypy>=1.0.0

ignores =
    requests
    numpy
```

## 📚 Advanced Topics

### Constraint Sources (Priority Order)

Pipu checks these sources in order:

1. **`PIP_CONSTRAINT` environment variable**
   ```bash
   export PIP_CONSTRAINT=/path/to/constraints.txt
   ```

2. **Pip config - Environment-specific section**
   ```ini
   [myenv]  # Detected from conda/venv/poetry
   constraints = /path/to/constraints.txt
   ```

3. **Pip config - Global section**
   ```ini
   [global]
   constraints = /path/to/constraints.txt
   ```

4. **Project root** (legacy fallback)
   ```
   your-project/
   ├── pyproject.toml  # or setup.py
   └── constraints.txt
   ```

5. **Auto-discovered constraints** (from installed packages)
   - Automatically detected from package dependencies
   - Temporary and self-cleaning
   - Displayed with "Invalid When" trigger information

### Constraint File Format

```txt
# Web frameworks
requests>=2.25.0,<3.0.0
django>=4.1.0,<5.0.0
flask~=2.0.0

# Data science
numpy>=1.20.0
pandas>=1.5.0,!=1.5.1
scipy>=1.9.0,!=1.9.1,<2.0.0

# Comments and empty lines are ignored
matplotlib==3.6.0  # Exact version
```

### Constraint Operators

| Operator | Description | Example |
|----------|-------------|---------|
| `==1.0.0` | Exact version | `requests==2.28.0` |
| `>=1.0.0` | Minimum version | `numpy>=1.20.0` |
| `<=2.0.0` | Maximum version | `django<=4.2.0` |
| `>1.0.0,<2.0.0` | Version range | `flask>2.0,<3.0` |
| `~=1.4.0` | Compatible version | `black~=22.0` |
| `!=1.5.1` | Exclude version | `pandas!=1.5.1` |

### Environment Detection

Pipu automatically detects your Python environment:

- **Conda/Mamba/Micromamba**: Uses `$CONDA_DEFAULT_ENV`
- **Poetry**: Runs `poetry env info --name`
- **Virtualenv/venv**: Uses basename of `$VIRTUAL_ENV`

This enables environment-specific constraints and ignores.

### Invalidation Triggers

Pipu supports automatic constraint removal when packages are updated. Useful for temporary constraints:

```bash
# Add constraint with trigger
pipu constrain "wrapt<2" --invalidation-triggers "deprecated>1.2.10"

# When deprecated is updated to >1.2.10, wrapt constraint is automatically removed
pipu update deprecated

# Manually check and clean invalid constraints
pipu constrain --list  # Shows triggers
```

## 🎨 Output Examples

### List Command Output

```
                    Outdated Packages
┏━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┓
┃ Package      ┃ Version ┃ Latest  ┃ Type  ┃ Constraint ┃ Invalid When     ┃
┡━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━┩
│ requests     │ 2.28.0  │ 2.31.0  │ wheel │ -          │ -                │
│ numpy        │ 1.19.5  │ 1.24.3  │ wheel │ >=1.20.0   │ -                │
│ wrapt        │ 1.14.0  │ 2.0.0   │ wheel │ <2         │ deprecated>1.2.10│
└──────────────┴─────────┴─────────┴───────┴────────────┴──────────────────┘
```

**Color Coding:**
- 🟢 **Green** = Constraint satisfied, safe to update
- 🔴 **Red** = Constraint violated, update blocked
- ⚫ **Gray dash** = No constraint

## 🔧 Development

### Running Tests

```bash
# All tests (422 tests)
pytest tests/ -v

# Specific test categories
pytest tests/test_constraints.py -v
pytest tests/test_cli.py -v
pytest tests/test_tui_usability.py -v

# With coverage
pytest tests/ --cov=pipu --cov-report=html
```

### Building

```bash
# Install build dependencies
pip install build

# Build distribution
python -m build

# Install locally
pip install dist/pipu-*.whl
```

### Code Quality

The codebase includes:
- ✅ Thread-safe operations
- ✅ Cross-platform compatibility
- ✅ Comprehensive error handling
- ✅ Type hints on key functions
- ✅ Extensive test coverage (422 tests)
- ✅ Resource cleanup guarantees

## 🤝 Common Workflows

### Workflow 1: First-Time Setup

```bash
# 1. Install pipu
pip install -e .

# 2. Check what's outdated
pipu list

# 3. Use interactive mode to select packages
pipu

# 4. Add constraints for critical packages
pipu constrain "django>=4.1,<5.0" "numpy>=1.20"

# 5. Update everything else
pipu update --yes
```

### Workflow 2: CI/CD Pipeline

```bash
# Increase timeouts for CI environments
export PIPU_TIMEOUT=60
export PIPU_RETRIES=3
export PIPU_CACHE_TTL=300

# Check for outdated packages
pipu list

# Update with auto-approval
pipu update --yes
```

### Workflow 3: Development Environment

```bash
# Create dev-specific constraints
pipu constrain "pytest>=7.0" --env development
pipu ignore requests numpy --env development

# Update only development packages
pipu update --yes
```

### Workflow 4: Production Environment

```bash
# Strict constraints for production
pipu constrain "requests==2.28.0" --env production
pipu constrain "django~=4.1.0" --env production

# List what would be updated (without updating)
pipu list

# Only update when ready
pipu update --yes
```

## 📋 Troubleshooting

### Common Issues

**Slow Package Checks:**
```bash
# Increase timeout and enable retries
export PIPU_TIMEOUT=30
export PIPU_RETRIES=3
pipu list
```

**Network Errors:**
```bash
# Enable debug logging
export PIPU_LOG_LEVEL=DEBUG
pipu list
```

**Terminal Display Issues:**
```bash
# The TUI handles terminal cleanup automatically
# If issues persist, pipu resets terminal state on exit
```

**Package Not Found:**
```bash
# Check if package is in your pip indexes
pip search package-name

# Check your pip configuration
pip config list
```

## 📄 License

MIT License - see LICENSE file for details.

---

**Made with ❤️ for Python developers who want safe, smart package updates.**
