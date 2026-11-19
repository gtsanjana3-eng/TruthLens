"""Alternative Explanation Engine.

Maps each indicator type to plausible non-manipulation explanations and to the additional
evidence that would help distinguish them. Curated forensic knowledge, not model output."""
from __future__ import annotations

from .base import Indicator

KNOWLEDGE: dict[str, tuple[list[str], list[str]]] = {
    "ela_outlier_region": (
        ["Region has different texture/contrast (text, sharp edges, saturated colours) which naturally re-compresses differently",
         "Image was resized or re-saved several times by social platforms",
         "Local sharpening or noise reduction applied uniformly by the camera pipeline"],
        ["Obtain the earliest available copy (original camera file)", "Compare with other photos from the same device",
         "Inspect the region at 100% zoom for blending edges, lighting or shadow inconsistencies"]),
    "noise_inconsistency": (
        ["Different surfaces (sky vs foliage) have different noise visibility", "In-camera HDR or multi-frame merging",
         "Denoising applied selectively by smartphone computational photography"],
        ["Request the RAW/original file", "Compare sensor noise pattern (PRNU) with reference images from the claimed camera"]),
    "copy_move_cluster": (
        ["Repeating textures or patterns (tiles, windows, foliage, crowds) produce similar keypoints",
         "Symmetric objects or reflections"],
        ["Visually compare matched regions for identical pixel detail", "Check whether duplicated area hides or adds an object"]),
    "spectral_periodicity": (
        ["Resampling/resizing introduces periodic spectral peaks", "Halftone, screen moiré or photographing a display",
         "JPEG 8x8 block grid artefacts"],
        ["Run a dedicated, validated synthetic-image classifier", "Obtain the original-resolution file"]),
    "editing_software_tag": (
        ["Routine exposure/crop edits in an editor do not alter content", "Export from a phone gallery app or messaging app"],
        ["Ask the source for the unedited original", "Check edit history in the software if available (XMP history)"]),
    "generator_metadata": (
        ["Image may be intentionally labelled AI-generated art, not presented as a real photo",
         "Metadata could have been copied from another file"],
        ["Confirm with the source how the image was produced", "Look for a verifiable Content Credentials (C2PA) manifest"]),
    "missing_camera_metadata": (
        ["Most social networks and messaging apps strip EXIF on upload", "Screenshots and exports carry no camera metadata"],
        ["Locate the original upload/source file", "Use reverse image search to find earlier copies"]),
    "timestamp_mismatch": (
        ["Camera clock was set incorrectly or to a different time zone", "File was copied/exported, updating modification time"],
        ["Compare with timestamps of other files from the same device", "Check witnesses, weather or shadow direction for the claimed time"]),
    "abrupt_temporal_change": (
        ["Normal scene cut or camera switch in an edited broadcast", "Sudden lighting change (flash, lights switched on)",
         "Fast camera motion or auto-exposure adjustment"],
        ["Review the flagged frames manually in the frame inspector", "Obtain an unedited continuous recording"]),
    "duplicated_frames": (
        ["Variable frame-rate recording or encoder frame duplication", "Static scene with no motion",
         "Low-light camera dropping to a lower capture rate"],
        ["Check container frame timing", "Compare with the original recording device output"]),
    "face_region_inconsistency": (
        ["Shallow depth of field keeps the face at a different sharpness", "Beauty/smoothing filters applied by the camera app",
         "Motion blur from head movement"],
        ["Inspect face boundary (hairline, jaw, ears) frame by frame", "Run a dedicated face-swap detection model",
         "Compare with known authentic footage of the person"]),
    "lip_sync_mismatch": (
        ["Face partially turned, occluded or too small for reliable mouth tracking", "Audio is a voice-over, translation or added narration",
         "Background speech from someone off-camera", "Audio/video stream offset introduced by re-encoding"],
        ["Check whether the visible person is meant to be the speaker", "Obtain the original recording with its native audio track",
         "Have a human reviewer watch flagged timestamps with audio"]),
    "audio_splice_discontinuity": (
        ["Microphone bump, plosive or handling noise", "Automatic gain control or noise suppression switching",
         "Network audio dropout in a call recording"],
        ["Listen to the flagged segment with headphones", "Check spectrogram for consistent background (room tone / mains hum) across the cut"]),
    "noise_floor_shift": (
        ["Speaker moved relative to microphone or background noise source changed", "Noise suppression adapting"],
        ["Compare the background noise spectrum before and after", "Ask for the original continuous recording"]),
    "digital_silence": (
        ["Recording app muted or gated the input", "Editing to remove pauses in an interview (not deceptive per se)"],
        ["Ask whether the recording was edited", "Obtain the unedited file"]),
    "band_limited_audio": (
        ["Telephone/VoIP codec or Bluetooth headset limits bandwidth", "Low-bitrate compression (MP3/AAC)"],
        ["Confirm the recording channel (phone call, voice note, etc.)", "Run a dedicated anti-spoofing / voice-clone detector"]),
    "low_pitch_variability": (
        ["Monotone speaking style, reading a script, or short sample", "Pitch tracking errors on noisy audio"],
        ["Compare with authentic recordings of the claimed speaker", "Run a validated synthetic-speech classifier"]),
    "clipping": (
        ["Microphone overload from a loud source"], ["Obtain a recording with correct input gain"]),
    "text_internal_conflict": (
        ["Text references multiple distinct events or a correction", "Typo or date format ambiguity (DD/MM vs MM/DD)"],
        ["Clarify with the author which date/figure is intended"]),
    "context_mismatch": (
        ["Metadata clock incorrect or stripped and rewritten by an app", "The caption describes a related but different moment",
         "Claim is approximate (e.g. 'last week')"],
        ["Verify the claim with an independent source", "Locate the original publication of the media"]),
    "transcript_alignment": (
        ["Transcript produced for an edited version of the media", "Subtitle timing offset", "Automatic captions with errors"],
        ["Re-generate the transcript from the supplied audio", "Ask for the source of the transcript"]),
    "learned_model_deepfake": (
        ["Heavy compression, beauty filters or low light can push face classifiers towards 'fake'",
         "The face or setting is unlike the model's training data (age, ethnicity, angle, resolution)",
         "Legitimate post-production (colour grading, stabilisation, upscaling)"],
        ["Check flagged timestamps frame by frame for blending at hairline, jaw and teeth",
         "Compare with known authentic footage of the same person",
         "Obtain the original, uncompressed recording and re-run the analysis",
         "Corroborate with a second, independently trained detector"]),
    "learned_model_temporal_jump": (
        ["Different person, face angle or lighting between sampled frames", "Scene cut or camera switch",
         "Motion blur or occlusion on one of the frames"],
        ["Step through the frames around the timestamp in the frame inspector", "Check whether the face identity stays consistent"]),
    "learned_model_ai_image": (
        ["Heavily edited, upscaled or HDR-processed photos can resemble generated images",
         "Digital art, renders or screenshots are not camera photos but are not deceptive per se"],
        ["Look for generation artefacts: hands, text, reflections, repeated textures",
         "Search for earlier copies of the image (reverse image search)",
         "Check for a verifiable Content Credentials (C2PA) manifest"]),
    "extension_mismatch": (
        ["The file was renamed, mis-saved or exported with a default extension", "A messaging/cloud app changed the extension on download"],
        ["Ask the source for the original file as it was created", "Compare with other files from the same source"]),
    "sensational_language": (
        ["Legitimate urgent news reporting", "Opinion or commentary style"],
        ["Check the claim against reputable independent sources"]),
}

DEFAULT = (["Natural variation in capture conditions or compression"], ["Request the original file and review manually"])


def enrich(ind: Indicator) -> Indicator:
    alts, checks = KNOWLEDGE.get(ind.indicator_type, DEFAULT)
    if not ind.alternative_explanations:
        ind.alternative_explanations = list(alts)
    if not ind.recommended_checks:
        ind.recommended_checks = list(checks)
    return ind
