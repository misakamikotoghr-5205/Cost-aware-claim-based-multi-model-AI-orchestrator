"""Real concurrent child processes must use their owning profile's scope."""
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys

from src.executors import _run_process, execution_child_environment, execution_environment


def test_concurrent_native_profiles_do_not_use_launch_profiles_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", "launch-profile")
    command = [sys.executable, "-c", "import os; print(os.environ['HERMES_HOME'])"]
    def run(name):
        environment = dict(os.environ, HERMES_HOME=name)
        with execution_environment(environment):
            result = _run_process(command, tmp_path, "", 5)
        return result.stdout.strip()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(run, ["served-profile-a", "served-profile-b"])) == ["served-profile-a", "served-profile-b"]
    assert os.environ["HERMES_HOME"] == "launch-profile"
    assert execution_child_environment() is None


def test_profile_scope_is_reset_after_failure_and_input_is_not_mutable():
    environment = {"HERMES_HOME": "owning-profile"}
    try:
        with execution_environment(environment):
            environment["HERMES_HOME"] = "changed-caller-dict"
            assert execution_child_environment()["HERMES_HOME"] == "owning-profile"
            raise ValueError("fixture")
    except ValueError:
        pass
    assert execution_child_environment() is None
