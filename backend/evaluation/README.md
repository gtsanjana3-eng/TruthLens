# Evaluation dataset and method

`python -m evaluation.run_eval --pairs 12` regenerates the dataset, runs the **real agents**, and writes
`results.json` and `RESULTS.md` with whatever numbers it actually measures. Nothing in those files is hand-edited.

## Dataset (synthetic, deterministic)

Built by `evaluation/synth.py` with fixed seeds, so every run is reproducible. Every manipulated sample comes from a
known operation, so ground truth is exact.

| Modality | Genuine sample | Manipulated sample |
|---|---|---|
| Image | Photo-like scene (shapes, fine texture, sensor-like noise), JPEG q92 | Alternating: **copy-move** (130×130 px patch cloned elsewhere) or **splice** (region pasted from another scene that was compressed at q35 and re-noised) |
| Video | 15 fps panning scene with a moving object (mp4v) | 3 foreign frames inserted mid-clip + 6 frozen duplicate frames |
| Audio | 12 s speech-like harmonic signal with prosody and smooth pauses | Segment from another recording with a louder noise floor, hard cuts, 120 ms of digital silence |
| Text/context | Caption whose date and device match the image's EXIF | Caption with a contradicting date and device |

**Decision rule:** a sample counts as "flagged" if any indicator has severity low, medium or high. Info-level notes don't count.

## What the numbers mean and what they don't

* They show that each pipeline reliably detects **these specific, documented manipulations** without flagging the
  matching genuine samples.
* They **do not** measure detection of real-world deepfakes (face swaps, diffusion images, neural voice clones). The
  classical detectors here are not designed to catch those, and the UI and reports say so.
* For real-world figures, plug licensed benchmarks into `run_eval.py`. Examples are FaceForensics++, DFDC and Celeb-DF
  for video, ASVspoof 2019/2021 for audio, and CASIA v2 or Columbia for image splicing. Each requires accepting its own
  licence terms, so none of them is bundled.

## Issue found by evaluation

The first run measured an audio **false-positive rate of 0.75**. The cause was the waveform-discontinuity detector
firing at the start and end of the file, where the median filter is unreliable. That would happen on real recordings
too. It was fixed in audio agent v1.1.1, which excludes the first and last 100 ms, and the evaluation was re-run.
