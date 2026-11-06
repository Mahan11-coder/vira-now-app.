import React from 'react';
import { createRoot } from 'react-dom/client';
import '@fontsource/vazirmatn/400.css';
import '@fontsource/vazirmatn/700.css';
import '@fontsource/vazirmatn/800.css';
import App from './App';
import './styles.css';
import { assertApiConfigured } from './env';

try {
  assertApiConfigured();
} catch (e) {
  console.error(e);
}

createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
