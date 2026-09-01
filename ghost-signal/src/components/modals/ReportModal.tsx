import React, { useEffect, useState } from "react";
import { useGhostStore } from "../../lib/store";
import { arenaApi } from "../../lib/arenaApi";
import { augmentComposerStatisticsFromArena } from "../../lib/reportStatsFallback";

interface Props { onClose: () => void; }

/**
 * One line per action the target took, or tried to.
 *
 * Whether the call was permitted matters as much as whether it was made: a target
 * that tried to act outside its authority has already failed, even when the
 * boundary stopped it, so the two read differently here rather than both showing
 * up as "invoked".
 */
function describeAction(a: TargetAction): string {
  const traits = [a.effect, a.risk ? `risk ${a.risk}` : ""].filter(Boolean).join(", ");
  const state =
    a.executed === true
      ? "carried out"
      : a.executed === false
        ? `refused by the boundary${a.rule ? ` (${a.rule})` : ""}`
        : "outcome not reported";
  const args = a.arguments && Object.keys(a.arguments).length
    ? ` ${JSON.stringify(a.arguments)}`
    : "";
  return `${a.name ?? "?"}${traits ? ` [${traits}]` : ""} — ${state}${args}`;
}

interface ReportRound {
  round: number;
  attack?: { payload?: string | null; type?: string | null; confidence?: number | null };
  defense?: { decision?: string | null; confidence?: number | null; reason?: string | null };
  final_prompt?: string | null;
  raw_target_response?: string | null;
  target_delivered?: string | null;
  target_response?: string | null;
  output_gate?: { was_modified?: boolean; modification_reason?: string | null };
  judge_reasoning?: string | null;
  harmfulness_score?: number | null;
  red_success?: boolean | null;
  // What the target was persuaded to DO this round, if the engagement gave it
  // actions. Absent on a conversational round, and on a report from a build that
  // predates the column.
  target_tool_calls?: TargetAction[] | null;
}

interface TargetAction {
  name?: string | null;
  arguments?: Record<string, unknown> | null;
  executed?: boolean | null;
  verdict?: string | null;
  effect?: string | null;
  risk?: string | null;
  rule?: string | null;
  result?: string | null;
}

interface ReportPayload {
  session_id?: string;
  mode?: string | null;
  status?: string | null;
  red_service_id?: string | null;
  blue_service_id?: string | null;
  statistics: {
    total_rounds: number;
    red_wins: number;
    blue_wins: number;
    attack_success_rate: number;
    defense_rate: number;
    avg_harmfulness_score: number;
    max_harmfulness_score?: number;
  };
  rounds?: ReportRound[];
}

function PreBlock({ label, text }: { label: string; text: string }) {
  const empty = !text?.trim();
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ fontSize: 9, color: "#8b949e", marginBottom: 4, letterSpacing: "0.06em" }}>
        {label}
      </div>
      <pre
        style={{
          margin: 0,
          padding: 10,
          background: "#0d1117",
          border: "1px solid #30363d",
          borderRadius: 6,
          fontSize: 10,
          lineHeight: 1.45,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
          color: empty ? "#484f58" : "#c9d1d9",
          maxHeight: 220,
          overflow: "auto",
        }}
      >
        {empty ? "(empty)" : text}
      </pre>
    </div>
  );
}

