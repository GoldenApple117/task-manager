@echo off
chcp 65001 >nul
echo ========================================
echo   轻量任务管理系统 - 一键启动
echo ========================================
echo.
echo [1/3] 启动 Flask 服务器...
start "TaskServer" C:\Users\20210817\.workbuddy\binaries\python\envs\feishu\Scripts\python.exe app.py
timeout /t 3 /nobreak >nul

echo [2/3] 启动 SSH 公网隧道...
start "SSHTunnel" ssh -o StrictHostKeyChecking=no -o ServerAliveInterval=60 -R 80:localhost:5090 nokey@localhost.run

timeout /t 8 /nobreak >nul
echo.
echo [3/3] 启动飞书 Bot...
start "FeishuBot" cmd /c "cd /d D:\workburry_workspace\feishu_task_system && set PYTHONIOENCODING=utf-8 && C:\Users\20210817\.workbuddy\binaries\python\envs\feishu\Scripts\python.exe -u bot.py --chat-id oc_aed22a624ff36336cf36309a62e99fea --timeout 1440"

echo.
echo ========================================
echo   全部启动完成!
echo   本地地址: http://localhost:5090
echo   公网地址: 查看 SSH Tunnel 窗口
echo   关闭本窗口不影响服务运行
echo ========================================
pause
