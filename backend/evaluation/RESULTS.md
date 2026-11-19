# TruthLens evaluation results (measured)

Generated 2026-11-19T05:44:09.800297+00:00 · 12 genuine + 12 manipulated samples per modality · Python 3.10.4

**Dataset:** Deterministic synthetic genuine/manipulated pairs (evaluation/synth.py) – NOT real-world deepfakes.  
**Decision rule:** flagged if any indicator with severity low/medium/high.

| Modality | N | Accuracy | Precision | Recall | F1 | FPR | FNR | Mean ms | Max ms |
|---|---|---|---|---|---|---|---|---|---|
| image | 24 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 161.0 | 197.1 |
| video | 24 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 713.1 | 2012.5 |
| audio | 24 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 117.7 | 166.3 |
| text_context | 24 | 1.0 | 1.0 | 1.0 | 1.0 | 0.0 | 0.0 | 1.7 | 24.8 |

These figures only show that each pipeline detects the specific, documented synthetic manipulations. They say nothing about accuracy on real-world deepfakes, which requires licensed benchmarks (see README).
