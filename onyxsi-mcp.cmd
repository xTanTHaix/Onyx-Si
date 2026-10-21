@echo off
rem Zero-install MCP bridge for OnyxSi (Windows).
rem Usage: onyxsi-mcp.cmd --workspace C:\path\to\target\repo
setlocal
python "%~dp0onyxsi-mcp.py" %*
exit /b %ERRORLEVEL%
