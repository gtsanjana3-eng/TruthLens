"""AI-model layer (v3: one validated model per media type) – tested with mocked model outputs (no download)."""
import json

import numpy as np
import pytest

from app.agents import hf_models, ml_agent
from evaluation.synth import make_image_pair, make_video_pair, write_wav

CFG = {"video": ("org/face", "r1"), "image": ("org/img", "r2"), "audio": ("org/voice", "r3"), "text": ("org/text", "r4"),
       "cache_dir": ".", "offline": True, "yunet": None, "n_frames": 12, "require_validation": True}


def _validation(tmp_path, monkeypatch, auc: float, tp=40, fp=2, k=30, n=36):
    """Write held-out results for every media type in the formats the evaluation scripts produce."""
    files = {}
    for media in ("image", "audio", "text"):
        f = tmp_path / f"{media}_results.json"
        f.write_text(json.dumps({"dataset": f"{media}-set", "results": {CFG[media][0]: {
            "roc_auc": auc, "n": 200, "rule_likely_ai": {"tp": tp, "fp": fp}, "no_indication_npv": {"k": k, "n": n}}}}))
        files[media] = f
    v = tmp_path / "video_results.json"
    v.write_text(json.dumps({"meta": {"dataset": "dfd"}, "detectors": {"face_a": {"model": "org/face", "roc_auc": auc, "applicable_videos": 80}},
                             "app_headline": {"on_decided_only": {"tp": tp, "fp": fp, "tn": k, "fn": n - k}}}))
    files["video"] = v
    monkeypatch.setattr(ml_agent, "VALIDATION_FILES", files)


def test_band_and_confidence_are_measured(tmp_path, monkeypatch):
    assert ml_agent.band(0.9, 0.9) == "likely_ai" and ml_agent.band(0.9, 0.3) == "inconclusive" and ml_agent.band(0.1, 0.0) == "no_indication"
    _validation(tmp_path, monkeypatch, auc=0.9, tp=40, fp=2, k=30, n=36)
    v = ml_agent.validation_for("org/img", "image")
    assert v["decision_grade"] and v["precision"]["k"] == 40 and v["precision"]["n"] == 42
    c = ml_agent.confidence_for("likely_ai", v, "image")
    assert c["value"] == round(40 / 42, 3) and c["level"] == "high" and "40 of 42" in c["basis"]
    c2 = ml_agent.confidence_for("no_indication", v, "image")
    assert c2["value"] == round(30 / 36, 3) and "30 of 36" in c2["basis"]
    assert ml_agent.confidence_for("inconclusive", v, "image")["value"] is None
    assert ml_agent.validation_for("org/img", "audio") is None            # validation never crosses media types


