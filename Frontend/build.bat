@echo off
cd /d "%~dp0"
if not exist dist mkdir dist
call npm install
call npx esbuild src/main.ts --bundle --outfile=dist/main.js --platform=browser --format=iife --minify
echo.
echo Build complete ^— open index.html in browser.
pause
