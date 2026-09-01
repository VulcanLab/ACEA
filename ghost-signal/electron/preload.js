const { contextBridge } = require('electron')

contextBridge.exposeInMainWorld('ghostSignal', {
  version: '0.1.0',
})
