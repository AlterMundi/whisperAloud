#!/usr/bin/env bash
# Reproduce the local WhisperAloud Flatpak build and single-file bundle (WHISP-61).
#
#   scripts/build_flatpak.sh                 build + export to local repo + bundle
#   scripts/build_flatpak.sh --lint          also run flatpak-builder-lint
#   scripts/build_flatpak.sh --test          also run the unit tests inside the build sandbox
#   scripts/build_flatpak.sh --e2e           also run the e2e suite (private dbus-run-session bus) in it
#   scripts/build_flatpak.sh --no-build ...  skip build/bundle; only lint/test the existing build dir
#   scripts/build_flatpak.sh --regen-deps    regenerate flatpak/python3-requirements.json first
#
# Build artifacts go to $WHISPER_ALOUD_FLATPAK_BUILD_ROOT
# (default ~/REPOS/scripts/flatpak-build), never into the repo and never into
# /tmp (a RAM tmpfs on the dev machine).
#
# This script NEVER installs or runs the app: installing would export a
# .desktop and a D-Bus activation file that compete with a native install.
# See docs/flatpak.md ("Install and validate") for that step.
#
# Requirements (user scope is enough):
#   flatpak install --user flathub org.flatpak.Builder org.gnome.Platform//51 org.gnome.Sdk//51
set -euo pipefail

APP_ID=org.fede.whisperaloud
VERSION=0.3.0
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$REPO_ROOT/flatpak/$APP_ID.yml"
BUILD_ROOT="${WHISPER_ALOUD_FLATPAK_BUILD_ROOT:-$HOME/REPOS/scripts/flatpak-build}"
BUILD_DIR="$BUILD_ROOT/build-dir"
STATE_DIR="$BUILD_ROOT/state"
OSTREE_REPO="$BUILD_ROOT/repo"
BUNDLE="$BUILD_ROOT/whisper-aloud-$VERSION.flatpak"
SDK=org.gnome.Sdk//51

do_lint=0
do_test=0
do_regen=0
do_e2e=0
do_build=1
for arg in "$@"; do
    case "$arg" in
        --lint) do_lint=1 ;;
        --test) do_test=1 ;;
        --e2e) do_e2e=1 ;;
        --no-build) do_build=0 ;;
        --regen-deps) do_regen=1 ;;
        -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

