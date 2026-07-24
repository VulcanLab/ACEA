import React from "react";
import { useGhostStore } from "../../lib/store";

interface Props {
  team: "red" | "blue";
  onClose: () => void;
}

export function ExecutionModal({ team, onClose }: Props) {
  const liveAttackLog = useGhostStore((s) => s.liveAttackLog);
  const liveDefenseLog = useGhostStore((s) => s.liveDefenseLog);
  const lastAttack = useGhostStore((s) => s.lastAttack);
  const lastDefense = useGhostStore((s) => s.lastDefense);
  const lastVerdict = useGhostStore((s) => s.lastVerdict);

  const log = team === "red" ? liveAttackLog : liveDefenseLog;
  const accent = team === "red" ? "#e74c3c" : "#2980b9";

  return (
    <div style={{
      position: "fixed", top: "10%", left: "50%", transform: "translateX(-50%)",
      width: 600, background: "#0d1117", border: `2px solid ${accent}`,
      borderRadius: 8, padding: 20, zIndex: 9999, fontFamily: "monospace", color: "#cdd9e5",
    }}>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 12 }}>
        <span style={{ color: accent, fontWeight: "bold", fontSize: 14 }}>
          {team.toUpperCase()} TEAM — EXECUTION LOG
        </span>
        <button onClick={onClose} style={{ background: "none", border: "none", color: "#999", cursor: "pointer", fontSize: 18 }}>✕</button>
      </div>

      {team === "red" && lastAttack && (
        <div style={{ marginBottom: 12, padding: 10, background: "#161b22", borderRadius: 4 }}>
          <div style={{ color: "#f97316", fontSize: 11, marginBottom: 4 }}>LAST ATTACK</div>
          <div style={{ fontSize: 11 }}><b>Type:</b> {lastAttack.type} | <b>Confidence:</b> {(lastAttack.confidence * 100).toFixed(0)}%</div>
          <div style={{ fontSize: 10, color: "#8b949e", marginTop: 4, wordBreak: "break-all" }}>{lastAttack.payload}</div>
        </div>
      )}

      {team === "blue" && lastDefense && (
        <div style={{ marginBottom: 12, padding: 10, background: "#161b22", borderRadius: 4 }}>
          <div style={{ color: "#3b82f6", fontSize: 11, marginBottom: 4 }}>LAST DEFENSE</div>
          <div style={{ fontSize: 11 }}>
            <b>Decision:</b> <span style={{ color: lastDefense.decision === "block" ? "#22c55e" : "#ef4444" }}>{lastDefense.decision.toUpperCase()}</span>
            {" "}| <b>Confidence:</b> {(lastDefense.confidence * 100).toFixed(0)}%
          </div>
          <div style={{ fontSize: 10, color: "#8b949e", marginTop: 4 }}>{lastDefense.reason}</div>
        </div>
      )}

      {lastVerdict && (
        <div style={{ marginBottom: 12, padding: 10, background: "#161b22", borderRadius: 4 }}>
          <div style={{ color: "#a78bfa", fontSize: 11, marginBottom: 4 }}>LAST JUDGE VERDICT</div>
          <div style={{ fontSize: 11 }}>
            <b>Verdict:</b> <span style={{ color: lastVerdict.verdict === "success" ? "#ef4444" : "#22c55e" }}>
              {lastVerdict.verdict === "success" ? "RED WIN" : "BLUE WIN"}
            </span>
            {" "}| <b>Harm:</b> {lastVerdict.harmScore.toFixed(2)}
          </div>
          <div style={{ fontSize: 10, color: "#8b949e", marginTop: 4 }}>{lastVerdict.reason}</div>
        </div>
      )}

      <div style={{ background: "#0a0f16", borderRadius: 4, padding: 10, height: 200, overflowY: "auto" }}>
        <div style={{ fontSize: 10, color: "#22c55e", marginBottom: 6 }}>$ LIVE LOG</div>
        {log.length === 0 && <div style={{ color: "#555", fontSize: 10 }}>Waiting for battle events...</div>}
        {[...log].reverse().map((line, i) => (
          <div key={i} style={{ fontSize: 10, color: "#8b949e", marginBottom: 2 }}>{line}</div>
        ))}
      </div>
    </div>
  );
}
