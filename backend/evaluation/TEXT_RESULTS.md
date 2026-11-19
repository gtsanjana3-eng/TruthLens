# AI text detector – measured held-out validation

Generated 2026-11-19T12:09:02.093179+00:00 by `python -m evaluation.eval_modality --modality text --stage heldout`.

**Model:** `fakespot-ai/roberta-base-ai-text-detection-v1` @ `f9cdb14d1f8b`  
**Held-out data:** textsightai/ai-text-detection-benchmark-2026 – 206 samples never used to choose the model.

| ROC-AUC | Precision ('likely AI') | Recall | F1 | FPR | FNR | 'No indication' correct | Coverage |
|---|---|---|---|---|---|---|---|
| 0.9633 | 0.600 | 0.964 (0.879, 0.99) | 0.740 | 0.240 (0.179, 0.314) | 0.036 | 75/75 | 0.801 |

Rule fixed before testing: 'likely AI' if score ≥ 0.7, 'no indication' if ≤ 0.3. Scores are uncalibrated model outputs.
Decision-grade if ROC-AUC ≥ 0.75: **yes**.