case "$BUILD_ROOT" in
    /tmp|/tmp/*) echo "refusing to build under /tmp (RAM tmpfs): $BUILD_ROOT" >&2; exit 2 ;;
esac
mkdir -p "$BUILD_ROOT"

builder() { flatpak run org.flatpak.Builder "$@"; }

if [ "$do_regen" = 1 ]; then
    # flatpak-pip-generator from flatpak/flatpak-builder-tools, run in its own
    # venv under BUILD_ROOT. It resolves inside the SDK so wheel tags match the
    # runtime's Python.
    tools="$BUILD_ROOT/tools"
    gen="$tools/flatpak-builder-tools-master/pip/flatpak-pip-generator.py"
    if [ ! -f "$gen" ]; then
        mkdir -p "$tools"
        curl -fsSL https://github.com/flatpak/flatpak-builder-tools/archive/refs/heads/master.tar.gz \
            | tar xz -C "$tools"
    fi
    if [ ! -x "$tools/venv/bin/python" ]; then
        python3 -m venv "$tools/venv"
        "$tools/venv/bin/pip" install -q requirements-parser pyyaml packaging
    fi
    "$tools/venv/bin/python" "$gen" \
        --runtime "$SDK" \
        --requirements-file "$REPO_ROOT/flatpak/requirements-flatpak.txt" \
        --prefer-wheels=ctranslate2,av,numpy,scipy,onnxruntime,tokenizers,psutil,hf-xet,sherpa-onnx,sherpa-onnx-core,cffi,pyyaml,charset-normalizer,markupsafe,protobuf \
        --output "$REPO_ROOT/flatpak/python3-requirements"
fi

if [ "$do_build" = 1 ]; then
    # Build and export to the local OSTree repo. --install-deps-from only pulls the
    # runtime/SDK (user scope) if missing; it never installs the app.
    builder --user --install-deps-from=flathub \
        --force-clean \
        --state-dir="$STATE_DIR" \
        --repo="$OSTREE_REPO" \
        --default-branch=stable \
        "$BUILD_DIR" "$MANIFEST"

    # Single-file bundle. --runtime-repo lets `flatpak install` fetch the GNOME
    # runtime from Flathub if the target machine does not have it yet.
    flatpak build-bundle \
        --runtime-repo=https://dl.flathub.org/repo/flathub.flatpakrepo \
        "$OSTREE_REPO" "$BUNDLE" "$APP_ID" stable
    echo "bundle: $BUNDLE ($(du -h "$BUNDLE" | cut -f1))"
fi

if [ "$do_lint" = 1 ]; then
    flatpak run --command=flatpak-builder-lint org.flatpak.Builder manifest "$MANIFEST" || true
    flatpak run --command=flatpak-builder-lint org.flatpak.Builder repo "$OSTREE_REPO" || true
fi

# Run a command inside the build sandbox against the built /app, with no
# display, audio, host session/system bus or network, and HOME/XDG dirs in a
# throwaway directory under BUILD_ROOT (the root conftest.py redirects them
# again per test run). Never `flatpak run`: that would use the real session.
SANDBOX_EXTRA=()
sandbox_run() {
    local scratch="$BUILD_ROOT/sandbox-home"
    rm -rf "$scratch"
    mkdir -p "$scratch"
    flatpak build "${SANDBOX_EXTRA[@]}" \
        --filesystem="$REPO_ROOT" --filesystem="$BUILD_ROOT" \
        --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 \
        --nosocket=pulseaudio --nosocket=session-bus --nosocket=system-bus \
        --unshare=network \
        --env=HOME="$scratch" \
        --env=XDG_CONFIG_HOME="$scratch/config" --env=XDG_DATA_HOME="$scratch/data" \
        --env=XDG_CACHE_HOME="$scratch/cache" --env=XDG_RUNTIME_DIR="$scratch/runtime" \
        --env=PYTHONPATH="$BUILD_ROOT/testdeps" \
        --env=PYTHONDONTWRITEBYTECODE=1 \
        --env=WAYLAND_DISPLAY= --env=DISPLAY= --env=PULSE_SERVER= \
        --env=DBUS_SESSION_BUS_ADDRESS= \
        "$BUILD_DIR" \
        sh -c 'mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR" && cd "$0" && exec "$@"' \
        "$REPO_ROOT" "$@"
}

if [ "$do_test" = 1 ] || [ "$do_e2e" = 1 ]; then
    # pytest goes into a throwaway --target under BUILD_ROOT, never into /app.
    if [ ! -d "$BUILD_ROOT/testdeps/pytest" ]; then
        flatpak run --command=pip3 --share=network --filesystem="$BUILD_ROOT" "$SDK" \
            install -q --target "$BUILD_ROOT/testdeps" pytest
    fi
fi

if [ "$do_test" = 1 ]; then
    # tests/bench exercises the dev-only benchmark harness (needs jiwer), not
    # the shipped app.
    sandbox_run python3 -m pytest -q -p no:cacheprovider -o addopts='' \
        -m 'not e2e and not gui' --ignore=tests/bench tests
fi

if [ "$do_e2e" = 1 ]; then
    # `flatpak build` finds /app/lib via LD_LIBRARY_PATH, but the e2e harness
    # starts the daemon with an allowlisted env (tests/e2e/env_builder.py)
    # that drops it, so sounddevice could not dlopen PortAudio. `flatpak run`
    # instead regenerates an ld.so.cache that includes /app/lib
    # (regenerate_ld_cache in flatpak-run.c); mirror that here.
    flatpak build --unshare=network --filesystem="$BUILD_ROOT" "$BUILD_DIR" \
        ldconfig -X -C "$BUILD_ROOT/ld.so.cache" -f /dev/null /app/lib /usr/lib/x86_64-linux-gnu
    # Keep /app/share out of XDG_DATA_DIRS so the private test bus does not
    # see the app's own activation file (its relative Exec= is only valid
    # after Flatpak rewrites it on install) and try to auto-start it.
    SANDBOX_EXTRA=(--bind-mount=/etc/ld.so.cache="$BUILD_ROOT/ld.so.cache"
                   --env=XDG_DATA_DIRS=/usr/share)
    # scripts/test_e2e.sh wraps dbus-run-session: a private bus that lives
    # only inside the sandbox.
    sandbox_run env PYTHON=python3 sh scripts/test_e2e.sh -q -p no:cacheprovider -o addopts=''
fi
