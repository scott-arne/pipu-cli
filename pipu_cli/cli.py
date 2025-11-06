import sys
from typing import List
import rich_click as click
from .internals import list_outdated
from .package_constraints import (
    read_constraints,
    read_ignores,
    read_invalidation_triggers,
    _get_constraint_invalid_when,
    _set_constraint_invalid_when
)
from .common import console
from pip._internal.commands.install import InstallCommand


def _install_packages(package_names: List[str], packages_being_updated: List[str] | None = None) -> int:
    """
    Install packages using pip API with filtered constraints.

    :param package_names: List of package names to install
    :param packages_being_updated: List of package names being updated (to exclude from constraints)
    :returns: Exit code (0 for success, non-zero for failure)
    """
    import tempfile
    import os
    from packaging.utils import canonicalize_name

    # If packages_being_updated not provided, use package_names
    if packages_being_updated is None:
        packages_being_updated = package_names

    # Get all current constraints and filter out packages being updated
    from .package_constraints import read_constraints
    all_constraints = read_constraints()

    # Get canonical names of packages being updated
    packages_being_updated_canonical = {canonicalize_name(pkg) for pkg in packages_being_updated}

    # Filter out constraints for packages being updated to avoid conflicts
    filtered_constraints = {
        pkg: constraint
        for pkg, constraint in all_constraints.items()
        if pkg not in packages_being_updated_canonical
    }

    # Create a temporary constraints file if there are any constraints to apply
    constraint_file_path = None
    try:
        if filtered_constraints:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                constraint_file_path = f.name
                for pkg, constraint in filtered_constraints.items():
                    f.write(f"{pkg}{constraint}\n")

            # Set environment variable for pip to use the filtered constraints
            os.environ['PIP_CONSTRAINT'] = constraint_file_path
            console.print(f"[dim]Using filtered constraints (excluding {len(packages_being_updated_canonical)} package(s) being updated)[/dim]")

        install_cmd = InstallCommand("install", "Install packages")
        install_args = ["--upgrade"] + package_names
        return install_cmd.main(install_args)

    finally:
        # Clean up: remove the constraint file and unset environment variable
        if constraint_file_path:
            if 'PIP_CONSTRAINT' in os.environ:
                del os.environ['PIP_CONSTRAINT']
            if os.path.exists(constraint_file_path):
                try:
                    os.unlink(constraint_file_path)
                except Exception:
                    pass  # Best effort cleanup


def launch_tui() -> None:
    """
    Launch the main TUI interface.
    """
    try:
        # Check for invalid constraints and triggers from removed/renamed packages
        from .package_constraints import cleanup_invalid_constraints_and_triggers
        _, _, cleanup_summary = cleanup_invalid_constraints_and_triggers()
        if cleanup_summary:
            console.print(f"[yellow]🧹 {cleanup_summary}[/yellow]")
            console.print("[dim]Press any key to continue...[/dim]")
            input()  # Wait for user acknowledgment before launching TUI

        from .ui import main_tui_app
        main_tui_app()
    except Exception as e:
        console.print(f"[red]Error launching TUI: {e}[/red]")
        sys.exit(1)




@click.group(invoke_without_command=True)
@click.pass_context
def cli(ctx):
    """
    pipu - Python package updater with constraint management

    If no command is specified, launches the interactive TUI.
    """
    if ctx.invoked_subcommand is None:
        # No subcommand was invoked, launch the TUI
        launch_tui()


