# AI image detector – measured held-out validation

Generated 2026-11-19T12:19:31.716512+00:00 by `python -m evaluation.eval_modality --modality image --stage heldout`.

**Model:** `haywoodsloan/ai-image-detector-deploy` @ `4ae1822603cb`  
**Held-out data:** Parveshiiii/AI-vs-Real – 200 samples never used to choose the model.

| ROC-AUC | Precision ('likely AI') | Recall | F1 | FPR | FNR | 'No indication' correct | Coverage |
|---|---|---|---|---|---|---|---|
| 0.9529 | 0.846 | 0.930 (0.863, 0.966) | 0.886 | 0.170 (0.109, 0.255) | 0.070 | 83/90 | 1.000 |

Rule fixed before testing: 'likely AI' if score ≥ 0.7, 'no indication' if ≤ 0.3. Scores are uncalibrated model outputs.
Decision-grade if ROC-AUC ≥ 0.75: **yes**.
