# Learned video detector – measured validation

Generated 2026-11-19T10:21:26.207131+00:00 by `python -m evaluation.run_video_eval` (agent v2.1.0, 24 frames/video, 19.85 s/video on CPU).

**Data:** 80 labelled videos from `angads24/deepfake-video` @ `3470af66b5` (Google/Jigsaw DeepFakeDetection clips), fixed random selection (seed 2026); split: **heldout** (held-out = never used to choose models). The models' training sets are not documented by their authors, so overlap with these clips **cannot be ruled out**; no model or threshold was fitted on this data.

**Fixed thresholds (set before testing):** likely-AI if mean ≥ 0.7 and ≥60% of inputs ≥0.5; no-indication if mean ≤ 0.3.

## Per detector

| Detector | Model | Videos applicable | ROC-AUC | Mean score fake / real |
|---|---|---|---|---|
| Face A | `hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand50-aligned_unaugmentation` | 80 | 0.8531 | 0.5495 / 0.1542 |
| Face B (independent) | `prithivMLmods/Deep-Fake-Detector-v2-Model` | 80 | 0.4981 | 0.6421 / 0.6428 |
| Full-frame synthetic | `Organika/sdxl-detector` | 80 | 0.525 | 0.5924 / 0.5194 |

Operating-point metrics (fake = positive). TP/FP/TN/FN; 95% Wilson intervals in brackets.

| Detector & rule | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|
| Face A – verdict 'likely AI' | 80 | 15/0/40/25 | 1.000 | 0.375 (0.242, 0.53) | 0.545 | 0.000 (0.0, 0.088) | 0.625 |
| Face A – mean ≥ 0.5 | 80 | 24/1/39/16 | 0.960 | 0.600 (0.446, 0.737) | 0.739 | 0.025 (0.004, 0.129) | 0.400 |
| Face B (independent) – verdict 'likely AI' | 80 | 17/19/21/23 | 0.472 | 0.425 (0.285, 0.578) | 0.447 | 0.475 (0.329, 0.625) | 0.575 |
| Face B (independent) – mean ≥ 0.5 | 80 | 31/33/7/9 | 0.484 | 0.775 (0.625, 0.877) | 0.596 | 0.825 (0.68, 0.913) | 0.225 |
| Full-frame synthetic – verdict 'likely AI' | 80 | 21/13/27/19 | 0.618 | 0.525 (0.375, 0.671) | 0.568 | 0.325 (0.201, 0.48) | 0.475 |
| Full-frame synthetic – mean ≥ 0.5 | 80 | 25/22/18/15 | 0.532 | 0.625 (0.47, 0.758) | 0.575 | 0.550 (0.398, 0.693) | 0.375 |

## Headline shown in the app (decision-grade policy)

Only decision-grade detectors (measured AUC ≥ 0.75) may produce 'likely AI' / 'no indication'.

| True label | likely AI | no indication | inconclusive | models disagree | no validated detector |
|---|---|---|---|---|---|
| real | 0 | 28 | 12 | 0 | 0 |
| fake | 15 | 9 | 16 | 0 | 0 |

Coverage (definite answer): **65.0%**. 'No indication' was given to **9** fake video(s) – 'no indication' is not proof of authenticity.

| Scope | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|
| Decided videos only | 52 | 15/0/28/9 | 1.000 | 0.625 (0.427, 0.788) | 0.769 | 0.000 (0.0, 0.121) | 0.375 |
| All videos (abstentions = not flagged) | 80 | 15/0/40/25 | 1.000 | 0.375 (0.242, 0.53) | 0.545 | 0.000 (0.0, 0.088) | 0.625 |


## For comparison: headline without the decision-grade policy

Coverage (videos receiving a definite 'likely AI' or 'no indication'): **3.8%**

| True label | likely AI | no indication | inconclusive | models disagree | not applicable |
|---|---|---|---|---|---|
| real | 0 | 0 | 16 | 24 | 0 |
| fake | 3 | 0 | 6 | 31 | 0 |

| Scope | N | TP/FP/TN/FN | Precision | Recall | F1 | FPR | FNR |
|---|---|---|---|---|---|---|---|
| Decided videos only | 3 | 3/0/0/0 | 1.000 | 1.000 (0.438, 1.0) | 1.000 | –  | 0.000 |
| All videos (abstentions = not flagged) | 80 | 3/0/40/37 | 1.000 | 0.075 (0.026, 0.199) | 0.140 | 0.000 (0.0, 0.088) | 0.925 |

## Decision-grade policy (v2.1)

A detector may decide the headline only if its measured ROC-AUC here is ≥ 0.75 (fixed before re-testing). Decision-grade: Face A=True, Face B (independent)=False, Full-frame synthetic=False.

Headline under this policy: real: {'inconclusive': 12, 'no_indication': 28}; fake: {'likely_ai': 15, 'inconclusive': 16, 'no_indication': 9}

These are measured results on one dataset (face-swap deepfakes of paid actors). They do not transfer to other manipulation types (fully AI-generated video, lip-sync, rendered graphics) or other compression levels.