@cli.command(name='list')
@click.option('--pre', is_flag=True, help='Include pre-release versions (alpha, beta, rc, dev)')
@click.option('--debug', is_flag=True, help='Print debug information as packages are checked')
def list_packages(pre, debug):
    """
    List outdated packages

    Displays a formatted table of installed packages that have newer versions
    available on the configured package indexes. By default, pre-release versions
    are excluded unless --pre is specified.
    """
    try:
        # Check for invalid constraints and triggers from removed/renamed packages
        from .package_constraints import cleanup_invalid_constraints_and_triggers
        _, _, cleanup_summary = cleanup_invalid_constraints_and_triggers()
        if cleanup_summary:
            console.print(f"[yellow]🧹 {cleanup_summary}[/yellow]")

        # Read constraints, ignores, and invalidation triggers from configuration
        constraints = read_constraints()
        ignores = read_ignores()
        invalidation_triggers = read_invalidation_triggers()

        # Set up debug callbacks if debug mode is enabled
        progress_callback = None
        result_callback = None
        if debug:
            def debug_progress(package_name):
                console.print(f"[dim]DEBUG: Checking {package_name}...[/dim]")

            def debug_callback(package_result):
                pkg_name = package_result.get('name', 'unknown')
                current_ver = package_result.get('version', 'unknown')
                latest_ver = package_result.get('latest_version', 'unknown')
                if current_ver != latest_ver:
                    console.print(f"[dim]DEBUG: {pkg_name}: {current_ver} -> {latest_ver}[/dim]")
                else:
                    console.print(f"[dim]DEBUG: {pkg_name}: {current_ver} (up-to-date)[/dim]")

            progress_callback = debug_progress
            result_callback = debug_callback

        # Use the internals function to get outdated packages and print the table
        outdated_packages = list_outdated(
            console=console,
            print_table=True,
            constraints=constraints,
            ignores=ignores,
            pre=pre,
            progress_callback=progress_callback,
            result_callback=result_callback,
            invalidation_triggers=invalidation_triggers
        )

        # The function already prints the table, so we just return the data
        return outdated_packages

    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.option('--pre', is_flag=True, help='Include pre-release versions (alpha, beta, rc, dev)')
@click.option('-y', '--yes', is_flag=True, help='Skip confirmation prompt and install all updates')
def update(pre, yes):
    """
    Update outdated packages

    Lists all outdated packages and prompts for confirmation before installing
    updates. Respects version constraints from configuration files.

    For interactive package selection, run 'pipu' with no arguments to launch the TUI.
    """
    try:
        # Check for invalid constraints and triggers from removed/renamed packages
        from .package_constraints import cleanup_invalid_constraints_and_triggers
        _, _, cleanup_summary = cleanup_invalid_constraints_and_triggers()
        if cleanup_summary:
            console.print(f"[yellow]🧹 {cleanup_summary}[/yellow]")

        # Read constraints, ignores, and invalidation triggers from configuration
        constraints = read_constraints()
        ignores = read_ignores()
        invalidation_triggers = read_invalidation_triggers()

        # Get outdated packages
        outdated_packages = list_outdated(
            console=console, print_table=True, constraints=constraints, ignores=ignores, pre=pre, invalidation_triggers=invalidation_triggers
        )

        if not outdated_packages:
            console.print("[green]All packages are already up to date![/green]")
            return

        # Determine which packages to update
        packages_to_update = outdated_packages

        if not yes:
            # Standard confirmation mode
            console.print()
            response = click.confirm("Do you want to update these packages?", default=False)
            if not response:
                console.print("[yellow]Update cancelled.[/yellow]")
                return

        # Validate constraint compatibility before installation
        console.print()
        console.print("[bold blue]Validating constraint compatibility...[/bold blue]")

        from .package_constraints import validate_package_installation, get_constraint_violation_summary

        # Extract package names for validation
        package_names_to_install = [pkg['name'] for pkg in packages_to_update]

        # Check for constraint violations
        safe_packages, invalidated_constraints = validate_package_installation(package_names_to_install)

        if invalidated_constraints:
            # Show constraint violations
            console.print("[bold red]⚠ Constraint Violations Detected![/bold red]")
            console.print(get_constraint_violation_summary(invalidated_constraints))

            # Filter out packages that would violate constraints
            violating_package_names = set()
            for violators in invalidated_constraints.values():
                violating_package_names.update(pkg.lower() for pkg in violators)

            # Keep only safe packages
            original_count = len(packages_to_update)
            packages_to_update = [
                pkg for pkg in packages_to_update
                if pkg['name'].lower() not in violating_package_names
            ]

            blocked_count = original_count - len(packages_to_update)

            if packages_to_update:
                console.print(f"\n[yellow]Proceeding with {len(packages_to_update)} packages that don't violate constraints.")
                console.print(f"Blocked {blocked_count} packages due to constraint violations.[/yellow]")
            else:
                console.print(f"\n[red]All {blocked_count} packages would violate constraints. No packages will be installed.[/red]")
                return

        # Install updates
        console.print()
        console.print("[bold green]Installing updates...[/bold green]")

        # Create list of package names to install
        # Use --upgrade without version pinning to allow pip's dependency resolver
        # to handle interdependent packages (e.g., pydantic and pydantic-core)
        package_names = [package['name'] for package in packages_to_update]

        # Install packages using pip API with filtered constraints
        exit_code = _install_packages(package_names, packages_being_updated=package_names)

        if exit_code == 0:
            console.print("[bold green]✓ All packages updated successfully![/bold green]")

            # Clean up constraints whose invalidation triggers have been satisfied
            from .package_constraints import post_install_cleanup
            post_install_cleanup(console)

        else:
            console.print("[bold red]✗ Some packages failed to update.[/bold red]")
            sys.exit(exit_code)

    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('constraint_specs', nargs=-1, required=False)
