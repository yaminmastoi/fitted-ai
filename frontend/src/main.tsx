import { StrictMode, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import './styles.css';
import { api, analytics } from './lib/api';
import type { Bootstrap } from './lib/types';
import AppShell from './components/AppShell';
import Landing from './pages/Landing';import TryOn from './pages/TryOn';import Pricing from './pages/Pricing';import Login from './pages/Login';import Signup from './pages/Signup';import Dashboard from './pages/Dashboard';import Result from './pages/Result';import Admin from './pages/Admin';
function App(){const [boot,setBoot]=useState<Bootstrap|null>(null);const refresh=()=>api<Bootstrap>('/api/bootstrap').then(setBoot).catch(()=>setBoot(null));useEffect(()=>{refresh();analytics('landing_view')},[]);return <AppShell bootstrap={boot}><Routes><Route path="/" element={<Landing bootstrap={boot}/>}/><Route path="/try-on" element={<TryOn bootstrap={boot} refresh={refresh}/>}/><Route path="/pricing" element={<Pricing bootstrap={boot}/>}/><Route path="/login" element={<Login refresh={refresh}/>}/><Route path="/signup" element={<Signup refresh={refresh}/>}/><Route path="/dashboard" element={<Dashboard bootstrap={boot} refresh={refresh}/>}/><Route path="/result/:id" element={<Result bootstrap={boot}/>}/><Route path="/admin" element={<Admin bootstrap={boot}/>}/><Route path="*" element={<Navigate to="/" replace/>}/></Routes></AppShell>}
createRoot(document.getElementById('root')!).render(<StrictMode><BrowserRouter><App/></BrowserRouter></StrictMode>);
