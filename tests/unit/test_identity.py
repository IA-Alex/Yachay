import unittest

from motor.ingestion.identity import compute_job_key


class JobKeyTests(unittest.TestCase):
    def _key(self, **over):
        base = dict(file_hash="h", pipeline_version="p1", rules_version="r1",
                    config_hash="c1", llm_enabled=False)
        base.update(over)
        return compute_job_key(**base)

    def test_same_input_and_config_gives_same_key(self):
        self.assertEqual(self._key(), self._key())

    def test_llm_enabled_changes_the_key(self):
        self.assertNotEqual(self._key(llm_enabled=False),
                            self._key(llm_enabled=True))

    def test_model_and_prompt_only_matter_when_llm_enabled(self):
        self.assertEqual(self._key(llm_enabled=False, model_version="m2"),
                         self._key(llm_enabled=False))
        self.assertNotEqual(self._key(llm_enabled=True, model_version="m1"),
                            self._key(llm_enabled=True, model_version="m2"))
