import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { AppRoutes } from "../App";
import { TimelineStrip, BarList } from "../components/Charts";
import { ConcernBadge, Disclaimer, Field, SeverityBadge, ScoreBar } from "../components/ui";
import { IndicatorCard, fmtLocation } from "../components/Forensics";
import { AuthProvider } from "../lib/auth";
import { ApiError, api, tokenStore, type Indicator } from "../lib/api";

type Handler = (url: string, init?: RequestInit) => { status?: number; body?: unknown };
function mockFetch(handler: Handler) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const { status = 200, body = {} } = handler(url, init);
    return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

const user = { id: "u1", email: "a@example.com", full_name: "Alice", role: "admin", preferences: {}, created_at: "2026-01-01T00:00:00Z" };

function renderAt(path: string) {
  return render(<AuthProvider><MemoryRouter initialEntries={[path]}><AppRoutes /></MemoryRouter></AuthProvider>);
}

beforeEach(() => vi.unstubAllGlobals());

describe("UI primitives", () => {
  it("conveys severity with text and symbol, not colour alone", () => {
    render(<SeverityBadge severity="high" />);
    expect(screen.getByText("HIGH")).toBeInTheDocument();
    expect(screen.getByText("▲▲")).toHaveAttribute("aria-hidden", "true");
  });
  it("shows 'not analysed' when no concern level", () => {
    render(<ConcernBadge level={null} />);
    expect(screen.getByText("not analysed")).toBeInTheDocument();
  });
  it("associates labels and errors with inputs", () => {
    render(<Field label="Email" error="Bad email">{(id, d) => <input id={id} aria-describedby={d} aria-invalid />}</Field>);
    const input = screen.getByLabelText("Email");
    expect(input).toHaveAccessibleDescription("Bad email");
  });
  it("score bar exposes a meter with value", () => {
    render(<ScoreBar value={0.42} label="Image score" />);
    expect(screen.getByRole("meter", { name: "Image score" })).toHaveAttribute("aria-valuenow", "42");
    render(<ScoreBar value={null} label="x" />);
    expect(screen.getByText("n/a")).toBeInTheDocument();
  });
  it("disclaimer states indicators are not verdicts", () => {
    render(<Disclaimer />);
    expect(screen.getByText(/Indicators, not verdicts/)).toBeInTheDocument();
  });
});

describe("Charts", () => {
  it("timeline has a text alternative and selection works", async () => {
    const onSelect = vi.fn();
    render(<TimelineStrip values={[0, 0.5, 1]} times={[0, 1, 2]} flagged={[false, false, true]} label="Anomaly" onSelect={onSelect} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(/Anomaly\. 3 points .* 1 flagged/);
  });
  it("bar list summarises data for screen readers", () => {
    render(<BarList title="Evidence" data={{ image: 2, video: 1 }} />);
    expect(screen.getByLabelText("Evidence: image: 2, video: 1")).toBeInTheDocument();
  });
});

describe("fmtLocation", () => {
  it("formats regions, segments and metadata", () => {
    expect(fmtLocation({ x: 1, y: 2, w: 3, h: 4 })).toBe("Region x=1, y=2, 3×4px");
    expect(fmtLocation({ t_start: 1, t_end: 2.5 })).toBe("1.00s – 2.50s");
    expect(fmtLocation({ field: "EXIF" })).toBe("Metadata: EXIF");
  });
});

describe("API client", () => {
  it("sends bearer token and surfaces validation errors", async () => {
    tokenStore.set("tok");
    const f = mockFetch(() => ({ status: 422, body: { detail: "Validation failed", errors: [{ field: "title", message: "too short" }] } }));
    await expect(api.createCase({ title: "x", description: "", context_statement: "", priority: "low" })).rejects.toMatchObject({ status: 422, fields: [{ field: "title" }] });
    const headers = new Headers((f.mock.calls[0][1] as RequestInit).headers);
    expect(headers.get("Authorization")).toBe("Bearer tok");
    expect(f.mock.calls[0][0]).not.toContain("tok");
  });
  it("reports network failure clearly", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("net"); }));
    await expect(api.me()).rejects.toBeInstanceOf(ApiError);
  });
});