@click.option('--env', help='Target environment section (defaults to current environment or global)')
@click.option('--list', 'list_constraints', is_flag=True, help='List existing constraints for specified environment (or all environments if no --env specified)')
@click.option('--remove', 'remove_constraints', is_flag=True, help='Remove constraints for specified packages')
@click.option('--remove-all', 'remove_all_constraints', is_flag=True, help='Remove all constraints from specified environment (or all environments if no --env specified)')
@click.option('--yes', '-y', 'skip_confirmation', is_flag=True, help='Skip confirmation prompt')
@click.option('--invalidates-when', 'invalidation_triggers', multiple=True, help='Specify trigger conditions that invalidate this constraint (format: "package>=version" or "package>version"). Only ">=" and ">" operators allowed.')
def constrain(constraint_specs, env, list_constraints, remove_constraints, remove_all_constraints, skip_confirmation, invalidation_triggers):
    """
    Add or update package constraints in pip configuration

    Sets version constraints for packages in the pip configuration file.
    Constraints prevent packages from being updated beyond specified versions.

    Note: Automatic constraints from installed packages are now discovered
    on every pipu execution and do not need to be manually added. The constraints
    you add here will override any automatic constraints.

    \b
    Examples:
        pipu constrain "requests==2.31.0"
        pipu constrain "numpy>=1.20.0" "pandas<2.0.0"
        pipu constrain "django~=4.1.0" --env production
        pipu constrain "flask<2" --invalidates-when "other_package>=1" --invalidates-when "another_package>1.5"
        pipu constrain --list
        pipu constrain --list --env production
        pipu constrain --remove requests numpy
        pipu constrain --remove django --env production
        pipu constrain --remove-all --env production
        pipu constrain --remove-all --yes

    \f
    :param constraint_specs: One or more constraint specifications or package names (for --remove)
    :param env: Target environment section name
    :param list_constraints: List existing constraints instead of adding new ones
    :param remove_constraints: Remove constraints for specified packages
    :param remove_all_constraints: Remove all constraints from environment(s)
    :param skip_confirmation: Skip confirmation prompt for --remove-all
    :raises SystemExit: Exits with code 1 if an error occurs
    """
    try:
        # Validate mutually exclusive options
        # Note: constraint_specs with --remove are package names, not constraint specs
        has_constraint_specs_for_adding = bool(constraint_specs) and not (remove_constraints or remove_all_constraints)
        active_options = [list_constraints, remove_constraints, remove_all_constraints, has_constraint_specs_for_adding]
        if sum(active_options) > 1:
            console.print("[red]Error: Cannot use --list, --remove, --remove-all, and constraint specs together. Use only one at a time.[/red]")
            sys.exit(1)

        # Validate --invalidates-when can only be used when adding constraints
        if invalidation_triggers and (list_constraints or remove_constraints or remove_all_constraints):
            console.print("[red]Error: --invalidates-when cannot be used with --list, --remove, or --remove-all.[/red]")
            sys.exit(1)

        if invalidation_triggers and not has_constraint_specs_for_adding:
            console.print("[red]Error: --invalidates-when can only be used when adding constraint specifications.[/red]")
            sys.exit(1)

        # Handle --list option
        if list_constraints:
            from .package_constraints import list_all_constraints

            console.print("[bold blue]Listing constraints from pip configuration...[/bold blue]")

            all_constraints = list_all_constraints(env)

            if not all_constraints:
                if env:
                    console.print(f"[yellow]No constraints found for environment '[bold]{env}[/bold]'.[/yellow]")
                else:
                    console.print("[yellow]No constraints found in any environment.[/yellow]")
                return

            # Display constraints
            for env_name, constraints in all_constraints.items():
                console.print(f"\n[bold cyan]Environment: {env_name}[/bold cyan]")
                if constraints:
                    for package, constraint_spec in sorted(constraints.items()):
                        console.print(f"  {package}{constraint_spec}")
                else:
                    console.print("  [dim]No constraints[/dim]")

            return

        # Handle --remove option
        if remove_constraints:
            if not constraint_specs:
                console.print("[red]Error: At least one package name must be specified for removal.[/red]")
                sys.exit(1)

            from .package_constraints import remove_constraints_from_config, parse_invalidation_triggers_storage, get_current_environment_name, parse_inline_constraints

            package_names = list(constraint_specs)
            console.print(f"[bold blue]Removing constraints for {len(package_names)} package(s)...[/bold blue]")

            try:
                config_path, removed_constraints, removed_triggers = remove_constraints_from_config(package_names, env)

                if not removed_constraints:
                    console.print("[yellow]No constraints were removed (packages not found in constraints).[/yellow]")
                    return

                # Triggers are already cleaned up by remove_constraints_from_config

                # Display summary
                console.print("\n[bold green]✓ Constraints removed successfully![/bold green]")
                console.print(f"[bold]File:[/bold] {config_path}")

                # Show removed constraints
                console.print("\n[bold]Constraints removed:[/bold]")
                for package, constraint_spec in removed_constraints.items():
                    console.print(f"  [red]Removed[/red]: {package}{constraint_spec}")

                # Show removed invalidation triggers if any
                if removed_triggers:
                    console.print("\n[bold]Invalidation triggers removed:[/bold]")
                    for package, triggers in removed_triggers.items():
                        for trigger in triggers:
                            console.print(f"  [red]Removed trigger[/red]: {trigger}")

                # Show which environment was updated
                if env:
                    console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
                else:
                    from .package_constraints import get_current_environment_name
                    current_env = get_current_environment_name()
                    if current_env and current_env != "global":
                        console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {current_env}")
                    else:
                        console.print("\n[bold cyan]Environment updated:[/bold cyan] global")

                return

            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
            except IOError as e:
                console.print(f"[red]Error writing configuration: {e}[/red]")
                sys.exit(1)

        # Handle --remove-all option
        if remove_all_constraints:
            from .package_constraints import remove_all_constraints_from_config, list_all_constraints, parse_invalidation_triggers_storage

            # Get confirmation if not using --yes and removing from all environments
            if not env and not skip_confirmation:
                # Show what will be removed
                try:
                    all_constraints = list_all_constraints()
                    if not all_constraints:
                        console.print("[yellow]No constraints found in any environment.[/yellow]")
                        return

                    console.print("[bold red]WARNING: This will remove ALL constraints from ALL environments![/bold red]")
                    console.print("\n[bold]Constraints that will be removed:[/bold]")

                    total_constraints = 0
                    for env_name, constraints in all_constraints.items():
                        console.print(f"\n[bold cyan]{env_name}:[/bold cyan]")
                        for package, constraint_spec in sorted(constraints.items()):
                            console.print(f"  {package}{constraint_spec}")
                            total_constraints += 1

                    console.print(f"\n[bold]Total: {total_constraints} constraint(s) in {len(all_constraints)} environment(s)[/bold]")

                    if not click.confirm("\nAre you sure you want to remove all constraints?"):
                        console.print("[yellow]Operation cancelled.[/yellow]")
                        return

                except Exception:
                    # If we can't list constraints, ask for generic confirmation
                    if not click.confirm("Are you sure you want to remove all constraints from all environments?"):
                        console.print("[yellow]Operation cancelled.[/yellow]")
                        return

            console.print(f"[bold blue]Removing all constraints from {'all environments' if not env else env}...[/bold blue]")

            try:
                config_path, removed_constraints, removed_triggers_by_env = remove_all_constraints_from_config(env)

                if not removed_constraints:
                    if env:
                        console.print(f"[yellow]No constraints found in environment '{env}'.[/yellow]")
                    else:
                        console.print("[yellow]No constraints found in any environment.[/yellow]")
                    return

                # Triggers are already cleaned up by remove_all_constraints_from_config

                # Display summary
                console.print("\n[bold green]✓ All constraints removed successfully![/bold green]")
                console.print(f"[bold]File:[/bold] {config_path}")

                # Show removed constraints
                console.print("\n[bold]Constraints removed:[/bold]")
                total_removed = 0
                for env_name, constraints in removed_constraints.items():
                    console.print(f"\n[bold cyan]{env_name}:[/bold cyan]")
                    for package, constraint_spec in sorted(constraints.items()):
                        console.print(f"  [red]Removed[/red]: {package}{constraint_spec}")
                        total_removed += 1

                console.print(f"\n[bold]Total removed: {total_removed} constraint(s) from {len(removed_constraints)} environment(s)[/bold]")

                # Show removed invalidation triggers if any
                if removed_triggers_by_env:
                    console.print("\n[bold]Invalidation triggers removed:[/bold]")
                    total_triggers_removed = 0
                    for env_name, env_triggers in removed_triggers_by_env.items():
                        console.print(f"\n[bold cyan]{env_name}:[/bold cyan]")
                        for package, triggers in env_triggers.items():
                            for trigger in triggers:
                                console.print(f"  [red]Removed trigger[/red]: {trigger}")
                                total_triggers_removed += 1
                    console.print(f"\n[bold]Total triggers removed: {total_triggers_removed}[/bold]")

                # Show which environment(s) were updated
                if env:
                    console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
                else:
                    environments_updated = list(removed_constraints.keys())
                    console.print(f"\n[bold cyan]Environments updated:[/bold cyan] {', '.join(sorted(environments_updated))}")

                return

            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
            except IOError as e:
                console.print(f"[red]Error writing configuration: {e}[/red]")
                sys.exit(1)

        constraint_list = list(constraint_specs)

        if not constraint_list:
            console.print("[red]Error: At least one constraint must be specified.[/red]")
            sys.exit(1)

        console.print("[bold blue]Adding constraints to pip configuration...[/bold blue]")

        # Use backend function for constraint addition
        from .package_constraints import (
            add_constraints_to_config,
            validate_invalidation_triggers,
            get_recommended_pip_config_path,
            get_current_environment_name,
            parse_invalidation_triggers_storage,
            merge_invalidation_triggers,
            format_invalidation_triggers,
            parse_inline_constraints,
            parse_requirement_line
        )
        import configparser

        # Get the recommended config file path early for error handling
        config_path = get_recommended_pip_config_path()

        try:
            # Validate invalidation triggers if provided
            validated_triggers = []
            if invalidation_triggers:
                validated_triggers = validate_invalidation_triggers(list(invalidation_triggers))

            # Add constraints using the backend function
            config_path, changes = add_constraints_to_config(constraint_list, env)

            # Handle invalidation triggers if provided
            if validated_triggers:
                # Load the config and add triggers for the constrained packages
                config = configparser.ConfigParser()
                config.read(config_path)

                # Determine target environment section
                if env is None:
                    env = get_current_environment_name()
                section_name = env if env else 'global'

                # Get existing invalidation triggers
                existing_triggers_storage = {}
                existing_value = _get_constraint_invalid_when(config, section_name)
                if existing_value:
                    existing_triggers_storage = parse_invalidation_triggers_storage(existing_value)

                # Get current constraints to find the packages that were added/updated
                current_constraints = {}
                if config.has_option(section_name, 'constraints'):
                    constraints_value = config.get(section_name, 'constraints')
                    if any(op in constraints_value for op in ['>=', '<=', '==', '!=', '~=', '>', '<']):
                        current_constraints = parse_inline_constraints(constraints_value)

                # Process triggers for each package that was specified (whether changed or not)
                updated_triggers_storage = existing_triggers_storage.copy()

                # Get all package names from the constraint list
                all_package_names = set()
                for spec in constraint_list:
                    parsed = parse_requirement_line(spec)
                    if parsed:
                        all_package_names.add(parsed['name'].lower())

                for package_name in all_package_names:
                    # Get existing triggers for this package
                    existing_package_triggers = existing_triggers_storage.get(package_name, [])

                    # Merge with new triggers
                    merged_triggers = merge_invalidation_triggers(existing_package_triggers, validated_triggers)

                    if merged_triggers:
                        updated_triggers_storage[package_name] = merged_triggers

                # Format and store the triggers
                if updated_triggers_storage:
                    trigger_entries = []
                    for package_name, triggers in updated_triggers_storage.items():
                        # Get the constraint for this package to format properly
                        if package_name in current_constraints:
                            package_constraint = current_constraints[package_name]
                            formatted_entry = format_invalidation_triggers(f"{package_name}{package_constraint}", triggers)
                            if formatted_entry:
                                trigger_entries.append(formatted_entry)

                    triggers_value = ','.join(trigger_entries) if trigger_entries else ''
                    _set_constraint_invalid_when(config, section_name, triggers_value)

                # Write the updated config file
                with open(config_path, 'w', encoding='utf-8') as f:
                    config.write(f)

        except Exception as e:
            if isinstance(e, ValueError):
                raise e
            else:
                raise IOError(f"Failed to write pip config file '{config_path}': {e}")

        if not changes and not validated_triggers:
            console.print("[yellow]No changes made - all constraints already exist with the same values.[/yellow]")
            return

        # Display summary
        console.print("\n[bold green]✓ Configuration updated successfully![/bold green]")
        console.print(f"[bold]File:[/bold] {config_path}")

        # Show changes
        if changes:
            console.print("\n[bold]Constraints modified:[/bold]")
            for package, (action, constraint) in changes.items():
                action_color = "green" if action == "added" else "yellow"
                console.print(f"  [{action_color}]{action.title()}[/{action_color}]: {package}{constraint}")

        # Show invalidation triggers if added
        if validated_triggers:
            console.print("\n[bold]Invalidation triggers added:[/bold]")
            for trigger in validated_triggers:
                console.print(f"  [cyan]Trigger[/cyan]: {trigger}")

        # Show which environment was updated
        if env:
            console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
        else:
            current_env = get_current_environment_name()
            if current_env and current_env != "global":
                console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {current_env}")
            else:
                console.print("\n[bold cyan]Environment updated:[/bold cyan] global")

    except ValueError as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)
    except IOError as e:
        console.print(f"[red]Error writing configuration: {e}[/red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)


@cli.command()
@click.argument('package_names', nargs=-1, required=False)
@click.option('--env', help='Target environment section (defaults to current environment or global)')
@click.option('--list', 'list_ignores', is_flag=True, help='List existing ignores for specified environment (or all environments if no --env specified)')
@click.option('--remove', 'remove_ignores', is_flag=True, help='Remove ignores for specified packages')
@click.option('--remove-all', 'remove_all_ignores', is_flag=True, help='Remove all ignores from specified environment (or all environments if no --env specified)')
@click.option('--yes', '-y', 'skip_confirmation', is_flag=True, help='Skip confirmation prompt')
def ignore(package_names, env, list_ignores, remove_ignores, remove_all_ignores, skip_confirmation):
    """
    Add or remove package ignores in pip configuration

    Manages packages that should be ignored during update operations.
    Ignored packages will be skipped when checking for outdated packages.

    \b
    Examples:
        pipu ignore requests numpy
        pipu ignore flask --env production
        pipu ignore --list
        pipu ignore --list --env production
        pipu ignore --remove requests numpy
        pipu ignore --remove flask --env production
        pipu ignore --remove-all --env production
        pipu ignore --remove-all --yes

    \f
    :param package_names: One or more package names to ignore or remove
    :param env: Target environment section name
    :param list_ignores: List existing ignores instead of adding new ones
    :param remove_ignores: Remove ignores for specified packages
    :param remove_all_ignores: Remove all ignores from environment(s)
    :param skip_confirmation: Skip confirmation prompt for --remove-all
    :raises SystemExit: Exits with code 1 if an error occurs
    """
    try:
        # Validate mutually exclusive options
        active_options = [list_ignores, remove_ignores, remove_all_ignores, bool(package_names and not (list_ignores or remove_ignores or remove_all_ignores))]
        if sum(active_options) > 1:
            console.print("[red]Error: Cannot use --list, --remove, --remove-all, and package names together. Use only one at a time.[/red]")
            sys.exit(1)

        # Handle --list option
        if list_ignores:
            from .package_constraints import list_all_ignores

            console.print("[bold blue]Listing ignores from pip configuration...[/bold blue]")

            all_ignores = list_all_ignores(env)

            if not all_ignores:
                if env:
                    console.print(f"[yellow]No ignores found for environment '[bold]{env}[/bold]'.[/yellow]")
                else:
                    console.print("[yellow]No ignores found in any environment.[/yellow]")
                return

            # Display ignores
            for env_name, ignores in all_ignores.items():
                console.print(f"\n[bold cyan]Environment: {env_name}[/bold cyan]")
                if ignores:
                    for package in sorted(ignores):
                        console.print(f"  {package}")
                else:
                    console.print("  [dim]No ignores[/dim]")

            return

        # Handle --remove option
        if remove_ignores:
            if not package_names:
                console.print("[red]Error: At least one package name must be specified for removal.[/red]")
                sys.exit(1)

            from .package_constraints import remove_ignores_from_config

            packages_list = list(package_names)
            console.print(f"[bold blue]Removing ignores for {len(packages_list)} package(s)...[/bold blue]")

            try:
                config_path, removed_packages = remove_ignores_from_config(packages_list, env)

                if not removed_packages:
                    console.print("[yellow]No ignores were removed (packages not found in ignores).[/yellow]")
                    return

                # Display summary
                console.print("\n[bold green]✓ Ignores removed successfully![/bold green]")
                console.print(f"[bold]File:[/bold] {config_path}")

                # Show removed ignores
                console.print("\n[bold]Ignores removed:[/bold]")
                for package in removed_packages:
                    console.print(f"  [red]Removed[/red]: {package}")

                # Show which environment was updated
                if env:
                    console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
                else:
                    from .package_constraints import get_current_environment_name
                    current_env = get_current_environment_name()
                    if current_env and current_env != "global":
                        console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {current_env}")
                    else:
                        console.print("\n[bold cyan]Environment updated:[/bold cyan] global")

                return

            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
            except IOError as e:
                console.print(f"[red]Error writing configuration: {e}[/red]")
                sys.exit(1)

        # Handle --remove-all option
        if remove_all_ignores:
            from .package_constraints import remove_all_ignores_from_config, list_all_ignores

            # Get confirmation if not using --yes and removing from all environments
            if not env and not skip_confirmation:
                # Show what will be removed
                try:
                    all_ignores = list_all_ignores()
                    if not all_ignores:
                        console.print("[yellow]No ignores found in any environment.[/yellow]")
                        return

                    console.print("[bold red]WARNING: This will remove ALL ignores from ALL environments![/bold red]")
                    console.print("\n[bold]Ignores that will be removed:[/bold]")

                    total_ignores = 0
                    for env_name, ignores in all_ignores.items():
                        console.print(f"\n[bold cyan]{env_name}:[/bold cyan]")
                        for package in sorted(ignores):
                            console.print(f"  {package}")
                            total_ignores += 1

                    console.print(f"\n[bold]Total: {total_ignores} ignore(s) in {len(all_ignores)} environment(s)[/bold]")

                    if not click.confirm("\nAre you sure you want to remove all ignores?"):
                        console.print("[yellow]Operation cancelled.[/yellow]")
                        return

                except Exception:
                    # If we can't list ignores, ask for generic confirmation
                    if not click.confirm("Are you sure you want to remove all ignores from all environments?"):
                        console.print("[yellow]Operation cancelled.[/yellow]")
                        return

            console.print(f"[bold blue]Removing all ignores from {'all environments' if not env else env}...[/bold blue]")

            try:
                config_path, removed_ignores = remove_all_ignores_from_config(env)

                if not removed_ignores:
                    if env:
                        console.print(f"[yellow]No ignores found in environment '{env}'.[/yellow]")
                    else:
                        console.print("[yellow]No ignores found in any environment.[/yellow]")
                    return

                # Display summary
                console.print("\n[bold green]✓ All ignores removed successfully![/bold green]")
                console.print(f"[bold]File:[/bold] {config_path}")

                # Show removed ignores
                console.print("\n[bold]Ignores removed:[/bold]")
                total_removed = 0
                for env_name, ignores in removed_ignores.items():
                    console.print(f"\n[bold cyan]{env_name}:[/bold cyan]")
                    for package in sorted(ignores):
                        console.print(f"  [red]Removed[/red]: {package}")
                        total_removed += 1

                console.print(f"\n[bold]Total removed: {total_removed} ignore(s) from {len(removed_ignores)} environment(s)[/bold]")

                # Show which environment(s) were updated
                if env:
                    console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
                else:
                    environments_updated = list(removed_ignores.keys())
                    console.print(f"\n[bold cyan]Environments updated:[/bold cyan] {', '.join(sorted(environments_updated))}")

                return

            except ValueError as e:
                console.print(f"[red]Error: {e}[/red]")
                sys.exit(1)
            except IOError as e:
                console.print(f"[red]Error writing configuration: {e}[/red]")
                sys.exit(1)

        # Handle adding ignores (default behavior)
        if not package_names:
            console.print("[red]Error: At least one package name must be specified.[/red]")
            sys.exit(1)

        from .package_constraints import add_ignores_to_config, get_current_environment_name

        packages_list = list(package_names)
        console.print("[bold blue]Adding ignores to pip configuration...[/bold blue]")

        # Get the recommended config file path early for error handling
        from .package_constraints import get_recommended_pip_config_path
        config_path = get_recommended_pip_config_path()

        try:
            config_path, changes = add_ignores_to_config(packages_list, env)

            if not any(action == 'added' for action in changes.values()):
                console.print("[yellow]No changes made - all packages are already ignored.[/yellow]")
                return

            # Display summary
            console.print("\n[bold green]✓ Configuration updated successfully![/bold green]")
            console.print(f"[bold]File:[/bold] {config_path}")

            # Show changes
            console.print("\n[bold]Ignores modified:[/bold]")
            for package, action in changes.items():
                if action == 'added':
                    console.print(f"  [green]Added[/green]: {package}")
                elif action == 'already_exists':
                    console.print(f"  [yellow]Already ignored[/yellow]: {package}")

            # Show which environment was updated
            if env:
                console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {env}")
            else:
                current_env = get_current_environment_name()
                if current_env and current_env != "global":
                    console.print(f"\n[bold cyan]Environment updated:[/bold cyan] {current_env}")
                else:
                    console.print("\n[bold cyan]Environment updated:[/bold cyan] global")

        except IOError as e:
            console.print(f"[red]Error writing configuration: {e}[/red]")
            sys.exit(1)

    except Exception as e:
        console.print(f"[red]Unexpected error: {e}[/red]")
        sys.exit(1)


# This allows the module to be run with: python -m pipu_cli.cli
if __name__ == '__main__':
    cli()
