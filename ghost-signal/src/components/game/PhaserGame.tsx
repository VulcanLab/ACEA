import { useEffect, useRef } from 'react'
import { CANVAS } from '@/lib/sceneConfig'
import { useGhostStore } from '@/lib/store'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let gameInstance: any = null

export default function PhaserGame() {
  const containerRef = useRef<HTMLDivElement>(null)
  const modalType = useGhostStore(s => s.modal.type)

  // While a dialog is open it covers the scene, so the scene must stop reacting
  // to pointer input. Without this, a click meant for a control inside the dialog
  // is ALSO hit-tested against whatever sits beneath it on the canvas, which can
  // open a different dialog and make the one the user was using appear to vanish.
  useEffect(() => {
    const input = gameInstance?.input
    if (!input) return
    input.enabled = modalType === null
  }, [modalType])

  useEffect(() => {
    if (gameInstance || !containerRef.current) return
    let destroyed = false

    ;(async () => {
      const [{ default: Phaser }, { BootScene }, { OpsScene }] = await Promise.all([
        import('phaser'),
        import('./scenes/BootScene'),
        import('./scenes/OpsScene'),
      ])

      if (destroyed || !containerRef.current) return

      gameInstance = new Phaser.Game({
        type: Phaser.AUTO,
        width: CANVAS.width,
        height: CANVAS.height,
        backgroundColor: '#0a0a0f',
        parent: containerRef.current,
        pixelArt: true,
        antialias: false,
        scene: [BootScene, OpsScene],
        render: { pixelArt: true, antialias: false, roundPixels: true },
      })

      // Respect a dialog that is already open at the moment the scene boots.
      if (gameInstance?.input) {
        gameInstance.input.enabled = useGhostStore.getState().modal.type === null
      }
    })()

    return () => {
      destroyed = true
      gameInstance?.destroy(true)
      gameInstance = null
    }
  }, [])

  return (
    <div
      ref={containerRef}
      style={{ width: CANVAS.width, height: CANVAS.height, imageRendering: 'pixelated' }}
    />
  )
}
