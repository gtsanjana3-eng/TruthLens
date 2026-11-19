# AI audio detector – measured held-out validation

Generated 2026-11-19T12:14:55.045449+00:00 by `python -m evaluation.eval_modality --modality audio --stage heldout`.

**Model:** `abhishtagatya/wav2vec2-base-960h-itw-deepfake` @ `cb2350dec824`  
**Held-out data:** garystafford/deepfake-audio-detection – 200 samples never used to choose the model.

| ROC-AUC | Precision ('likely AI') | Recall | F1 | FPR | FNR | 'No indication' correct | Coverage |
|---|---|---|---|---|---|---|---|
| 0.8583 | 1.000 | 0.120 (0.07, 0.198) | 0.214 | 0.000 (0.0, 0.037) | 0.880 | 99/182 | 0.970 |

Rule fixed before testing: 'likely AI' if score ≥ 0.7, 'no indication' if ≤ 0.3. Scores are uncalibrated model outputs.
Decision-grade if ROC-AUC ≥ 0.75: **yes**.
