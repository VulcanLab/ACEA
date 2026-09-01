import React, { useState, useEffect, useRef } from 'react'
import { useGhostStore } from './lib/store'
import { arenaWsClient } from './lib/arenaWsClient'
import PhaserGame from './components/game/PhaserGame'
import TopBar from './components/hud/TopBar'
import AgentPanel from './components/hud/AgentPanel'
import BattleSidebar from './components/hud/BattleSidebar'
import ActivityLog from './components/hud/ActivityLog'
import TerminalModal from './components/hud/modals/TerminalModal'
import PrinterModal from './components/hud/modals/PrinterModal'
import CoreModal from './components/hud/modals/CoreModal'
import TargetScreenModal from './components/hud/modals/TargetScreenModal'
import AdapterConfigModal from './components/hud/modals/AdapterConfigModal'
import JudgeVerdictModal from './components/hud/modals/JudgeVerdictModal'
import ModelHealthModal from './components/hud/modals/ModelHealthModal'
import ReadinessModal from './components/hud/modals/ReadinessModal'
import PhaseBanner from './components/hud/PhaseBanner'
import ModelFaultBanner from './components/hud/ModelFaultBanner'
import { ExecutionModal } from './components/modals/ExecutionModal'
import { ReportModal } from './components/modals/ReportModal'

const GAME_W = 1280
const GAME_H = 720

export default function App() {
  const { modal } = useGhostStore()
  const stageRef = useRef<HTMLDivElement>(null)

  const [showRedTerminal, setShowRedTerminal] = useState(false)
  const [showBlueTerminal, setShowBlueTerminal] = useState(false)
  const [showReport, setShowReport] = useState(false)

  // Fit the fixed 1280×720 layout into the viewport (mobile / small windows /
  // iOS Safari address bar via visualViewport).
  useEffect(() => {
    const el = stageRef.current
    if (!el) return

    const applyLayout = () => {
      const vv = window.visualViewport
      const vw = Math.max(1, vv?.width ?? window.innerWidth)
      const vh = Math.max(1, vv?.height ?? window.innerHeight)
      const ox = vv?.offsetLeft ?? 0
      const oy = vv?.offsetTop ?? 0
      const scale = Math.max(0.06, Math.min(vw / GAME_W, vh / GAME_H))
      const gx = ox + Math.max(0, (vw - GAME_W * scale) / 2)
      const gy = oy + Math.max(0, (vh - GAME_H * scale) / 2)
      el.style.transform = `translate(${gx}px, ${gy}px) scale(${scale})`
    }

    applyLayout()
    window.addEventListener('resize', applyLayout)
    const vv = window.visualViewport
    vv?.addEventListener('resize', applyLayout)
    vv?.addEventListener('scroll', applyLayout)
    return () => {
      window.removeEventListener('resize', applyLayout)
      vv?.removeEventListener('resize', applyLayout)
      vv?.removeEventListener('scroll', applyLayout)
    }
  }, [])

  // Connect to the global WS stream (persistent — active even outside battles)
  useEffect(() => {
    arenaWsClient.connectGlobal()
    return () => arenaWsClient.disconnectGlobal()
  }, [])

  useEffect(() => {
    const openRed = () => { setShowRedTerminal(true) }
    const openBlue = () => { setShowBlueTerminal(true) }
    const openRep = () => { setShowReport(true) }
    ;(window as Window & {
      __openRedTerminal?: () => void
      __openBlueTerminal?: () => void
      __openReport?: () => void
    }).__openRedTerminal = openRed
    ;(window as Window & { __openBlueTerminal?: () => void }).__openBlueTerminal = openBlue
    ;(window as Window & { __openReport?: () => void }).__openReport = openRep
  }, [])

  return (
    <div
      style={{
        position: 'fixed',
        inset: 0,
        background: '#000',
        overflow: 'hidden',
      }}
    >
      <div
        ref={stageRef}
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          width: GAME_W,
          height: GAME_H,
          transformOrigin: 'top left',
          background: '#0a0a0f',
          overflow: 'hidden',
        }}
      >
        <TopBar />

        {/* Phaser under HUD stacking */}
        <div style={{ position: 'absolute', inset: 0, zIndex: 0 }}>
          <PhaserGame />
        </div>

        <AgentPanel side="red" />
        <AgentPanel side="blue" />
        <BattleSidebar />
        <PhaseBanner />
        {/* Stays until dismissed: a model failure that retrying cannot clear. */}
        <ModelFaultBanner />
        <ActivityLog />

        {modal.type === 'terminal_red'  && <TerminalModal side="red" />}
        {modal.type === 'terminal_blue' && <TerminalModal side="blue" />}
        {modal.type === 'printer'       && <PrinterModal />}
        {modal.type === 'core'          && <CoreModal />}
        {modal.type === 'target_screen' && <TargetScreenModal />}
        {modal.type === 'adapter_config' && <AdapterConfigModal />}
        {modal.type === 'judge_verdict'  && <JudgeVerdictModal />}
        {modal.type === 'model_health'   && <ModelHealthModal />}
        {modal.type === 'battle_readiness' && <ReadinessModal />}

        {showRedTerminal && <ExecutionModal team="red" onClose={() => setShowRedTerminal(false)} />}
        {showBlueTerminal && <ExecutionModal team="blue" onClose={() => setShowBlueTerminal(false)} />}
        {showReport && <ReportModal onClose={() => setShowReport(false)} />}
      </div>
    </div>
  )
}
