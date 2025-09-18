const { app, BrowserWindow, ipcMain, dialog, shell, Menu } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const fs = require('fs');
const os = require('os');

// Определяем, запущен ли в режиме разработки
const isDev = process.argv.includes('--dev');

// Глобальные переменные
let mainWindow;
let pythonProcess;
let isQuitting = false;

// Путь к Python backend
const getPythonBackendPath = () => {
  if (isDev) {
    return path.join(__dirname, '..', 'python-backend');
  } else {
    return path.join(process.resourcesPath, 'python-backend');
  }
};

// Создание главного окна
function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1200,
    minHeight: 700,
    icon: path.join(__dirname, '..', 'assets', 'icon.ico'),
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      enableRemoteModule: false,
      preload: path.join(__dirname, 'preload.js')
    },
    titleBarStyle: 'default',
    show: false, // Не показываем окно сразу
    frame: true
  });

  // Устанавливаем заголовок окна
  mainWindow.setTitle('Blueprint Trading Terminal v2.1');

  // Убираем меню полностью
  Menu.setApplicationMenu(null);

  // Проверяем первый запуск
  checkFirstRun();

  // Показываем окно после загрузки
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
    
    if (isDev) {
      mainWindow.webContents.openDevTools();
    }
  });

  // Обработка закрытия окна
  mainWindow.on('close', (event) => {
    if (!isQuitting) {
      event.preventDefault();
      gracefulShutdown();
    }
  });

  // Предотвращаем навигацию к внешним URL
  mainWindow.webContents.on('will-navigate', (event, navigationUrl) => {
    const parsedUrl = new URL(navigationUrl);
    if (parsedUrl.origin !== 'http://127.0.0.1:5000') {
      event.preventDefault();
      shell.openExternal(navigationUrl);
    }
  });
}

// Проверка первого запуска
function checkFirstRun() {
  const configPath = path.join(os.homedir(), '.blueprint-terminal', 'config.json');
  
  try {
    if (!fs.existsSync(configPath)) {
      // Первый запуск - показываем setup
      loadSetupPage();
    } else {
      // Проверяем MT5
      checkMT5Installation();
    }
  } catch (error) {
    console.error('Error checking first run:', error);
    loadSetupPage();
  }
}

// Загрузка страницы setup
function loadSetupPage() {
  const setupPath = path.join(__dirname, 'pages', 'setup.html');
  mainWindow.loadFile(setupPath);
}

// Проверка установки MT5
function checkMT5Installation() {
  const isWindows = process.platform === 'win32';
  const isMac = process.platform === 'darwin';
  
  let mt5Found = false;
  
  if (isWindows) {
    // Проверяем стандартные пути установки MT5 на Windows
    const possiblePaths = [
      'C:\\Program Files\\MetaTrader 5\\terminal64.exe',
      'C:\\Program Files (x86)\\MetaTrader 5\\terminal64.exe',
      path.join(os.homedir(), 'AppData', 'Local', 'Programs', 'MetaTrader 5', 'terminal64.exe')
    ];
    
    mt5Found = possiblePaths.some(path => fs.existsSync(path));
    
  } else if (isMac) {
    // Проверяем установку на macOS
    mt5Found = fs.existsSync('/Applications/MetaTrader 5.app');
  }
  
  if (mt5Found) {
    startPythonBackend();
  } else {
    loadSetupPage();
  }
}

// Запуск Python backend
function startPythonBackend() {
  const backendPath = getPythonBackendPath();
  const pythonScript = path.join(backendPath, 'server.py');
  
  console.log('Starting Python backend from:', pythonScript);
  
  try {
    pythonProcess = spawn('python', [pythonScript], {
      cwd: backendPath,
      stdio: ['pipe', 'pipe', 'pipe']
    });
    
    pythonProcess.stdout.on('data', (data) => {
      console.log('Python stdout:', data.toString());
    });
    
    pythonProcess.stderr.on('data', (data) => {
      console.error('Python stderr:', data.toString());
    });
    
    pythonProcess.on('close', (code) => {
      console.log('Python process exited with code:', code);
    });
    
    // Ждем запуска сервера, затем загружаем интерфейс
    setTimeout(() => {
      mainWindow.loadURL('http://127.0.0.1:5000');
    }, 3000);
    
  } catch (error) {
    console.error('Failed to start Python backend:', error);
    
    dialog.showErrorBox(
      'Backend Error', 
      'Failed to start the trading backend. Please ensure Python is installed.'
    );
  }
}

// Graceful shutdown
function gracefulShutdown() {
  isQuitting = true;
  
  if (pythonProcess) {
    console.log('Stopping Python backend...');
    pythonProcess.kill('SIGTERM');
    
    setTimeout(() => {
      if (pythonProcess) {
        pythonProcess.kill('SIGKILL');
      }
    }, 5000);
  }
  
  setTimeout(() => {
    app.quit();
  }, 1000);
}

// IPC обработчики
ipcMain.handle('recheckPlatforms', async () => {
  checkMT5Installation();
});

ipcMain.handle('selectInstallDirectory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory'],
    title: 'Select Installation Directory'
  });
  
  return result.filePaths[0];
});

ipcMain.handle('showRiskWarning', async () => {
  const result = await dialog.showMessageBox(mainWindow, {
    type: 'warning',
    title: 'Risk Warning',
    message: 'Important Risk Disclosure',
    detail: `Trading CFDs and Forex involves significant risk of loss. 
    
• Up to 80% of retail investor accounts lose money when trading CFDs
• You should consider whether you understand how CFDs work
• You should consider whether you can afford to take the high risk of losing your money
• Past performance is not indicative of future results
• This software is for educational purposes and comes with no guarantees

By continuing, you acknowledge that you understand these risks and accept full responsibility for your trading decisions.`,
    buttons: ['I Accept the Risks', 'Cancel'],
    defaultId: 0,
    cancelId: 1,
    noLink: true
  });
  
  return result.response === 0;
});

ipcMain.handle('saveUserConfig', async (event, config) => {
  const configDir = path.join(os.homedir(), '.blueprint-terminal');
  const configPath = path.join(configDir, 'config.json');
  
  try {
    if (!fs.existsSync(configDir)) {
      fs.mkdirSync(configDir, { recursive: true });
    }
    
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
    return true;
  } catch (error) {
    console.error('Error saving config:', error);
    return false;
  }
});

// События приложения
app.whenReady().then(() => {
  createMainWindow();
  
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      createMainWindow();
    }
  });
});

app.on('window-all-closed', () => {
  gracefulShutdown();
});

app.on('before-quit', () => {
  isQuitting = true;
});

// Обработка исключений
process.on('uncaughtException', (error) => {
  console.error('Uncaught Exception:', error);
});

process.on('unhandledRejection', (reason, promise) => {
  console.error('Unhandled Rejection at:', promise, 'reason:', reason);
});