"""Tests for configuration persistence utilities."""

import json
from pathlib import Path

import pytest

from whisper_aloud.config import WhisperAloudConfig
from whisper_aloud.utils.config_persistence import save_config_to_file


class TestSaveConfigToFile:
    """Test save_config_to_file function."""

    @pytest.fixture(autouse=True)
    def setup_home(self, monkeypatch, tmp_path):
        """Mock HOME environment variable to use temporary directory."""
        monkeypatch.setenv("HOME", str(tmp_path))

    def test_save_config_to_file_creates_directory(self, tmp_path):
        """Test that save creates the config directory if it doesn't exist."""
        config_dir = tmp_path / ".config" / "whisper_aloud"
        assert not config_dir.exists()

        config = WhisperAloudConfig.load()
        result_path = save_config_to_file(config)

        assert config_dir.exists()
        assert result_path == config_dir / "config.json"
        assert result_path.exists()

    def test_save_config_to_file_valid_json(self, tmp_path):
        """Test that saved file contains valid JSON with expected structure."""
        config = WhisperAloudConfig.load()
        result_path = save_config_to_file(config)

        # Read and parse the JSON
        with open(result_path, 'r') as f:
            data = json.load(f)

        # Check top-level sections exist
        assert "model" in data
        assert "transcription" in data
        assert "audio" in data
        assert "clipboard" in data
        assert "persistence" in data

        # Check model section
        model = data["model"]
        assert "name" in model
        assert "device" in model
        assert "compute_type" in model
        assert "download_root" in model

    def test_save_config_to_file_preserves_values(self, tmp_path):
        """Test that config values are correctly serialized."""
        # Create config with specific values
        config = WhisperAloudConfig.load()
        config.model.name = "tiny"
        config.transcription.language = "fr"
        config.audio.sample_rate = 22050

        result_path = save_config_to_file(config)

        with open(result_path, 'r') as f:
            data = json.load(f)

        assert data["model"]["name"] == "tiny"
        assert data["transcription"]["language"] == "fr"
        assert data["audio"]["sample_rate"] == 22050

    def test_save_config_to_file_handles_none_values(self, tmp_path):
        """Test that None values are correctly serialized."""
        config = WhisperAloudConfig.load()
        config.model.download_root = None
        config.audio.device_id = None

        result_path = save_config_to_file(config)

        with open(result_path, 'r') as f:
            data = json.load(f)

        assert data["model"]["download_root"] is None
        assert data["audio"]["device_id"] is None

    def test_save_config_to_file_creates_parent_dirs(self, tmp_path, monkeypatch):
        """Test that parent directories are created."""
        deep_path = tmp_path / "very" / "deep" / "config" / "dir"
        config_dir = deep_path / ".config" / "whisper_aloud"

        # Temporarily change home to our test dir
        monkeypatch.setenv("HOME", str(deep_path))

        config = WhisperAloudConfig.load()
        result_path = save_config_to_file(config)

        assert config_dir.exists()
        assert result_path.exists()