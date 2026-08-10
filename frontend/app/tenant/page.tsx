"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Building2, CheckCircle2, CircleAlert, CreditCard, Droplets, Lightbulb, LogOut, Send, Wrench } from "lucide-react";
import { api, sendStage3SupportMessage, stage3Thread, tenantFinancialActivity } from "@/lib/api";
import { logout, useRoleGuard } from "@/lib/auth";
import { formatCurrency } from "@/lib/currency";

const categories = [["Maintenance", Wrench], ["Plumbing", Droplets], ["Electrical", Lightbulb], ["Billing", CreditCard], ["General", Building2]];
const displayStatus = (value?: string | null) => value ? value.replaceAll("_", " ") : "Not available";
const displayTeam = (team?: string | null) => !team || team.toLowerCase() === "awaiting assignment" ? "Unassigned" : team;
const approvalLabel = (status?: string) => status === "pending" ? "Waiting for admin approval" : status === "approved" || status === "edited_approved" ? "Service credit approved" : "Service credit rejected";
const canonicalStatus = (value?: string) => (value || "").trim().toLowerCase().replaceAll("-", "_").replaceAll(" ", "_");
const activeStatuses = new Set(["open", "awaiting_assignment", "assigned", "scheduled", "in_progress", "pending", "waiting_for_approval"]);

