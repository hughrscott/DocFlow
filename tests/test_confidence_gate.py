"""Tests for src/filing/confidence_gate."""
from __future__ import annotations

from src.clustering.clusterer import DocumentCandidate
from src.classification.classifier import FilingDecision
from src.filing.confidence_gate import gate_decisions


def _make_decision(confidence: float, rule: str = "some_rule") -> FilingDecision:
    candidate = DocumentCandidate(
        pages=[1], institution="pnc", account=None,
        period=None, doc_type=None, clustering_confidence=confidence,
    )
    return FilingDecision(
        candidate=candidate,
        filename="test.pdf",
        target_directory="/tmp/test",
        rule_matched=rule,
        confidence=confidence,
        auto_file=confidence >= 0.75,
        notes=None,
    )


class TestGateDecisions:
    def test_high_confidence_auto_filed(self):
        decisions = [_make_decision(0.9)]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.75})
        assert len(auto) == 1
        assert len(review) == 0

    def test_low_confidence_to_review(self):
        decisions = [_make_decision(0.5)]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.75})
        assert len(auto) == 0
        assert len(review) == 1

    def test_threshold_boundary(self):
        decisions = [_make_decision(0.75)]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.75})
        assert len(auto) == 1

    def test_unmatched_always_review(self):
        decisions = [_make_decision(0.9, rule="none")]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.75})
        assert len(auto) == 0
        assert len(review) == 1

    def test_mixed_decisions(self):
        decisions = [
            _make_decision(0.9),
            _make_decision(0.5),
            _make_decision(0.8),
            _make_decision(0.3),
        ]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.75})
        assert len(auto) == 2
        assert len(review) == 2

    def test_custom_threshold(self):
        decisions = [_make_decision(0.6)]
        auto, review = gate_decisions(decisions, {"confidence_threshold": 0.5})
        assert len(auto) == 1
