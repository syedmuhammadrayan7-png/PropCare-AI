"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Check, ChevronLeft, CircleAlert, LogOut, Pencil, X } from "lucide-react";
import { approvalHistory, resumeStage2Approval, stage2Approvals } from "@/lib/api";
import { logout, useRoleGuard } from "@/lib/auth";
import { formatCurrency } from "@/lib/currency";

type Approval = {
  thread_id: string; tenant_id: string; tenant: string; unit?: string; property?: string;
  issue: string; evidence?: string; recommended_action?: string; proposed_credit?: number;
  approved_credit?: number; maintenance_request_id?: string; approval_id?: string; status?: string;
};

export default function ApprovalsPage() {
  const session = useRoleGuard("admin"), router = useRouter();
  const [items, setItems] = useState<Approval[]>([]), [history, setHistory] = useState<Approval[]>([]), [error, setError] = useState(""), [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(""), [credits, setCredits] = useState<Record<string, string>>({});
  const load = async () => {
    const [pending, completed] = await Promise.all([stage2Approvals(), approvalHistory()]);
    setItems(pending); setHistory(completed);
  };
  useEffect(() => { if (session) load().catch(e => setError(e instanceof Error ? e.message : "Approvals could not be loaded.")); }, [session]);
  async function resume(item: Approval, decision: "approve" | "reject" | "edit") {
    const typedCredit = credits[item.thread_id] ?? String(item.proposed_credit ?? 5000);
    setBusy(item.thread_id); setError(""); setSuccess("");
    try {
      const editedAmount = Number(typedCredit);
      if (decision === "edit" && (!Number.isFinite(editedAmount) || editedAmount <= 0)) {
        throw new Error("Enter a valid credit amount greater than zero before editing approval.");
      }
      const result: any = await resumeStage2Approval(item.thread_id, decision, decision === "edit" ? editedAmount : undefined);
      const amount = result.approval?.approved_credit;
      setSuccess(decision === "reject" ? `Service credit rejected for ${item.tenant || "the tenant"}.` : `PKR ${(amount ?? editedAmount).toLocaleString()} service credit approved for ${item.tenant || "the tenant"}.`);
      await load();
    } catch (e) { setError(e instanceof Error ? e.message : "Approval could not be completed."); }
    finally { setBusy(""); }
  }
  if (!session) return <main className="app-shell grid min-h-screen place-items-center text-sm text-white">Loading approvals...</main>;
  return <main className="min-h-screen bg-[#f2f6f9] px-5 py-8"><div className="mx-auto max-w-4xl">
    <div className="flex items-center justify-between"><Link href="/admin" className="flex items-center gap-1 text-xs font-bold text-[#486d7b]"><ChevronLeft size={15}/>Operations overview</Link><button onClick={() => { logout("admin"); router.push("/login/admin"); }} className="flex items-center gap-1 text-xs font-bold text-[#486d7b]"><LogOut size={14}/>Logout</button></div>
    <p className="eyebrow mt-8 !text-[#5c6fe2]">Financial approval workflow</p><h1 className="display mt-2 text-4xl font-extrabold text-[#132d3a]">Approval queue</h1><p className="mt-2 text-sm font-medium text-[#58717f]">Financial service credits require an explicit admin decision before the tenant workflow resumes.</p>
    {success && <p className="mt-5 flex gap-2 rounded-xl border border-[#bde4d8] bg-[#effcf6] p-3 text-sm font-bold text-[#167261]"><Check size={16}/>{success}</p>}
    {error && <p className="mt-5 flex gap-2 rounded-xl bg-[#fff0ef] p-3 text-sm text-[#ad4741]"><CircleAlert size={16}/>{error}</p>}
    <div className="mt-7 space-y-4">{items.map(item => <article key={item.thread_id} className="light-card rounded-2xl p-6"><div className="flex flex-wrap justify-between gap-4"><div><p className="eyebrow !text-[#6e7890]">{item.tenant} · {item.property || "Property"} · Unit {item.unit || "—"}</p><h2 className="mt-1 text-lg font-extrabold text-[#15303d]">Recurring service-credit review</h2><p className="mt-3 max-w-2xl text-sm font-medium leading-6 text-[#526f7b]">{item.issue}</p></div><span className="status status-open">Approval required</span></div><div className="mt-5 grid gap-3 rounded-xl bg-[#f5f8fa] p-4 text-sm sm:grid-cols-3"><p><b>Evidence</b><br/><span className="text-[#58717f]">{item.evidence || "Specialist review"}</span></p><p><b>Recommended action</b><br/><span className="text-[#58717f]">{item.recommended_action || "Review"}</span></p><p><b>Proposed credit</b><br/><span className="font-extrabold text-[#167261]">{formatCurrency(item.proposed_credit || 0)}</span></p></div><div className="mt-5 flex flex-wrap items-center gap-3"><label className="text-xs font-bold text-[#355b68]">Credit amount<input type="number" min="1" step="1" value={credits[item.thread_id] ?? String(item.proposed_credit ?? 5000)} onChange={e => setCredits(current => ({ ...current, [item.thread_id]: e.target.value }))} className="ml-2 w-24 rounded-lg border border-[#d5e3e9] px-2 py-1.5"/></label><button disabled={busy === item.thread_id} onClick={() => resume(item, "approve")} className="flex items-center gap-1 rounded-lg bg-[#168b77] px-3 py-2 text-xs font-extrabold text-white"><Check size={14}/>Approve</button><button disabled={busy === item.thread_id} onClick={() => resume(item, "edit")} className="flex items-center gap-1 rounded-lg border border-[#6b67cf] px-3 py-2 text-xs font-extrabold text-[#5850b9]"><Pencil size={14}/>Edit & approve</button><button disabled={busy === item.thread_id} onClick={() => resume(item, "reject")} className="flex items-center gap-1 rounded-lg border border-[#e8c3c0] px-3 py-2 text-xs font-extrabold text-[#a3433c]"><X size={14}/>Reject</button></div></article>)}{!items.length && !error && <div className="rounded-2xl border border-dashed border-[#cfdde4] p-10 text-center text-sm font-medium text-[#617b89]">No financial approvals are waiting.</div>}</div>
    {history.length > 0 && <section className="mt-8"><p className="eyebrow !text-[#397d78]">Recent decisions</p><div className="mt-3 space-y-3">{history.map(item => <article key={item.thread_id} className="rounded-xl border border-[#dce8ed] bg-white p-4 text-sm"><p className="font-extrabold text-[#17313d]">{item.tenant} · {item.status === "rejected" ? "Service credit rejected" : "Service credit approved"}</p><p className="mt-1 text-xs text-[#58717f]">Proposed: {formatCurrency(item.proposed_credit || 0)}{item.status !== "rejected" && <> · Approved: {formatCurrency(item.approved_credit || 0)}</>}</p></article>)}</div></section>}
  </div></main>;
}
