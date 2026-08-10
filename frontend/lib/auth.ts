"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

export type Role = "tenant" | "admin";
export type DemoSession = { token: string; role: Role; name: string; tenant_id: string | null };
const SESSION_KEYS: Record<Role, string> = { tenant: "propcare_tenant_session", admin: "propcare_admin_session" };

export function getSession(role: Role): DemoSession | null {
  if (typeof window === "undefined") return null;
  try { return JSON.parse(sessionStorage.getItem(SESSION_KEYS[role]) || "null"); } catch { return null; }
}
export function saveSession(session: DemoSession) { sessionStorage.setItem(SESSION_KEYS[session.role], JSON.stringify(session)); }
export function logout(role?: Role) {
  const resolvedRole = role || (window.location.pathname.startsWith("/admin") ? "admin" : "tenant");
  sessionStorage.removeItem(SESSION_KEYS[resolvedRole]);
}

export function useRoleGuard(role: Role) {
  const router = useRouter(); const [session, setSession] = useState<DemoSession | null>(null);
  useEffect(() => { const current = getSession(role); if (!current || current.role !== role) { router.replace(`/login/${role}`); return; } setSession(current); }, [role, router]);
  return session;
}
