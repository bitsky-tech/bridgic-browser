#!/bin/bash

# Usage:
#     bash ./scripts/publish_packages.sh [repo_name]
#
# This script publishes the bridgic-browser package to the specified repository.
# 
# Arguments:
#     repo_name: Target repository (btsk, testpypi, pypi). Default: btsk

set -e

repo=${1:-"btsk"}
package_name="bridgic-browser"

echo "==============================================================================="
echo " Publishing $package_name to repository [$repo]"
echo "==============================================================================="
echo ""

# Set credentials - source the script to set environment variables directly
source "$(dirname "$0")/set_publish_credentials.sh"

echo ""
echo "Building and publishing package..."
echo ""

# Build and publish
if make publish repo="$repo"; then
    echo ""
    echo "==============================================================================="
    echo " ✅ SUCCESS: $package_name published to $repo"
    echo "==============================================================================="
    exit 0
else
    echo ""
    echo "==============================================================================="
    echo " ❌ FAILED: Failed to publish $package_name to $repo"
    echo "==============================================================================="
    exit 1
fi