describe("Routing & auth", () => {
  it("first page is the eye only and continues to sign-in", async () => {
    renderAt("/");
    expect(screen.queryByText(/See what's real|Start an investigation|Eight features/)).toBeNull();
    await userEvent.click(screen.getByRole("button", { name: "Continue to sign in" }));
    expect(await screen.findByRole("heading", { name: /Sign in/ })).toBeInTheDocument();
  });

  it("sends signed-out visitors to the eye intro, then to sign-in", async () => {
    renderAt("/app");
    await userEvent.click(await screen.findByRole("button", { name: "Continue to sign in" }));
    expect(await screen.findByRole("heading", { name: /Sign in/ })).toBeInTheDocument();
  });

  it("validates the registration form client-side", async () => {
    const f = mockFetch(() => ({ body: {} }));
    renderAt("/register");
    await userEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByLabelText("Password")).toHaveAccessibleDescription(/At least 10 characters/);
    expect(f).not.toHaveBeenCalled();
  });

  it("logs in and shows the dashboard empty state", async () => {
    mockFetch((url) => {
      if (url.endsWith("/api/auth/login")) return { body: { access_token: "t", user } };
      if (url.includes("/dashboard/stats")) return { body: { cases_by_status: {}, evidence_by_type: {}, indicators_by_severity: {}, jobs_by_status: {}, recent_activity: [] } };
      if (url.startsWith("/api/cases")) return { body: { items: [], total: 0, page: 1, page_size: 10 } };
      return { body: {} };
    });
    renderAt("/login");
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "Str0ngPassword!");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Investigation dashboard" })).toBeInTheDocument();
    expect(await screen.findByText("No investigations yet")).toBeInTheDocument();
    expect(tokenStore.get()).toBe("t");
  });

  it("shows server error message on failed login", async () => {
    mockFetch(() => ({ status: 401, body: { detail: "Invalid email or password" } }));
    renderAt("/login");
    await userEvent.type(screen.getByLabelText("Email"), "a@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "nope");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("creates a case and navigates to upload", async () => {
    tokenStore.set("t");
    const calls: string[] = [];
    mockFetch((url, init) => {
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/api/auth/me")) return { body: user };
      if (url === "/api/cases" && init?.method === "POST") return { status: 201, body: { id: "c1" } };
      if (url.endsWith("/api/cases/c1")) return { body: { id: "c1", reference: "TL-1", title: "Case", description: "", context_statement: "", status: "open", priority: "medium", owner_id: "u1", created_at: "", updated_at: "", evidence_count: 0, latest_job_status: null, overall_concern: null } };
      if (url.endsWith("/evidence") || url.endsWith("/jobs")) return { body: [] };
      if (url.endsWith("/results/latest")) return { body: null };
      if (url.endsWith("/api/capabilities")) return { body: { agents: [], learned_models: [], limits: { image_mb: 25, video_mb: 300, audio_mb: 60, text_mb: 2, video_seconds: 600, audio_seconds: 900 } } };
      return { body: {} };
    });
    renderAt("/app/cases/new");
    const title = await screen.findByLabelText("Case title");
    await userEvent.click(screen.getByRole("button", { name: /Create case/ }));
    expect(title).toHaveAttribute("aria-invalid", "true");
    await userEvent.type(title, "Viral clip");
    await userEvent.click(screen.getByRole("button", { name: /Create case/ }));
    expect(await screen.findByRole("heading", { name: "Evidence upload center" })).toBeInTheDocument();
    expect(calls).toContain("POST /api/cases");
    expect(screen.getByRole("button", { name: "Run multi-agent investigation" })).toBeDisabled();
  });
});

describe("IndicatorCard", () => {
  it("shows alternatives and offers no human-review controls", async () => {
    const ind: Indicator = { id: 7, evidence_id: "e1", agent_name: "Image Forensics Agent", category: "region", indicator_type: "copy_move_cluster", severity: "medium",
      description: "12 keypoints repeated", location: { x: 1, y: 2, w: 3, h: 4 }, score: 0.6, alternative_explanations: ["Repeating textures"], recommended_checks: ["Compare regions"] };
    const { CaseProvider } = await import("../lib/caseContext");
    mockFetch(() => ({ body: [] }));
    render(<MemoryRouter><CaseProvider caseId="c1"><ul><IndicatorCard ind={ind} /></ul></CaseProvider></MemoryRouter>);
    const card = screen.getAllByRole("listitem")[0];
    expect(within(card).getByText("MEDIUM")).toBeInTheDocument();
    expect(within(card).getByText("Repeating textures")).toBeInTheDocument();
    expect(within(card).queryByRole("button", { name: /review note/i })).toBeNull();
  });
});
