import { useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../lib/auth";

export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-bold tracking-tight ${className}`}>
      <svg viewBox="0 0 32 32" className="h-7 w-7" aria-hidden="true"><circle cx="14" cy="14" r="9" fill="none" stroke="var(--color-cyan)" strokeWidth="2.5" /><circle cx="14" cy="14" r="3" fill="var(--color-gold)" /><path d="M21 21l7 7" stroke="var(--color-gold)" strokeWidth="2.5" strokeLinecap="round" /></svg>
      <span>TruthLens <span className="text-cyan">AI</span></span>
    </span>
  );
}

const NAV = [
  { to: "/", label: "Start page", icon: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z", end: true },
  { to: "/app", label: "Dashboard", icon: "M3 13h8V3H3zm10 8h8V11h-8zM3 21h8v-6H3zm10-18v6h8V3z", end: true },
  { to: "/app/cases/new", label: "New investigation", icon: "M12 5v14M5 12h14" },
  { to: "/app/history", label: "Investigation history", icon: "M12 8v4l3 2M3 12a9 9 0 1 0 3-6.7L3 8M3 3v5h5" },
];

export function AppShell() {
  const { user, logout } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  return (
    <div className="min-h-screen lg:grid lg:grid-cols-[250px_1fr]">
      <a href="#main" className="skip-link">Skip to main content</a>
      <header className="lg:hidden flex items-center justify-between px-4 py-3 border-b border-line bg-navy">
        <Link to="/" aria-label="TruthLens AI start page"><Logo /></Link>
        <button className="btn btn-ghost" aria-expanded={open} aria-controls="sidebar" onClick={() => setOpen((o) => !o)}>{open ? "Close" : "Menu"}</button>
      </header>
      <aside id="sidebar" className={`${open ? "block" : "hidden"} lg:block border-r border-line bg-navy lg:min-h-screen`}>
        <div className="p-5 hidden lg:block"><Link to="/" aria-label="TruthLens AI start page" title="Start page"><Logo /></Link></div>
        <nav aria-label="Primary" className="px-3 pb-4">
          <ul className="grid gap-1">
            {NAV.map((n) => (
              <li key={n.to}>
                <NavLink to={n.to} end={n.end} onClick={() => setOpen(false)}
                  className={({ isActive }) => `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium ${isActive ? "bg-panel2 text-cyan" : "text-muted hover:text-text hover:bg-panel"}`}>
                  <svg viewBox="0 0 24 24" className="h-4 w-4" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><path d={n.icon} /></svg>
                  {n.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="mx-3 p-3 card text-xs">
          <p className="font-semibold truncate">{user?.full_name}</p>
          <p className="text-muted truncate">{user?.email}</p>
          <p className="mt-1"><span className="chip text-gold">{user?.role}</span></p>
          <button className="btn btn-ghost w-full mt-3" onClick={() => { logout(); nav("/"); }}>Sign out</button>
        </div>
      </aside>
      <main id="main" tabIndex={-1} className="p-4 sm:p-6 lg:p-8 max-w-[1400px] w-full min-w-0">
        <Outlet />
      </main>
    </div>
  );
}