export default function TenantPage() {
  const session = useRoleGuard("tenant"), router = useRouter();
  const [data, setData] = useState<any>(), [category, setCategory] = useState("Maintenance"), [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false), [error, setError] = useState(""), [resolution, setResolution] = useState<any>();
  const [workflowThread, setWorkflowThread] = useState<string>();
  const [workflowNotice, setWorkflowNotice] = useState("");
  const requestFetchVersion = useRef(0);

  const refreshRequests = useCallback(async () => {
    if (!session?.tenant_id) return;
    const version = ++requestFetchVersion.current;
    const requests = await api<any[]>(`/api/tenants/${session.tenant_id}/requests`);
    if (version === requestFetchVersion.current) setData((current: any) => current ? { ...current, requests: [...requests] } : current);
  }, [session?.tenant_id]);

  const refreshFinancialActivity = useCallback(async () => {
    if (!session?.tenant_id) return;
    const financialActivity = await tenantFinancialActivity(session.tenant_id);
    setData((current: any) => current ? { ...current, financialActivity } : current);
  }, [session?.tenant_id]);

  useEffect(() => {
    if (!session?.tenant_id) return;
    const initialRequestVersion = ++requestFetchVersion.current;
    Promise.all([
      api<any>(`/api/tenants/${session.tenant_id}`),
      api<any>(`/api/tenants/${session.tenant_id}/unit`),
      api<any[]>(`/api/tenants/${session.tenant_id}/requests`),
      api<any[]>(`/api/tenants/${session.tenant_id}/payments`),
      tenantFinancialActivity(session.tenant_id),
    ]).then(([tenant, unit, requests, payments, financialActivity]) => setData((current: any) => ({
      tenant, unit, payment: payments[0], financialActivity,
      requests: initialRequestVersion === requestFetchVersion.current ? [...requests] : (current?.requests || []),
    })))
      .catch(e => setError(e instanceof Error ? e.message : "Resident data could not be loaded."));
  }, [session?.tenant_id]);

  useEffect(() => {
    if (!session?.tenant_id) return;
    const timer = window.setInterval(() => { Promise.all([refreshRequests(), refreshFinancialActivity()]).catch(() => undefined); }, 5000);
    return () => window.clearInterval(timer);
  }, [refreshRequests, refreshFinancialActivity, session?.tenant_id]);

  useEffect(() => {
    if (!workflowThread || !session?.tenant_id) return;
    const timer = window.setInterval(async () => {
      try {
        const workflow = await stage3Thread(workflowThread);
        if (workflow.status === "completed" && workflow.resolution) {
          setResolution(workflow.resolution);
          setWorkflowThread(undefined);
          setWorkflowNotice("");
          setMessage("");
          await Promise.all([refreshRequests(), refreshFinancialActivity()]);
        } else if (workflow.status === "waiting_for_approval") {
          setWorkflowNotice("Waiting for admin approval");
          if (workflow.resolution) setResolution(workflow.resolution);
        } else {
          setWorkflowNotice("Investigating your request...");
        }
      } catch (e) {
        setWorkflowThread(undefined);
        setWorkflowNotice("");
        setError(e instanceof Error ? e.message : "We could not retrieve your workflow update.");
      }
    }, 3000);
    return () => window.clearInterval(timer);
  }, [workflowThread, session?.tenant_id, refreshRequests, refreshFinancialActivity]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!message.trim() || workflowThread) return;
    setLoading(true); setError(""); setResolution(undefined); setWorkflowNotice("Investigating your request...");
    try {
      const result = await sendStage3SupportMessage(`${category}: ${message}`);
      if (result.resolution) setResolution(result.resolution);
      await Promise.all([refreshRequests(), refreshFinancialActivity()]); // Both panels read persisted backend state.
      if (result.status === "waiting_for_approval") {
        setWorkflowThread(result.thread_id);
        setWorkflowNotice("Waiting for admin approval");
      } else if (result.status === "completed" && result.resolution) {
        setWorkflowNotice("");
        setMessage("");
      } else {
        setWorkflowThread(result.thread_id);
      }
    } catch (e) {
      setWorkflowThread(undefined);
      setWorkflowNotice("");
      setError(e instanceof Error ? e.message : "Your request could not be submitted.");
    } finally { setLoading(false); }
  }

  if (!session || !data) return <main className="app-shell grid min-h-screen place-items-center"><p className="text-sm text-[#d3e1e8]">Loading your resident workspace...</p></main>;
  const { tenant, unit, requests, payment, financialActivity = [] } = data;
  const activeRequests = requests.filter((request: any) => activeStatuses.has(canonicalStatus(request.status)));
  const creditStatus = resolution?.approval_status || resolution?.approval_decision;
  const pendingApproval = Boolean(resolution?.approval_required && creditStatus === "pending");
  const specialists: string[] = resolution?.specialists_used || [];
  const billingOnly = specialists.length === 1 && specialists[0] === "billing" && !resolution?.request_id;
  const hasMaintenance = !billingOnly && Boolean(resolution?.request_id || resolution?.maintenance_status);
  const isGeneralOrMulti = specialists.includes("resident_services") || specialists.length > 1;

  return <main className="min-h-screen bg-[#f1f6f8] text-[#132e3b]">
    <header className="border-b border-[#dbe6eb] bg-white/90 px-5 py-4"><div className="mx-auto flex max-w-7xl items-center justify-between"><Link href="/" className="flex items-center gap-3 font-extrabold"><span className="grid h-9 w-9 place-items-center rounded-xl bg-[#10364b] text-emerald"><Building2 size={18}/></span>PropCare AI</Link><div className="flex items-center gap-3"><span className="hidden text-xs font-bold text-[#45606e] sm:block">{unit.property_name} · {unit.location}</span><button onClick={() => { logout(); router.push("/login/tenant"); }} className="flex items-center gap-1 rounded-lg border border-[#d4e1e7] px-3 py-2 text-xs font-bold text-[#355a68]"><LogOut size={14}/>Logout</button></div></div></header>
    <div className="mx-auto max-w-7xl px-5 py-9"><p className="eyebrow !text-[#2d8177]">Resident workspace · Pakistan demo</p><h1 className="display mt-2 text-4xl font-extrabold">Welcome back, {tenant.name.split(" ")[0]}.</h1><p className="mt-2 text-sm font-medium text-[#52707c]">{unit.property_name} · {unit.location}</p>
      <section className="mt-7 grid gap-4 md:grid-cols-3"><article className="rounded-2xl bg-gradient-to-br from-[#0d3046] to-[#174e61] p-6 text-white"><p className="eyebrow">Your residence</p><h2 className="mt-7 text-xl font-extrabold">{unit.property_name}</h2><p className="mt-2 text-sm font-medium text-[#d0e2e7]">{unit.building} · Floor {unit.floor} · Apartment {unit.unit_number}</p><p className="mt-1 text-xs font-medium text-[#acd0d7]">{unit.location}</p></article><article className="light-card rounded-2xl p-6"><div className="flex justify-between"><p className="eyebrow !text-[#557782]">August rent</p><CreditCard className="text-[#168f80]" size={19}/></div><p className="mt-7 text-2xl font-extrabold">{formatCurrency(payment.amount)}</p><p className="mt-2 text-sm font-bold text-[#247968]">{payment.payment_status === "paid" ? "Paid · received" : "Due"}</p><p className="mt-3 text-xs font-medium text-[#58727e]">Due {payment.due_date}</p></article><article className="light-card rounded-2xl p-6"><div className="flex justify-between"><p className="eyebrow !text-[#557782]">Active requests</p><Wrench className="text-[#1383a3]" size={19}/></div><p className="mt-7 text-2xl font-extrabold">{activeRequests.length}</p><p className="mt-2 text-sm font-medium text-[#52707c]">Current work and service history.</p></article></section>
      <section className="mt-6 grid gap-6 xl:grid-cols-[.95fr_1.05fr]"><form onSubmit={submit} className="rounded-3xl bg-[#0b1d31] p-6 text-white shadow-xl"><p className="eyebrow">Resident support</p><h2 className="display mt-2 text-2xl font-extrabold">Report an issue</h2><p className="mt-2 text-sm leading-6 text-[#c4d5dd]">We&apos;ll review your property records and prepare the right resolution.</p><div className="mt-5 flex flex-wrap gap-2">{categories.map(([name, Icon]: any) => <button disabled={loading || Boolean(workflowThread)} type="button" onClick={() => setCategory(name)} key={name} className={`flex items-center gap-1 rounded-lg border px-3 py-2 text-xs font-bold ${category === name ? "border-emerald bg-emerald/15 text-emerald" : "border-white/15 text-[#d1dce2]"}`}><Icon size={13}/>{name}</button>)}</div><textarea disabled={loading || Boolean(workflowThread)} value={message} onChange={e => setMessage(e.target.value)} placeholder={`Describe your ${category.toLowerCase()} request...`} className="mt-4 h-32 w-full rounded-xl border border-white/15 bg-white/5 p-4 text-sm text-white outline-none placeholder:text-[#acc0ca] focus:border-cyan"/><div className="mt-4 flex justify-end"><button disabled={loading || Boolean(workflowThread) || !message.trim()} className="gradient-button flex items-center gap-2 rounded-xl px-4 py-3 text-sm font-extrabold text-[#09111f] disabled:opacity-50"><Send size={16}/>{loading ? "Preparing resolution..." : "Submit request"}</button></div>{loading && <p className="mt-4 rounded-xl bg-white/5 p-3 text-xs font-medium text-[#d1e0e7]">Reviewing resident profile · Checking property records · Preparing resolution</p>}</form>
        <section className="light-card overflow-hidden rounded-3xl"><div className="border-b border-[#e3ebef] p-6"><p className="eyebrow !text-[#397d78]">Current service</p><h2 className="mt-1 text-lg font-extrabold">Recent requests</h2></div><div className="divide-y divide-[#e7eef1]">{activeRequests.map((r: any) => <article key={r.request_id} className="p-5"><div className="flex justify-between gap-3"><h3 className="font-extrabold">{r.category}</h3><span className="status status-progress">{displayStatus(canonicalStatus(r.status))}</span></div><p className="mt-2 text-sm font-medium leading-6 text-[#526e79]">{r.description}</p><p className="mt-3 text-xs font-bold text-[#527b87]">{r.request_id} · {displayTeam(r.assigned_team)}</p></article>)}</div></section></section>
      {financialActivity.length > 0 && <section className="light-card mt-6 rounded-3xl p-6"><p className="eyebrow !text-[#5c6fe2]">Financial activity</p><h2 className="mt-1 text-lg font-extrabold">Service-credit decisions</h2><div className="mt-4 grid gap-3 md:grid-cols-2">{financialActivity.map((item: any) => <article key={item.approval_id} className="rounded-xl border border-[#dce8ed] bg-white p-4"><p className="text-xs font-extrabold uppercase tracking-wide text-[#557782]">Service credit</p><p className="mt-1 font-extrabold text-[#17313d]">{approvalLabel(item.status)}</p><p className="mt-2 text-xs font-medium text-[#526e79]">Proposed: {formatCurrency(item.proposed_credit || 0)}</p>{["approved", "edited_approved"].includes(item.status) ? <p className="mt-1 text-xs font-bold text-[#167261]">Approved: {formatCurrency(item.approved_credit || 0)}</p> : item.status === "rejected" ? <p className="mt-1 text-xs font-bold text-[#a3433c]">No credit issued</p> : null}</article>)}</div></section>}
      {workflowNotice && <p className="mt-5 rounded-xl border border-[#c3dfe3] bg-[#eef8f8] p-4 text-sm font-bold text-[#1e6f71]">{workflowNotice}</p>}
      {error && <p className="mt-5 flex gap-2 rounded-xl border border-[#f0c5c1] bg-[#fff4f3] p-4 text-sm font-medium text-[#ad4741]"><CircleAlert size={17}/>{error}</p>}
      {resolution && <section className="mt-6 rounded-3xl border border-[#bde4d8] bg-[#effcf6] p-6"><div className="flex items-center gap-2 text-sm font-extrabold text-[#167261]">{pendingApproval ? <CircleAlert size={18}/> : <CheckCircle2 size={18}/>}{pendingApproval ? "Waiting for admin approval" : "Resolution ready"}</div><h2 className="display mt-3 text-2xl font-extrabold text-[#163b37]">{resolution.summary}</h2><p className="mt-2 text-sm font-medium text-[#496d64]">{resolution.action_taken}</p>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {billingOnly && <><p className="rounded-xl bg-white p-3 text-xs"><b>Team</b><br/>Billing</p><p className="rounded-xl bg-white p-3 text-xs"><b>Payment status</b><br/>{displayStatus(resolution.payment_status)}</p><p className="rounded-xl bg-white p-3 text-xs"><b>Billing summary</b><br/>{resolution.action_taken}</p></>}
          {hasMaintenance && <><p className="rounded-xl bg-white p-3 text-xs"><b>Team</b><br/>{displayTeam(resolution.assigned_team)}</p><p className="rounded-xl bg-white p-3 text-xs"><b>Request ID</b><br/>{resolution.request_id}</p><p className="rounded-xl bg-white p-3 text-xs"><b>Maintenance status</b><br/>{displayStatus(resolution.maintenance_status)}</p><p className="rounded-xl bg-white p-3 text-xs"><b>Priority</b><br/>{resolution.priority}</p></>}
          {isGeneralOrMulti && <p className="rounded-xl bg-white p-3 text-xs"><b>Specialists used</b><br/>{specialists.map(name => name.replaceAll("_", " ")).join(", ")}</p>}
        </div>
        {creditStatus && <div className="mt-3 rounded-xl border border-[#cfe7dc] bg-white p-4 text-sm"><p className="text-xs font-extrabold uppercase tracking-wide text-[#397d78]">Service credit</p><p className="mt-1 font-extrabold text-[#163b37]">{creditStatus === "pending" ? "Waiting for admin approval" : creditStatus === "approved" ? "Approved" : creditStatus === "edited_approved" ? "Approved (edited amount)" : "Rejected"}</p><p className="mt-2 text-xs text-[#496d64]">Proposed: {formatCurrency(resolution.proposed_credit || 0)}{["approved", "edited_approved"].includes(creditStatus) && <><br/>Approved: {formatCurrency(resolution.approved_credit || 0)}</>}</p></div>}
        {isGeneralOrMulti && resolution.related_open_requests?.length > 0 && <div className="mt-3 rounded-xl border border-[#cfe7dc] bg-white p-4 text-sm"><p className="text-xs font-extrabold uppercase tracking-wide text-[#397d78]">Open maintenance context</p>{resolution.related_open_requests.map((request: any) => <p key={request.request_id} className="mt-2 text-xs font-medium text-[#496d64]">{request.request_id} · {request.category} · {displayStatus(request.status)} · {displayTeam(request.assigned_team)}</p>)}</div>}
      </section>}
    </div>
  </main>;
}
