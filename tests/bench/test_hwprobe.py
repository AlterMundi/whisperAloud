from bench.hwprobe import parse_cpu_model, parse_nvidia_smi, probe


def test_parse_cpu_model():
    cpuinfo = "processor\t: 0\nmodel name\t: 13th Gen Intel(R) Core(TM) i7-1360P\nflags\t: ...\n"
    assert parse_cpu_model(cpuinfo) == "13th Gen Intel(R) Core(TM) i7-1360P"


def test_parse_cpu_model_missing():
    assert parse_cpu_model("processor\t: 0\n") is None


def test_parse_nvidia_smi():
    out = "NVIDIA GeForce RTX 4090, 24564 MiB\n"
    name, vram_gb = parse_nvidia_smi(out)
    assert name == "NVIDIA GeForce RTX 4090"
    assert vram_gb == 24  # 24564 MiB rounded to GB


def test_parse_nvidia_smi_empty():
    assert parse_nvidia_smi("") == (None, None)


def test_probe_returns_required_keys():
    hw = probe(hw_id="testbox")
    for key in ("id", "kind", "gpu", "vram_gb", "cpu", "cores", "ram_gb", "os"):
        assert key in hw
    assert hw["id"] == "testbox"
    assert hw["kind"] in ("cpu", "gpu")
