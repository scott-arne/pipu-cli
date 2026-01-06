"""Output formatting for pipu CLI."""

import json
from typing import List, Optional, Any, Dict
from dataclasses import asdict

from rich.console import Console

from pipu_cli.package_management import UpgradePackageInfo, UpgradedPackage, BlockedPackageInfo


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
        """Format all data as a single JSON object."""
        data = {
            "upgradable": [self._package_to_dict(pkg) for pkg in upgradable],
            "upgradable_count": len(upgradable)
        }

        if blocked is not None:
            data["blocked"] = [self._package_to_dict(pkg) for pkg in blocked]
            data["blocked_count"] = len(blocked)

        if results is not None:
            successful = [self._package_to_dict(pkg) for pkg in results if pkg.upgraded]
            failed = [self._package_to_dict(pkg) for pkg in results if not pkg.upgraded]
            data["results"] = {
                "successful": successful,
                "failed": failed,
                "total": len(results),
                "success_count": len(successful),
                "failure_count": len(failed)
            }

        return json.dumps(data, indent=2)
