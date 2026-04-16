"""Output formatting for pipu CLI."""

import json
from typing import List, Optional, Any, Dict
from pipu_cli.package_management import UpgradePackageInfo, UpgradedPackage, BlockedPackageInfo, InstalledResult


class OutputFormatter:
    """Base class for output formatting."""

    def format_upgradable(self, packages: List[UpgradePackageInfo]) -> str:
        """Format upgradable packages."""
        raise NotImplementedError

    def format_blocked(self, packages: List[BlockedPackageInfo]) -> str:
        """Format blocked packages."""
        raise NotImplementedError

    def format_results(self, results: List[UpgradedPackage]) -> str:
        """Format upgrade results."""
        raise NotImplementedError


class JsonOutputFormatter(OutputFormatter):
    """JSON output formatter."""

    def _package_to_dict(self, pkg: Any) -> Dict[str, Any]:
        """Convert a package dataclass to a JSON-serializable dict."""
        result = {}
        for field_name in pkg.__dataclass_fields__:
            value = getattr(pkg, field_name)
            if hasattr(value, '__str__'):
                result[field_name] = str(value)
            else:
                result[field_name] = value
        return result

    def format_upgradable(self, packages: List[UpgradePackageInfo]) -> str:
        """Format upgradable packages as JSON."""
        data = {
            "upgradable": [self._package_to_dict(pkg) for pkg in packages],
            "count": len(packages)
        }
        return json.dumps(data, indent=2)

    def format_blocked(self, packages: List[BlockedPackageInfo]) -> str:
        """Format blocked packages as JSON."""
        data = {
            "blocked": [self._package_to_dict(pkg) for pkg in packages],
            "count": len(packages)
        }
        return json.dumps(data, indent=2)

    def format_results(self, results: List[UpgradedPackage]) -> str:
        """Format upgrade results as JSON."""
        successful = [self._package_to_dict(pkg) for pkg in results if pkg.upgraded]
        failed = [self._package_to_dict(pkg) for pkg in results if not pkg.upgraded]

        data = {
            "successful": successful,
            "failed": failed,
            "total": len(results),
            "success_count": len(successful),
            "failure_count": len(failed)
        }
        return json.dumps(data, indent=2)

    def format_all(
        self,
        upgradable: List[UpgradePackageInfo],
        blocked: Optional[List[BlockedPackageInfo]] = None,
        results: Optional[List[UpgradedPackage]] = None
    ) -> str:
        """Format all data as a single JSON object with standardized schema."""
        data: Dict[str, Any] = {
            "upgradable": [self._package_to_dict(pkg) for pkg in upgradable],
            "blocked": [self._package_to_dict(pkg) for pkg in blocked] if blocked else [],
            "results": [],
            "summary": {
                "total": 0,
                "upgraded": 0,
                "failed": 0
            }
        }

        if results is not None:
            data["results"] = [self._package_to_dict(pkg) for pkg in results]
            successful = [pkg for pkg in results if pkg.upgraded]
            failed = [pkg for pkg in results if not pkg.upgraded]
            data["summary"] = {
                "total": len(results),
                "upgraded": len(successful),
                "failed": len(failed)
            }

        return json.dumps(data, indent=2)

    def format_install_results(self, results: List[InstalledResult]) -> str:
        """Format install results as a single JSON object.

        :param results: List of InstalledResult objects
        :returns: JSON string
        """
        result_dicts = [self._package_to_dict(pkg) for pkg in results]
        new_installs = [pkg for pkg in results if pkg.installed and pkg.previous_version is None]
        updated = [pkg for pkg in results if pkg.installed and pkg.previous_version is not None
                    and pkg.version > pkg.previous_version]
        failed = [pkg for pkg in results if not pkg.installed]

        data: Dict[str, Any] = {
            "results": result_dicts,
            "summary": {
                "total": len(results),
                "installed": len(new_installs),
                "updated": len(updated),
                "failed": len(failed),
            }
        }
        return json.dumps(data, indent=2)

    def format_group_install_results(self, env_results: List[Dict[str, Any]]) -> str:
        """Format group install results as a JSON array of per-environment results.

        :param env_results: List of dicts, each with 'environment' key plus install results
        :returns: JSON string
        """
        return json.dumps(env_results, indent=2)

    def format_group_results(self, env_results: List[Dict[str, Any]]) -> str:
        """Format group results as a JSON array of per-environment results.

        :param env_results: List of dicts, each with 'environment' key plus standard schema
        :returns: JSON string
        """
        return json.dumps(env_results, indent=2)
