#!/bin/sh
set -eu

if ! command -v uv >/dev/null 2>&1; then
	echo "uv is required to build this project" >&2
	exit 1
fi

uv run pyinstaller --clean deploy.spec
