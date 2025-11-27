#!/usr/bin/env python3
"""Test script for WhisperAloud D-Bus daemon service."""

import subprocess
import time
import sys
from pathlib import Path

def run_command(cmd, check=True):
    """Run a command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {result.stderr}")
        return None
    return result

def test_daemon():
    """Test the daemon functionality."""
    print("Testing WhisperAloud D-Bus daemon...")

    # Check if we're in the right directory
    if not Path('pyproject.toml').exists():
        print("Error: Run this script from the project root directory")
        return False

    # Install the package in development mode
    print("Installing package...")
    if not run_command([sys.executable, '-m', 'pip', 'install', '-e', '.']):
        return False

    # Start daemon in background
    print("Starting daemon...")
    daemon_proc = subprocess.Popen(
        [sys.executable, '-m', 'whisper_aloud', '--daemon'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # Wait a bit for daemon to start
    time.sleep(2)

    try:
        # Check if daemon is running
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'status'], check=False)
        if result and result.returncode == 0:
            print("✓ Daemon is running")
        else:
            print("✗ Daemon failed to start")
            return False

        # Test start command
        print("Testing start command...")
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'start'], check=False)
        if result and result.returncode == 0:
            print("✓ Start command successful")
        else:
            print("✗ Start command failed")
            return False

        # Wait a moment
        time.sleep(1)

        # Check status again
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'status'], check=False)
        if result and 'recording' in result.stdout.lower():
            print("✓ Recording started")
        else:
            print("✗ Recording not started")
            return False

        # Test stop command
        print("Testing stop command...")
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'stop'], check=False)
        if result and result.returncode == 0:
            print("✓ Stop command successful")
        else:
            print("✗ Stop command failed")
            return False

        # Wait for transcription to complete
        time.sleep(3)

        # Test quit command
        print("Testing quit command...")
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'quit'], check=False)
        if result and result.returncode == 0:
            print("✓ Quit command successful")
        else:
            print("✗ Quit command failed")
            return False

        # Wait for daemon to stop
        time.sleep(1)

        # Check if daemon stopped
        result = run_command([sys.executable, '-m', 'whisper_aloud', 'status'], check=False)
        if result and result.returncode != 0:
            print("✓ Daemon stopped")
        else:
            print("✗ Daemon still running")
            return False

        print("All tests passed! ✓")
        return True

    finally:
        # Clean up daemon process
        if daemon_proc.poll() is None:
            daemon_proc.terminate()
            try:
                daemon_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()

if __name__ == '__main__':
    success = test_daemon()
    sys.exit(0 if success else 1)