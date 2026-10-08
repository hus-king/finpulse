import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import { AuthProvider } from './AuthContext';
import { ThemeProvider } from './ThemeContext';
import './styles.css';
import './finance.css';
import './briefing.css';
import './theme.css';
import './industry.css';
import './chart-controls.css';

ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><ThemeProvider><AuthProvider><App /></AuthProvider></ThemeProvider></React.StrictMode>);
