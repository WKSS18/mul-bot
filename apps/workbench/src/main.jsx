import React from 'react';
import {createRoot} from 'react-dom/client';
import {ConnectedApp} from './ConnectedApp.jsx';
import '../../../packages/workbench/src/styles.css';
import './connected.css';
createRoot(document.getElementById('root')).render(<ConnectedApp/>);
