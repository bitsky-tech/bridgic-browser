#!/usr/bin/env python3
"""
Check version compatibility with target repository according to PEP 440.

This script validates that the package version is appropriate for the target
repository before publishing.

Version rules:
- Development versions (*.dev*): btsk only
- Pre-release versions (alpha, beta, rc): pypi or testpypi
- Release versions (x.y.z): pypi or testpypi
"""

import re
import sys
import argparse


# ANSI color codes for terminal output
class Colors:
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    CYAN = "\033[36m"
    RESET = "\033[0m"


def colored(text: str, color: str) -> str:
    """Apply color to text for terminal output."""
    color_code = getattr(Colors, color.upper(), Colors.RESET)
    return f"{color_code}{text}{Colors.RESET}"


def parse_version(version_str: str) -> str:
    """Parse version string and return version type."""
    # PEP 440 version specification
    # Development versions: 1.0.0.dev1, 1.0.0a1.dev1
    # Pre-release versions: 1.0.0a1, 1.0.0b1, 1.0.0rc1
    # Release versions: 1.0.0, 1.0.1
    # Post-release versions: 1.0.post1
    
    if re.search(r'\.dev\d+', version_str):
        return 'dev'
    elif re.search(r'\d+(a|alpha)\d+', version_str):
        return 'alpha'
    elif re.search(r'\d+(b|beta)\d+', version_str):
        return 'beta'
    elif re.search(r'\d+rc\d+', version_str):
        return 'rc'
    elif re.search(r'\.post\d+', version_str):
        return 'post'
    elif re.match(r'^\d+\.\d+(\.\d+)?$', version_str):
        return 'release'
    else:
        return 'unknown'


def check_version_repo_compatibility(version: str, repo: str) -> bool:
    """Check version compatibility with repository."""
    version_type = parse_version(version)
    
    # Development versions can only be published to btsk-repo
    if version_type == 'dev':
        return repo == 'btsk'
    
    # Pre-release versions (alpha, beta, rc, post) can be published to pypi or testpypi
    if version_type in ['alpha', 'beta', 'rc', 'post']:
        return repo in ['pypi', 'testpypi']
    
    # Release versions can be published to pypi or testpypi
    if version_type == 'release':
        return repo in ['pypi', 'testpypi']
    
    # Unknown version types are not allowed to be published
    return False


def main():
    parser = argparse.ArgumentParser(
        description='Check version compatibility with target repository'
    )
    parser.add_argument('--version', required=True, help='Version string')
    parser.add_argument('--repo', required=True, help='Target repository (btsk, pypi, testpypi)')
    parser.add_argument('--package', help='Package name (for error messages)')
    
    args = parser.parse_args()

    version_str = colored(f"{args.package}-{args.version}", "cyan")
    repo_str = colored(args.repo, "yellow")

    if check_version_repo_compatibility(args.version, args.repo):
        checkmark = colored("✓", "green")
        print(f"{checkmark} Version [{version_str}] is compatible with repository [{repo_str}].")
        sys.exit(0)
    else:
        crossmark = colored("✗", "red")
        print(f"{crossmark} Version [{version_str}] is not compatible with repository [{repo_str}].")

        version_type = parse_version(args.version)
        if version_type == 'dev':
            repo_name_str = colored("btsk", "yellow")
            print(f"  Development versions can only be published to [{repo_name_str}].")
        elif version_type in ['alpha', 'beta', 'rc', 'post']:
            repo_name_str = colored("pypi or testpypi", "yellow")
            print(f"  Pre-release versions can only be published to [{repo_name_str}].")
        elif version_type == 'release':
            repo_name_str = colored("pypi or testpypi", "yellow")
            print(f"  Release versions can only be published to [{repo_name_str}].")
        else:
            print("  Unknown version type.")
        
        sys.exit(1)


if __name__ == '__main__':
    main()