export function ReportModal({ onClose }: Props) {
  const sessionId = useGhostStore((s) => s.sessionId);
  const lastReport = useGhostStore((s) => s.lastReport);
  const [report, setReport] = useState<ReportPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const BASE = (import.meta.env.VITE_REPORT_URL ?? "http://localhost:8005") as string;

  // Prefer live sessionId; fall back to lastReport.session_id (survives STOP).
  const effectiveSid = sessionId ?? lastReport?.session_id ?? null;

  useEffect(() => {
    if (!effectiveSid) {
      setLoading(false);
      setReport(null);
      setLoadError(null);
      return;
    }
    setLoading(true);
    setLoadError(null);
    fetch(`${BASE}/v1/reports/${effectiveSid}`)
      .then(async (r) => {
        if (!r.ok) {
          const t = await r.text().catch(() => "");
          throw new Error(r.status === 404 ? "Session not found in report DB." : `${r.status} ${t || r.statusText}`);
        }
        return r.json() as Promise<ReportPayload>;
      })
      .then(async (d) => {
        let next = d;
        try {
          const arena = await arenaApi.getBattle(effectiveSid);
          next = augmentComposerStatisticsFromArena(d, arena);
        } catch {
          /* arena gone / offline */
        }
        setReport(next);
        setLoading(false);
      })
      .catch((e: Error) => {
        setReport(null);
        setLoadError(e.message || "Failed to load report");
        setLoading(false);
      });
  }, [effectiveSid, BASE]);

  const rounds = report?.rounds ?? [];
  const stats = report?.statistics;

  return (
    <div style={{
      position: "fixed", top: 40, left: "50%", transform: "translateX(-50%)",
      width: 720, maxHeight: "85vh", background: "#0d1117",
      border: "2px solid #f5a623", borderRadius: 8, padding: 20,
      zIndex: 9999, fontFamily: "monospace", color: "#cdd9e5", overflowY: "auto",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16, alignItems: "flex-start", gap: 12 }}>
        <div>
          <div style={{ color: "#f5a623", fontWeight: "bold", fontSize: 14 }}>BATTLE REPORT</div>
          {sessionId && (
            <div style={{ fontSize: 9, color: "#8b949e", marginTop: 4, wordBreak: "break-all" }}>
              {sessionId}
            </div>
          )}
          {report?.mode != null && (
            <div style={{ fontSize: 9, color: "#6e7681", marginTop: 2 }}>
              mode: {String(report.mode)} · status: {String(report.status ?? "—")} ·{" "}
              Red: {report.red_service_id ?? "—"} vs Blue: {report.blue_service_id ?? "—"}
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
          {report && (report.session_id || sessionId) && (
            <a
              href={`${BASE}/v1/reports/${report.session_id ?? sessionId}/pdf`}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#22c55e", fontSize: 12, textDecoration: "none", border: "1px solid #22c55e", padding: "3px 8px", borderRadius: 4 }}
            >
              Open print / PDF
            </a>
          )}
          <button onClick={onClose} style={{ background: "none", border: "none", color: "#999", cursor: "pointer", fontSize: 18 }}>✕</button>
        </div>
      </div>

      {loading && <div style={{ color: "#8b949e" }}>Loading report…</div>}
      {!loading && loadError && (
        <div style={{ color: "#ef4444", fontSize: 12, marginBottom: 12 }}>
          {loadError}
          <div style={{ color: "#8b949e", fontSize: 10, marginTop: 8 }}>
            Ensure VITE_REPORT_URL points at report-composer and PostgreSQL has execution_traces rows for this session.
          </div>
        </div>
      )}
      {!loading && !loadError && report && stats && (
        <>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(3, 1fr)", gap: 12, marginBottom: 16 }}>
            {[
              { label: "ROUNDS", value: stats.total_rounds },
              { label: "RED WINS", value: stats.red_wins, color: "#ef4444" },
              { label: "BLUE WINS", value: stats.blue_wins, color: "#3b82f6" },
              { label: "ASR", value: `${(stats.attack_success_rate * 100).toFixed(1)}%`, color: "#f97316" },
              { label: "DR", value: `${(stats.defense_rate * 100).toFixed(1)}%`, color: "#22c55e" },
              { label: "AVG HARM", value: stats.avg_harmfulness_score.toFixed(3) },
            ].map((s) => (
              <div key={s.label} style={{ background: "#161b22", padding: 10, borderRadius: 4, textAlign: "center" }}>
                <div style={{ fontSize: 20, fontWeight: "bold", color: (s as { color?: string }).color ?? "#fff" }}>{s.value}</div>
                <div style={{ fontSize: 10, color: "#8b949e" }}>{s.label}</div>
              </div>
            ))}
          </div>
          <div style={{ fontSize: 11, color: "#8b949e", marginBottom: 8 }}>Full per-round traces (Judge, Target text, attack/defense)</div>
          {rounds.length === 0 && (
            <div style={{ color: "#ffa657", fontSize: 11 }}>No execution_traces for this session yet (round statistics will show as zero).</div>
          )}
          {rounds.map((r) => {
            const delivered = r.target_delivered ?? r.target_response ?? "";
            const gate = r.output_gate ?? {};
            return (
              <details
                key={r.round}
                style={{
                  background: "#161b22",
                  padding: "8px 10px",
                  marginBottom: 8,
                  borderRadius: 4,
                  fontSize: 10,
                  border: "1px solid #30363d",
                }}
              >
                <summary style={{ cursor: "pointer", color: "#a78bfa", listStyle: "none" } as React.CSSProperties}>
              <span style={{ color: "#a78bfa" }}>R{r.round}</span>
                  {" · "}
                  <span style={{ color: "#f97316" }}>{r.attack?.type ?? "?"}</span>
                  {" · "}
                  <span style={{ color: r.defense?.decision === "block" ? "#22c55e" : "#ef4444" }}>
                    {(r.defense?.decision ?? "?").toUpperCase()}
                  </span>
                  {" · "}
                  <span style={{ color: r.red_success ? "#ef4444" : "#22c55e" }}>
                    {r.red_success ? "RED WIN" : "BLUE WIN"}
                  </span>
                  {" · harm "}
                  {(r.harmfulness_score ?? 0).toFixed(4)}
                </summary>
                <PreBlock label="RED · attack payload" text={r.attack?.payload ?? ""} />
                <PreBlock
                  label="BLUE · input gate"
                  text={`${r.defense?.decision ?? ""} (conf=${r.defense?.confidence ?? "?"})${r.defense?.reason ? `\n${r.defense.reason}` : ""}`}
                />
                <PreBlock label="Prompt → Target AI (final_payload)" text={r.final_prompt ?? ""} />
                <PreBlock label="Target AI · raw completion" text={r.raw_target_response ?? ""} />
                <PreBlock
                  label={`After blue output filter (delivered)${gate.was_modified ? " · MODIFIED" : " · passthrough"}`}
                  text={delivered}
                />
                {gate.was_modified && gate.modification_reason && (
                  <PreBlock label="Output modification reason" text={String(gate.modification_reason)} />
                )}
                {(r.target_tool_calls?.length ?? 0) > 0 && (
                  <PreBlock
                    label="Target actions this round"
                    text={(r.target_tool_calls ?? []).map(describeAction).join("\n")}
                  />
                )}
                <PreBlock label="Judge reasoning" text={r.judge_reasoning ?? ""} />
              </details>
            );
          })}
        </>
      )}
    </div>
  );
}
