import { useEffect, useRef } from 'react'
import { CANVAS } from '@/lib/sceneConfig'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
let gameInstance: any = null

export default function PhaserGame() {
  const containerRef = useRef<HTMLDivElement>(null)

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
