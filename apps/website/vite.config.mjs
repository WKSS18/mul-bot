import {defineConfig} from 'vite';
import react from '@vitejs/plugin-react';
import {fileURLToPath} from 'node:url';
export default defineConfig({plugins:[react()], publicDir:fileURLToPath(new URL('../../packages/workbench/public',import.meta.url)),build:{outDir:'../../dist/website',assetsDir:'website-assets',emptyOutDir:true},server:{fs:{allow:['../..']}}});
