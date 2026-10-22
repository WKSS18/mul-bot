import React from 'react';
import {createRoot} from 'react-dom/client';
import {App} from '../../../packages/workbench/src/App.jsx';
import '../../../packages/workbench/src/styles.css';
createRoot(document.getElementById('root')).render(<App mode="web"/>);
