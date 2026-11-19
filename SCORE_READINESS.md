# Score-readiness checklist

Status key: ✅ done and verified · ⚠️ done with a stated limitation · ❌ not done.
Every row names its evidence. **No category is claimed to have reached 100.** Final scores are the judges' call.

## A. Code quality
| Item | Status | Evidence |
|---|---|---|
| Modular architecture | ✅ | `app/agents/*` (one module per agent), `services/` (orchestrator, jobs, isolation, reporting), `routers/` split by domain |
| Reusable components | ✅ | Shared `AgentOutput`/`Indicator`/`Signal` dataclasses; frontend `ui.tsx`, `Forensics.tsx`, `Charts.tsx`, `CaseProvider` |
| Type safety | ✅ | Pydantic schemas, SQLAlchemy `Mapped[]` types, TypeScript `strict` (`tsc --noEmit` passes with 0 errors) |
| Consistent error handling | ✅ | Agents return a `failed` status instead of raising; global 500 handler; per-field 422s; `ApiError` on the client |
| Documentation | ✅ | README, module docstrings, `evaluation/README.md`, `.env.example` |
| No linter configured | ⚠️ | No ruff or eslint config was added |

## B. Security
| Item | Status | Evidence |
|---|---|---|
| Password hashing, lockout, password policy | ✅ | `security.py`, `routers/auth.py`; tests `test_login_failures_generic_and_lockout`, `test_weak_password...` |
| AuthN/AuthZ, roles, object-level access | ✅ | `get_case_for_user`; `test_case_isolation_between_users`, `test_admin_only_endpoints` |
| Extension + MIME + signature validation, size/duration limits | ✅ | `storage.py`; `test_upload_rejections`, `test_upload_size_limit` |
| Path traversal, safe names | ✅ | `safe_join`, `safe_display_name`; `test_safe_names_and_traversal`, `test_artifact_path_traversal_blocked` |
| Isolated processing with timeout | ✅ | `services/isolation.py`; `test_isolated_worker_timeout` |
| Rate limiting, CORS, security headers, safe errors | ✅ | `ratelimit.py`, `main.py`; `test_security_headers_and_rate_limit` |
| Secrets management | ✅ | `.env.example`, production weak-secret refusal; `test_production_requires_strong_secret` |
| Audit log with tamper evidence | ✅ | Hash chain in `audit.py`; `test_audit_chain_detects_tampering` |
| Secure deletion and retention | ⚠️ | `scripts/retention.py` + test. Overwriting is best-effort on SSDs (documented) |
| Tokens in sessionStorage | ⚠️ | Vulnerable to XSS. Mitigated by nginx CSP; httpOnly cookies would be stronger |

## C. Efficiency
| Item | Status | Evidence |
|---|---|---|
| Async jobs with progress tracking | ✅ | `jobs.py` queue, `progress`/`stage` columns, UI polling bar |
| Streaming upload hashing; no full video in memory | ✅ | 1 MB chunked `store_upload`; video `grab()`/`retrieve()` with ≤10 fps sampling |
| Downscaled analysis copies; originals untouched | ✅ | Image capped at 1600 px, previews 640 px |
| Result caching | ✅ | Per (sha256, agent, version) JSON cache; the second e2e run hits it |
| Pagination, indexed and aggregated queries | ✅ | `/api/cases`, `/api/history`, `/audit` paginated; indexes in `models.py`; grouped counts |
| Timeouts and graceful failure | ✅ | Worker timeout + pool recycling; restart recovery in `recover_interrupted` |
| Measured processing time | ✅ | `evaluation/RESULTS.md` (mean/max ms per modality) |
| Load testing | ❌ | Not performed |

## D. Testing
| Item | Status | Evidence |
|---|---|---|
| Unit, API, auth, DB integration, upload, image/video/audio/text, cross-modal, PDF, security, error, e2e | ✅ | 46 backend tests pass (`pytest`) |
| Frontend component tests | ✅ | 17 tests pass (`npm test`) |
| Reproducible evaluation with all metrics | ⚠️ | `run_eval.py`. Synthetic dataset only; real-world benchmarks not run |
| Browser check of the full workflow | ✅ | Manual run in the in-app browser: all 16 pages rendered without console errors; no horizontal overflow at 375 px |
| Coverage percentage | ❌ | Not measured (no coverage tool configured) |

## E. Accessibility
| Item | Status | Evidence |
|---|---|---|
| Semantic HTML, landmarks, skip link | ✅ | `main`/`nav`/`aside`/`header`, table captions, `Layout.tsx` skip link |
| Keyboard navigation and visible focus | ✅ | Global `:focus-visible`; graph nodes focusable with Enter/Space; frame inspector arrow keys |
| Labels, validation messages, live regions | ✅ | `Field` wires `aria-describedby`/`aria-invalid`; tests assert accessible descriptions |
| Not relying on colour alone | ✅ | Severity symbols + text; graph edges distinguished by dash pattern + symbol + label |
| Text alternatives for visualisations | ✅ | Charts have `aria-label` summaries and the graph has a relationship list; tested |
| Reduced motion, high contrast, responsive | ✅ | `prefers-reduced-motion` plus user preference toggles; checked at mobile width |
| Automated audit (axe/Lighthouse) or screen-reader test | ❌ | Not performed |

## F. Problem-statement alignment
| Requirement | Status | Where |
|---|---|---|
| Image / video / audio / text analysis | ✅ | Agents and workspaces for each |
| Cross-modal correlation | ✅ | `crossmodal_agent.py`, Evidence DNA graph |
| Manipulation indicators (regions, frames, segments, metadata) | ✅ | `manipulation_indicators` table with typed locations |
| Confidence, uncertainty, alternative explanations | ✅ | Review agent, Confidence breakdown page, `explanations.py` |
| Report and traceability | ✅ | PDF with hashes, locations, history and audit; report SHA-256 stored |
| Human review workflow | ✅ | Notes with verdicts on indicators/evidence; case members; case status |
| Advanced features A–L | ✅ | DNA graph, multi-agent room, voice-face sync, frame inspector, heatmap, vault, metadata detective, contradiction detector, confidence breakdown, alternative explanations, case manager, PDF |
| Learned deepfake classifiers | ⚠️ | Not bundled and explicitly marked *unavailable* (never simulated) |
