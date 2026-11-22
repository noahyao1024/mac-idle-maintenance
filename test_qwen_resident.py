import importlib.util
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("resident", Path(__file__).parent / "examples/ollama-resident/qwen-resident.py")
resident = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resident)


class ResidentTests(unittest.TestCase):
    def test_loaded_qwen_is_not_reloaded(self):
        with mock.patch.object(resident, "local_api", return_value={"models": [{"name": resident.MODEL}]}) as api:
            resident.ensure_resident()
            api.assert_called_once_with("/api/ps")

    def test_other_loaded_model_is_not_evicted(self):
        with mock.patch.object(resident, "local_api", return_value={"models": [{"name": "another-model"}]}) as api:
            resident.ensure_resident()
            api.assert_called_once_with("/api/ps")

    def test_empty_server_preloads_only_qwen_and_verifies(self):
        with mock.patch.object(resident, "local_api", side_effect=[
            {"models": []}, {"done": True}, {"models": [{"name": resident.MODEL}]}]) as api:
            resident.ensure_resident()
            self.assertEqual([call.args[0] for call in api.call_args_list],
                             ["/api/ps", "/api/generate", "/api/ps"])
            self.assertEqual(api.call_args_list[1].args[1]["keep_alive"], -1)


if __name__ == "__main__":
    unittest.main()
