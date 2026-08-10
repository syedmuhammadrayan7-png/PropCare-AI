"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { ChevronLeft, LogOut, Save, Wrench } from "lucide-react";
import { api, assignMaintenanceRequest } from "@/lib/api";
import { logout, useRoleGuard } from "@/lib/auth";

const teams = ["Awaiting Assignment", "HVAC Maintenance", "Electrical Services", "Plumbing Services", "General Maintenance", "Emergency Response", "Climate Systems"];
const statuses = ["awaiting_assignment", "assigned", "scheduled", "in_progress", "completed", "resolved", "closed", "cancelled"];
const activeStatuses = new Set(["open", "awaiting_assignment", "assigned", "scheduled", "in_progress", "pending", "waiting_for_approval"]);

export default function MaintenancePage() {
  const session = useRoleGuard("admin"), router = useRouter();
  const [requests, setRequests] = useState<any[]>([]), [error, setError] = useState(""), [saving, setSaving] = useState("");
  const load = () => api<any[]>("/api/admin/requests", "admin")
    .then(allRequests => setRequests(allRequests.filter(request => activeStatuses.has(request.status))))
    .catch(e => setError(e instanceof Error ? e.message : "Maintenance requests could not be loaded."));
  useEffect(() => { if (session) load(); }, [session]);
  async function update(request: any) {
    setSaving(request.request_id); setError("");
    try { await assignMaintenanceRequest(request.request_id, request.assigned_team, request.status); await load(); }
    catch (e) { setError(e instanceof Error ? e.message : "Assignment could not be saved."); }
    finally { setSaving(""); }
  }
  function change(id: string, field: string, value: string) { setRequests(items => items.map(item => item.request_id === id ? { ...item, [field]: value } : item)); }
  if (!session) return <main className="app-shell grid min-h-screen place-items-center text-sm text-white">Loading maintenance...</main>;
  return <main className="min-h-screen bg-[#f2f6f9] px-5 py-8"><div className="mx-auto max-w-5xl">
    <div className="flex items-center justify-between"><Link href="/admin" className="flex items-center gap-1 text-xs font-bold text-[#486d7b]"><ChevronLeft size={15}/>Operations overview</Link><button onClick={() => { logout("admin"); router.push("/login/admin"); }} className="flex items-center gap-1 text-xs font-bold text-[#486d7b]"><LogOut size={14}/>Logout</button></div>
    <p className="eyebrow mt-8 !text-[#397d78]">Maintenance operations</p><h1 className="display mt-2 text-4xl font-extrabold text-[#132d3a]">Assign and schedule work</h1><p className="mt-2 text-sm font-medium text-[#58717f]">Demo teams only. Saved updates are immediately visible in the tenant&apos;s active requests.</p>
    {error && <p className="mt-5 rounded-xl bg-[#fff0ef] p-3 text-sm text-[#ad4741]">{error}</p>}
    <div className="mt-7 space-y-4">{requests.map(request => <article key={request.request_id} className="light-card rounded-2xl p-5"><div className="flex flex-wrap justify-between gap-3"><div><p className="eyebrow !text-[#66818e]">{request.request_id} · {request.tenant || request.tenant_id} · {request.property || "Property"} · {request.unit_id}</p><h2 className="mt-1 flex items-center gap-2 font-extrabold text-[#15303d]"><Wrench size={16}/>{request.category}</h2><p className="mt-2 text-sm text-[#58717f]">{request.description}</p></div><span className="status status-progress">{request.priority}</span></div><div className="mt-5 grid gap-3 sm:grid-cols-[1fr_1fr_auto]"><select value={request.assigned_team || "Awaiting Assignment"} onChange={e => change(request.request_id, "assigned_team", e.target.value)} className="rounded-lg border border-[#d5e3e9] bg-white px-3 py-2 text-sm">{teams.map(team => <option key={team}>{team}</option>)}</select><select value={request.status} onChange={e => change(request.request_id, "status", e.target.value)} className="rounded-lg border border-[#d5e3e9] bg-white px-3 py-2 text-sm">{statuses.map(status => <option key={status} value={status}>{status.replaceAll("_", " ")}</option>)}</select><button disabled={saving === request.request_id} onClick={() => update(request)} className="flex items-center justify-center gap-1 rounded-lg bg-[#10364b] px-4 py-2 text-xs font-extrabold text-white disabled:opacity-50"><Save size={14}/>Save</button></div></article>)}{!requests.length && !error && <p className="rounded-2xl border border-dashed border-[#cfdde4] p-10 text-center text-sm text-[#617b89]">No active maintenance requests found.</p>}</div>
  </div></main>;
}
