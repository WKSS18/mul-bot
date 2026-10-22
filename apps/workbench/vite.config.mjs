import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
export default defineConfig({plugins:[react()],publicDir:fileURLToPath(new URL('../../packages/workbench/public',import.meta.url)),base:'/',build:{outDir:'../../dist/workbench',assetsDir:'workbench-assets',emptyOutDir:true},server:{fs:{allow:['../..']},proxy:{'/api':{target:'http://127.0.0.1:8000',changeOrigin:true,ws:true}}}});