def test_image_decision_grade_model(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    g, _ = make_image_pair(tmp_path, 31, "splice")
    monkeypatch.setattr(ml_agent, "content_profile", lambda img: {"noise_sigma": 2.0, "flat_fraction": 0.1, "rendered": False})
    monkeypatch.setattr(hf_models, "score_images", lambda imgs, *a, **k: [0.93] * len(imgs))
    r = ml_agent.analyze_image(str(g), "e1", CFG)
    a = r["artifacts"]
    assert a["verdict"] == "likely_ai" and a["confidence"]["value"] is not None
    assert r["suspicion_score"] == 0.93 and r["indicators"][0]["indicator_type"] == "learned_model_ai_image"
    assert a["detector"]["validation"]["roc_auc"] == 0.9 and "Scope:" in r["interpretation"]


def test_unvalidated_model_is_reference_only(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.55)                         # measured but below the 0.75 bar
    g, _ = make_image_pair(tmp_path, 32, "splice")
    monkeypatch.setattr(ml_agent, "content_profile", lambda img: {"noise_sigma": 2.0, "flat_fraction": 0.1, "rendered": False})
    monkeypatch.setattr(hf_models, "score_images", lambda imgs, *a, **k: [0.96] * len(imgs))
    r = ml_agent.analyze_image(str(g), "e1", CFG)
    assert r["artifacts"]["verdict"] == "no_validated_detector"
    assert r["indicators"] == [] and r["suspicion_score"] is None and r["artifacts"]["confidence"]["value"] is None
    assert any("reference only" in l for l in r["limitations"])


def test_rendered_graphics_not_applicable(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    called = []
    monkeypatch.setattr(hf_models, "score_images", lambda *a, **k: called.append(1) or [0.9])
    img = np.full((240, 320, 3), 30, np.uint8)
    img[100:120, 40:280] = 230
    import cv2

    p = tmp_path / "slide.png"
    cv2.imwrite(str(p), img)
    r = ml_agent.analyze_image(str(p), "e1", CFG)
    assert r["artifacts"]["verdict"] == "not_applicable" and not called


def test_ai_voice_windows_flagged(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    p = tmp_path / "v.wav"
    write_wav(p, (0.2 * np.sin(np.arange(16000 * 10) * 2 * np.pi * 180 / 16000)).astype(np.float32))
    monkeypatch.setattr(hf_models, "score_audio", lambda x, sr, *a, **k: [(0.0, 0.9), (4.0, 0.95), (6.0, 0.85)])
    r = ml_agent.analyze_audio(str(p), "e1", CFG)
    a = r["artifacts"]
    assert a["verdict"] == "likely_ai" and len(a["detector"]["windows"]) == 3
    assert [i["location"]["t_start"] for i in r["indicators"]] == [0.0, 4.0, 6.0]


def test_text_short_not_applicable_and_long_scored(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    assert ml_agent.analyze_text("too short to judge", "t", CFG)["artifacts"]["verdict"] == "not_applicable"
    long = " ".join(["The committee reviewed the proposal and found it consistent with prior guidance."] * 12)
    monkeypatch.setattr(hf_models, "score_text", lambda text, *a, **k: [(0, 0.05), (400, 0.1)])
    r = ml_agent.analyze_text(long, "t", CFG)
    assert r["artifacts"]["verdict"] == "no_indication" and len(r["artifacts"]["detector"]["chunks"]) == 2
    assert r["artifacts"]["confidence"]["value"] is not None and not r["indicators"]


def test_video_needs_faces_and_flags_frames(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.85)
    _, vid = make_video_pair(tmp_path, 33, seconds=3)
    monkeypatch.setattr(ml_agent, "detect_face", lambda img, yunet=None, margin=0.3: (None, None, "mock"))
    r = ml_agent.analyze_video(str(vid), "e1", CFG)
    assert r["artifacts"]["verdict"] == "not_applicable" and r["artifacts"]["faces_found"] == 0
    monkeypatch.setattr(ml_agent, "detect_face", lambda img, yunet=None, margin=0.3: (img[:64, :64].copy(), [0, 0, 64, 64], "mock"))
    monkeypatch.setattr(hf_models, "score_images", lambda imgs, *a, **k: [0.95] * len(imgs))
    r = ml_agent.analyze_video(str(vid), "e1", CFG, str(tmp_path / "art"))
    a = r["artifacts"]
    assert a["verdict"] == "likely_ai" and all(f["suspicious"] for f in a["frames"])
    assert a["visual_evidence"] and (tmp_path / "art" / a["visual_evidence"][0]["image"]).exists()


def test_model_failure_is_reported_not_faked(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    g, _ = make_image_pair(tmp_path, 34, "splice")
    monkeypatch.setattr(ml_agent, "content_profile", lambda img: {"noise_sigma": 2.0, "flat_fraction": 0.1, "rendered": False})

    def boom(*a, **k):
        raise OSError("Cannot connect to huggingface.co")

    monkeypatch.setattr(hf_models, "score_images", boom)
    r = ml_agent.analyze_image(str(g), "e1", CFG)
    assert r["artifacts"]["verdict"] == "unavailable" and r["suspicion_score"] is None and not r["indicators"]


def test_policy_fingerprint_changes_with_validation(tmp_path, monkeypatch):
    _validation(tmp_path, monkeypatch, auc=0.9)
    f1 = ml_agent.policy_fingerprint()
    _validation(tmp_path, monkeypatch, auc=0.7)
    assert ml_agent.policy_fingerprint() != f1


def test_pinned_label_maps_match_model_configs():
    """Fake-class indices are pinned from each model's config.json (documented label order) – never guessed."""
    expected = {  # id2label as published at the pinned revisions
        "hchcsuim/batch-size16_DFDC_opencv-1FPS_faces-expand50-aligned_unaugmentation": {0: "Fake", 1: "Real"},
        "MelodyMachine/Deepfake-audio-detection-V2": {0: "fake", 1: "real"},
        "abhishtagatya/wav2vec2-base-960h-itw-deepfake": {0: "bona-fide", 1: "spoof"},
        "Ateeqq/ai-vs-human-image-detector": {0: "ai", 1: "hum"},
        "haywoodsloan/ai-image-detector-deploy": {0: "artificial", 1: "real"},
        "fakespot-ai/roberta-base-ai-text-detection-v1": {0: "Human", 1: "AI"},
        "openai-community/roberta-base-openai-detector": {0: "Fake", 1: "Real"},
    }
    fake_words = ("fake", "spoof", "ai", "artificial")
    for model, id2label in expected.items():
        want = [i for i, lab in id2label.items() if lab.lower() in fake_words]
        assert hf_models.FAKE_CLASSES[model] == want, model


def test_unpinned_model_is_refused(monkeypatch):
    import types

    import transformers

    fake_model = types.SimpleNamespace(config=types.SimpleNamespace(num_labels=2, id2label={0: "x", 1: "y"}), eval=lambda: fake_model)
    monkeypatch.setattr(transformers.AutoImageProcessor, "from_pretrained", lambda *a, **k: object())
    monkeypatch.setattr(transformers.AutoModelForImageClassification, "from_pretrained", lambda *a, **k: fake_model)
    with pytest.raises(ValueError, match="not pinned"):
        hf_models.load("someone/unknown-model", "abc", "image", ".", offline=True)


def test_orchestrator_files_ai_results_under_agents(client, auth, case_id, fixtures_dir, monkeypatch):
    from app.config import get_settings

    from .conftest import upload

    s = get_settings()
    monkeypatch.setattr(ml_agent, "dependencies_available", lambda: (True, "ok"))
    monkeypatch.setattr(s, "hf_image_model", "")                        # not configured -> reported unavailable, never faked
    monkeypatch.setattr(ml_agent, "VALIDATION_FILES", {m: fixtures_dir / "none.json" for m in ("video", "image", "audio", "text")})
    g, _ = make_image_pair(fixtures_dir, 501, "splice")
    upload(client, auth, case_id, g, "image/jpeg")
    job = client.post(f"/api/cases/{case_id}/analyze", headers=auth).json()
    res = client.get(f"/api/jobs/{job['id']}/results", headers=auth).json()
    ai = next(r for r in res["agent_results"] if r["agent_name"] == "Image Forensics Agent (AI model)")
    assert ai["status"] == "unavailable" and ai["suspicion_score"] is None and ai["artifacts"]["verdict"] == "unavailable"
    lead = [r for r in res["agent_results"] if r["agent_name"] == "Lead Investigation Agent"]
    assert len(lead) == 1 and "assessments" in lead[0]["artifacts"] and "findings" in lead[0]["artifacts"]
