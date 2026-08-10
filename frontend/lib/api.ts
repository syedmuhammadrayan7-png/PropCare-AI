import { getSession, type DemoSession, type Role } from "@/lib/auth";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";
async function request<T>(path: string, init: RequestInit = {}, role?: Role): Promise<T> {
  const session = role ? getSession(role) : null;
  const response = await fetch(`${BASE}${path}`, { cache: "no-store", ...init, headers: { "Content-Type": "application/json", "Cache-Control": "no-cache, no-store, max-age=0", ...(session?.token ? { Authorization: `Bearer ${session.token}` } : {}), ...init.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.detail || "Unable to reach PropCare.");
  return data;
}
export const api = <T,>(path: string, role?: Role) => {
  const resolvedRole = role || (typeof window !== "undefined" && window.location.pathname.startsWith("/admin") ? "admin" : "tenant");
  return request<T>(path, {}, resolvedRole);
};
export const sendSupportMessage = (message: string) => request("/api/support/message", { method: "POST", body: JSON.stringify({ message }) }, "tenant");
export const sendStage2SupportMessage = (message: string, threadId?: string) => request<{ thread_id: string; status: string; resolution?: any; approval?: { proposed_credit?: number } }>("/api/stage2/support", { method: "POST", body: JSON.stringify({ message, thread_id: threadId }) }, "tenant");
export const stage2Thread = (threadId: string) => request<{ thread_id: string; status: string; resolution?: any }>(`/api/stage2/threads/${threadId}`, {}, "tenant");
export const sendStage3SupportMessage = (message: string, threadId?: string) => request<{ thread_id: string; status: string; resolution?: any; approval?: { proposed_credit?: number } }>("/api/stage3/support", { method: "POST", body: JSON.stringify({ message, thread_id: threadId }) }, "tenant");
export const stage3Thread = (threadId: string) => request<{ thread_id: string; status: string; resolution?: any }>(`/api/stage3/threads/${threadId}`, {}, "tenant");
export const tenantFinancialActivity = (tenantId: string) => request<any[]>(`/api/tenants/${tenantId}/financial-activity`, {}, "tenant");
export const login = (email: string, password: string, role: Role) => request<DemoSession>("/api/auth/login", { method: "POST", body: JSON.stringify({ email, password, role }) });
export const demoCredentials = (role: Role) => request<{ email: string; password: string }>(`/api/auth/demo-credentials?role=${role}`);
export const stage2Approvals = () => request<Array<{ thread_id: string; tenant_id: string; tenant: string; unit?: string; property?: string; issue: string; evidence?: string; recommended_action?: string; proposed_credit?: number; maintenance_request_id?: string }>>("/api/admin/stage2/approvals", {}, "admin");
export const resumeStage2Approval = (threadId: string, decision: "approve" | "reject" | "edit", approved_credit?: number) => request(`/api/admin/stage2/approvals/${threadId}/resume`, { method: "POST", body: JSON.stringify({ decision, approved_credit }) }, "admin");
export const approvalHistory = () => request<any[]>("/api/admin/approvals/history", {}, "admin");
export const assignMaintenanceRequest = (requestId: string, assignedTeam: string, status: string) => request(`/api/admin/requests/${requestId}/assignment`, { method: "POST", body: JSON.stringify({ assigned_team: assignedTeam, status }) }, "admin");
