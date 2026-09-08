import React from 'react'
import ReactDOM from 'react-dom/client'
import './index.css'

void import('./App').then(({ default: App }) => {
  ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
}).catch(() => {
  const root = document.getElementById('root')!
  root.setAttribute('role', 'alert')
  root.textContent = 'PlanGo 无法加载已有会话。请保留本地数据并检查迁移冲突或存储空间后重新启动。'
})
