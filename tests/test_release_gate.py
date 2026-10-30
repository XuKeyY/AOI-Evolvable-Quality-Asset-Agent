import unittest

from services.version_service import DEFAULT_GATE, gate


class ReleaseGateTests(unittest.TestCase):
    def candidate(self, balanced_accuracy=0.9, fpr=0.0, fnr=0.2):
        return {
            "normal": 20,
            "defect": 30,
            "false_positive_rate": fpr,
            "false_negative_rate": fnr,
            "balanced_accuracy": balanced_accuracy,
        }

    def test_eighty_percent_policy_accepts_current_candidate_metrics(self):
        result = gate(self.candidate(), None, DEFAULT_GATE)
        self.assertTrue(result["passed"], result["failures"])

    def test_balanced_accuracy_below_eighty_percent_fails(self):
        result = gate(self.candidate(0.79, 0.2, 0.22), None, DEFAULT_GATE)
        self.assertFalse(result["passed"])
        self.assertIn("balanced_accuracy below absolute gate", result["failures"])


if __name__ == "__main__":
    unittest.main()
