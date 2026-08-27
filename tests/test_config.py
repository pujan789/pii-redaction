from taxhance_pii.config import Settings


def test_new_model_defaults() -> None:
    settings = Settings(token_pepper="x" * 40)
    assert settings.model_id == "google/gemma-4-E2B-it"
    assert settings.model_revision == "3e22461f65e89153144f8adb70e3b8c2cc9845a7"
    assert settings.vllm_base_url == "http://127.0.0.1:8000/v1"
    assert settings.vllm_launch is True
    assert settings.detector_concurrency == 8


def test_vllm_bounds() -> None:
    settings = Settings(token_pepper="x" * 40)
    assert 1024 <= settings.vllm_port <= 65_535
    assert settings.vllm_startup_timeout_seconds == 600
    assert 0.3 <= settings.vllm_gpu_memory_utilization <= 0.98
