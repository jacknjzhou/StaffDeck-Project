/**
 * 前端应用入口模块。
 *
 * 将 App 挂载到 #root 节点，并在最外层注入 I18nProvider 与全局样式；
 * StrictMode 仅在开发环境下额外触发一次副作用检查。
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { I18nProvider } from './i18n';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <I18nProvider>
      <App />
    </I18nProvider>
  </React.StrictMode>,
);
