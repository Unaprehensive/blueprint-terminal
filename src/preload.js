const { contextBridge, ipcRenderer } = require('electron');

// Безопасный API для рендер-процесса
contextBridge.exposeInMainWorld('electronAPI', {
  // Методы для setup страницы
  recheckPlatforms: () => ipcRenderer.invoke('recheckPlatforms'),
  
  // Методы для инсталлятора
  selectInstallDirectory: () => ipcRenderer.invoke('selectInstallDirectory'),
  showRiskWarning: () => ipcRenderer.invoke('showRiskWarning'),
  saveUserConfig: (config) => ipcRenderer.invoke('saveUserConfig', config),
  
  // Системные методы
  getPlatform: () => process.platform,
  getVersion: () => process.env.npm_package_version || '2.1.0',
  
  // Методы для работы с окном
  closeWindow: () => ipcRenderer.invoke('closeWindow'),
  minimizeWindow: () => ipcRenderer.invoke('minimizeWindow'),
  maximizeWindow: () => ipcRenderer.invoke('maximizeWindow'),
  
  // События
  onPlatformCheckResult: (callback) => {
    ipcRenderer.on('platform-check-result', callback);
  },
  
  onBackendStatus: (callback) => {
    ipcRenderer.on('backend-status', callback);
  },
  
  removePlatformCheckListener: () => {
    ipcRenderer.removeAllListeners('platform-check-result');
  },
  
  removeBackendStatusListener: () => {
    ipcRenderer.removeAllListeners('backend-status');
  }
});

// Предотвращаем доступ к Node.js API
delete window.require;
delete window.exports;
delete window.module;

// Логирование ошибок
window.addEventListener('error', (error) => {
  console.error('Renderer error:', error);
});

console.log('Preload script loaded successfully');