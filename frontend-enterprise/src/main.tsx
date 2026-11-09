/**
 * 前端应用入口模块。
 *
 * 将 App 挂载到 #root 节点，并在最外层注入 QueryClientProvider、I18nProvider 与全局样式；
 * StrictMode 仅在开发环境下额外触发一次副作用检查。
 */
import { QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { queryClient } from './api/queryClient';
import { I18nProvider } from './i18n';
import './styles.css';

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <App />
      </I18nProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
