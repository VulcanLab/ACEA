/**
 * Mock event bus has been removed.
 * Agents only animate in response to real backend WebSocket events.
 * These stubs exist only so old import sites compile without changes.
 */
export function startMockBusIfBackendOnline(): void { /* no-op */ }
export function stopMockBus(): void { /* no-op */ }
