import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import '@fontsource-variable/jetbrains-mono'
import '@fontsource-variable/space-grotesk'
import '@fontsource/instrument-serif/400-italic.css'
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles/index.css'
import App from './App.tsx'
import { BASE_URL } from './shared/base'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* 경로 접두사(MIA_BASE_PATH) 아래에서 열 때 라우터 경로는 그 뒤부터 */}
    <BrowserRouter basename={BASE_URL}>
      <App />
    </BrowserRouter>
  </StrictMode>,
)
