import statistics
from bench.runner import median_rtf, pick_device, load_manifest


def test_median_rtf():
    assert median_rtf([0.15, 0.16, 0.17]) == 0.16


def test_pick_device_prefers_cuda_when_available_and_capable():
    assert pick_device(caps_devices=["cpu", "cuda"], hw_kind="gpu") == "cuda"


def test_pick_device_falls_back_to_cpu():
    assert pick_device(caps_devices=["cpu", "cuda"], hw_kind="cpu") == "cpu"


def test_pick_device_vulkan_only_engine_on_gpu_box():
    # whisper.cpp exposes cpu+vulkan; no cuda -> vulkan not chosen unless explicitly asked
    assert pick_device(caps_devices=["cpu", "vulkan"], hw_kind="gpu") == "cpu"


def test_load_manifest(tmp_path):
    m = tmp_path / "es.jsonl"
    m.write_text('{"file":"es_00.wav","duration_s":5.0,"ref":"hola"}\n')
    rows = load_manifest(m)
    assert rows[0]["ref"] == "hola"
